/*
 * table_ui.js
 * ------------------------------------------------------------
 * تعاملات سبک و ویرایش دوضرب فیلدها متصل به موتور اسکرول مجازی
 */
(function (window, document) {
    'use strict';

    const ENABLE_BASIC_CELL_EDITING = true;
    const KIND = (window.FT_TOOL_SAVE && window.FT_TOOL_SAVE.kind) || '';
    // size / qty / unit are reference fields in BOTH TO and PI — never inline-edited.
    // (TO used to allow clicking them; that is intentionally locked now.)
    const BASIC_EDITABLE_COLUMNS = new Set();
    const ROW_LIGHT_DEBOUNCE_MS = 220;
    const ROW_FULL_DEBOUNCE_MS = 700;
    // PI columns that have permanent always-on inputs — handled by pi_columns.js.
    const PI_ALWAYS_ON = ['UNIT PRICE', 'BRAND', 'TIME'];

    const rowState = new WeakMap();

    function getCell(row, columnName) {
        return row?.querySelector(`td[data-col-name="${CSS.escape(columnName)}"]`) || null;
    }

    function cellGroupOrTypeValue(row, columnName, datasetKey) {
        // When Group/Type is empty the cell holds a <select>. textContent then
        // concatenates EVERY option label (e.g. "-- Select group --pipefitting…")
        // and that garbage was sent as group_key, poisoning FTCO DISCRIPTION.
        const cell = getCell(row, columnName);
        if (!cell) return row?.dataset?.[datasetKey] || '';
        const sel = cell.querySelector('select');
        if (sel) return String(sel.value || '').trim();
        return String(cell.textContent || '').trim();
    }

    function getRowState(row) {
        let state = rowState.get(row);
        if (!state) {
            state = { lightTimer: null, fullTimer: null, lastSubmittedSignatureByMode: {} };
            rowState.set(row, state);
        }
        return state;
    }

    function getRowValues(row) {
        return {
            originalText: getCell(row, 'description')?.textContent.trim() || '',
            group: cellGroupOrTypeValue(row, 'Group', 'group'),
            type: cellGroupOrTypeValue(row, 'Type', 'type'),
            remark: getCell(row, 'ریمارک')?.querySelector('textarea')?.value.trim() || '',
            revision: getCell(row, 'اصلاحیه')?.querySelector('textarea')?.value.trim() || ''
        };
    }

    function processRow(row, values, assignMode = 'full') {
        if (!window.RowProcessor || !window.RowProcessor.sendToProcessor) {
            console.error('RowProcessor module is not loaded.');
            return;
        }
        window.RowProcessor.sendToProcessor(
            values.originalText, values.group, values.type, row,
            values.remark, values.revision, assignMode
        );
    }

    function rowSignature(row, values) {
        const editableValues = Array.from(row.querySelectorAll('td[data-editable="1"]'))
            .map((cell) => `${cell.dataset.colName || ''}=${cell.textContent.trim()}`)
            .join('\u0001');
        return `${values.remark}\u0000${values.revision}\u0000${values.group}\u0000${values.type}\u0000${getCell(row, 'size')?.textContent.trim() || ''}\u0000${getCell(row, 'qty')?.textContent.trim() || ''}\u0000${getCell(row, 'unit')?.textContent.trim() || ''}\u0000${editableValues}`;
    }

    function submitRow(row, assignMode = 'full') {
        if (!row) return;
        // Unhandled Proforma remark / brand: coding waits for Reject / Confirm.
        if (row.getAttribute('data-pf-pending') === '1') return;
        if (row.getAttribute('data-brand-pending') === '1') return;
        const state = getRowState(row);
        const values = getRowValues(row);
        const signature = rowSignature(row, values);
        if (signature === state.lastSubmittedSignatureByMode[assignMode]) return;
        state.lastSubmittedSignatureByMode[assignMode] = signature;
        processRow(row, values, assignMode);
    }

    function debouncedSubmit(row) {
        if (!row) return;
        if (row.getAttribute('data-pf-pending') === '1') return;
        if (row.getAttribute('data-brand-pending') === '1') return;
        const state = getRowState(row);
        clearTimeout(state.lightTimer);
        clearTimeout(state.fullTimer);
        state.lightTimer = setTimeout(() => submitRow(row, 'light'), ROW_LIGHT_DEBOUNCE_MS);
        state.fullTimer = setTimeout(() => submitRow(row, 'full'), ROW_FULL_DEBOUNCE_MS);
    }

    function beginBasicCellEdit(cell) {
        if (!ENABLE_BASIC_CELL_EDITING || !cell || cell.dataset.editing === '1') return;

        const columnName = cell.dataset.colName || '';
        if (!BASIC_EDITABLE_COLUMNS.has(columnName) && cell.dataset.editable !== '1') return;
        // Locked cell (e.g. NOT-SUPPLIABLE row's code/brand/time, or a code-less
        // row's price): keep the value visible but refuse to open an editor.
        if (cell.dataset.locked === '1') return;
        // Never destroy permanent PI/TO cell editors.
        if (cell.querySelector('input.pi-unit-input, textarea.pi-text-area, textarea.cell-input, input.cell-input')) return;

        const displayNode = cell.querySelector(':scope > .calc-display-value');
        const marginPanel = cell.querySelector(':scope > .row-margin-panel');
        const previousValue = (cell.dataset.calcVariable
            ? (cell.dataset.calcBase || cell.dataset.calcRaw || displayNode?.textContent || '')
            : cell.textContent
        ).trim();
        const cellWidth = Math.max(56, Math.floor(cell.getBoundingClientRect().width || 80));

        cell.dataset.editing = '1';
        cell.dataset.previousValue = previousValue;

        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'basic-cell-editor';
        input.value = previousValue;
        input.setAttribute('aria-label', columnName);
        input.style.width = `${cellWidth}px`;
        input.style.maxWidth = '100%';

        if (displayNode) {
            displayNode.replaceWith(input);
        } else if (marginPanel) {
            Array.from(cell.childNodes).forEach((node) => {
                if (node !== marginPanel) node.remove();
            });
            cell.insertBefore(input, marginPanel);
        } else {
            cell.textContent = '';
            cell.appendChild(input);
        }

        function restoreDisplayValue(value) {
            if (cell.dataset.calcVariable) {
                const span = document.createElement('span');
                span.className = 'calc-display-value';
                span.textContent = value;
                input.replaceWith(span);
            } else {
                cell.textContent = value;
            }
        }

        function finish(save) {
            if (cell.dataset.editing !== '1') return;
            const newValue = save ? input.value.trim() : cell.dataset.previousValue || '';
            restoreDisplayValue(newValue);
            delete cell.dataset.editing;
            delete cell.dataset.previousValue;

            if (save && newValue !== previousValue) {
                if (!BASIC_EDITABLE_COLUMNS.has(columnName)) {
                    cell.dataset.userEdited = '1';
                }
                if (cell.dataset.calcVariable) {
                    cell.dataset.calcRaw = newValue;
                    cell.dataset.calcBase = newValue;
                    cell.dataset.calcValue = newValue;
                    delete cell.dataset.calcInputBase;
                }
                submitRow(cell.closest('tr'));
            } else if (cell.dataset.calcVariable && window.CalculationControls) {
                window.CalculationControls.refreshRow(cell.closest('tr'));
            }
        }

        input.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') { event.preventDefault(); finish(true); }
            else if (event.key === 'Escape') { event.preventDefault(); finish(false); }
        });
        input.addEventListener('blur', () => finish(true));

        requestAnimationFrame(() => {
            input.focus();
            input.select();
        });
    }

    // Wrap fields that must grow with content (same behavior as REVISION).
        const AUTO_SIZE_COLS = new Set(['ریمارک', 'اصلاحیه', 'BRAND', 'TIME', 'Final Arranged Text']);
    const AUTO_SIZE_TA_SEL =
        'td[data-col-name="ریمارک"] textarea, td[data-col-name="اصلاحیه"] textarea, ' +
        'td[data-col-name="BRAND"] textarea, td[data-col-name="TIME"] textarea, ' +
        'textarea.remark-revision-textarea, textarea.pi-text-area';

    function autoSizeCellTextarea(textarea) {
        if (!textarea) return;
        textarea.style.height = 'auto';
        // +2 covers border-box bottom edge so wrapped text never sits under the box.
        const minH = textarea.classList.contains('pi-text-area') ? 30 : 38;
        textarea.style.height = `${Math.max(minH, textarea.scrollHeight + 2)}px`;
        const tr = textarea.closest ? textarea.closest('tr') : null;
        if (tr && window.VirtualScrollEngine && window.VirtualScrollEngine.remeasureRow) {
            window.VirtualScrollEngine.remeasureRow(tr);
        }
    }
    // Back-compat alias used elsewhere in this file / paste helpers.
    function autoSizeRemarkTextarea(textarea) { autoSizeCellTextarea(textarea); }

    // Apply fixed, content-proportional column widths (NO user resize) and the
    // form-specific column hiding — BOTH done here, deterministically, by writing
    // CSS width rules onto the visible columns' th+td (keyed by data-col-name).
    //
    // We do NOT use a <colgroup>: with table-layout:fixed the browser drops the
    // <col> entries of display:none columns and shifts the rest, which made
    // visible columns inherit the wrong width (REVISION/ALARM/UNIT PRICE went to
    // 0). Width-on-cells has no such shift, so the visible columns always fill
    // 100% and every column that should show, shows.
    window.enableResizableColumns = function(table) {
        if (!table) return;
        const headerCells = Array.from(table.querySelectorAll('thead th'));
        if (!headerCells.length) return;

        // ── Measure content width for SIZE / QTY / UNIT once on first call ──
        if (!window.FT_MEASURED_WEIGHTS) {
            const mw = {};
            const eng = window.VirtualScrollEngine;
            if (eng && eng.getRows) {
                const canvas = document.createElement('canvas');
                const ctx = canvas.getContext('2d');
                const fs = getComputedStyle(table).fontSize || '12px';
                const ff = getComputedStyle(table).fontFamily || 'sans-serif';
                ctx.font = fs + ' ' + ff;
                const MCOLS = ['size', 'qty', 'unit'];
                const maxPx = { size: 44, qty: 30, unit: 30 };
                // unit holds tiny tokens (pcs, m, kg…); cap its measured width so
                // it never steals space from BRAND/TIME.
                const colCapPx = { size: 130, qty: 70, unit: 52 };
                eng.getRows().forEach(function (tr) {
                    MCOLS.forEach(function (col) {
                        const td = tr.querySelector('td[data-col-name="' + col + '"]');
                        if (!td) return;
                        const inp = td.querySelector('input');
                        const text = (inp ? inp.value : td.textContent || '').trim();
                        let w = ctx.measureText(text).width;
                        if (w > colCapPx[col]) w = colCapPx[col];
                        if (w > maxPx[col]) maxPx[col] = w;
                    });
                });
                // Convert px to weight units: 1 unit ≈ 55px content + 20px padding.
                MCOLS.forEach(function (col) {
                    mw[col] = Math.max(0.4, Math.min(2.2, (maxPx[col] + 20) / 55));
                });
                mw['unit'] = Math.max(0.62, Math.min(mw['unit'], 0.72)); // unit: small but readable
            }
            window.FT_MEASURED_WEIGHTS = mw;
        }

        // Content-appropriate proportions. unit is capped small (max ~3 chars
        // like "pcs"); the freed space goes to BRAND / TIME (and, in PI, the
        // proforma remark is narrowed to give BRAND/TIME even more room).
        const isPI = ((window.FT_TOOL_SAVE && window.FT_TOOL_SAVE.kind) || '') === 'PI';
        const WEIGHTS = Object.assign({
            '__unsup__': 0.5, '__del__': 0.5,
            // "#" is only a short client-row number (~3 digits / 999) — keep it
            // tiny; freed space goes to CLIENT / FTCO descriptions and REMARK.
            '#': 0.15, 'Item Code': isPI ? 0.82 : 0.7, 'کد': 1.5, 'description': 3.05,
            'size': 0.9, 'qty': 0.42, 'unit': 0.65,
            'Final Arranged Text': 3.35, 'Alarm_Features': 1.5,
            // TO: BRAND / PF remark / remark / revision share equal width.
            'اصلاحیه': isPI ? 1.4 : 1.35,
            'ریمارک': isPI ? 1.25 : 1.35,
            '__proforma_remark__': isPI ? 1.25 : 1.35,
            'BRAND': isPI ? 1.85 : 1.55,
            'TIME': isPI ? 1.35 : 0.8,
            'UNIT PRICE': 1.95, 'SERVICE PRICE': 1.95, 'TOTAL PRICE': 2.05
        }, window.FT_MEASURED_WEIGHTS || {});
        // Keep unit small but wide enough to read the word "unit"/"pcs".
        WEIGHTS['unit'] = Math.max(0.62, Math.min(WEIGHTS['unit'] || 0.65, 0.72));
        const KIND = (window.FT_TOOL_SAVE && window.FT_TOOL_SAVE.kind) || '';
        const ALWAYS_HIDDEN = ['وزن', 'Group', 'Type', 'Filled_Features', 'Weight'];
        const KIND_HIDDEN = (KIND === 'PI') ? ['Alarm_Features', 'اصلاحیه']
                                            : ['UNIT PRICE', 'TOTAL PRICE', 'SERVICE PRICE'];
        const HIDDEN = ALWAYS_HIDDEN.concat(KIND_HIDDEN);

        // Drop any legacy colgroup so it can't fight the CSS widths.
        const oldColgroup = table.querySelector(':scope > colgroup[data-resize-cols="1"]');
        if (oldColgroup) oldColgroup.remove();

        const names = headerCells.map(th => (th.dataset.colName || '').trim());
        const visible = names.filter(n => HIDDEN.indexOf(n) < 0);
        // "#" uses a fixed px width (not % of the table) so it never steals
        // space from BRAND / descriptions on wide screens.
        // SERVICE PRICE only joins the % pool while the column is actually shown;
        // otherwise its weight would shrink CLIENT/FTCO/REMARK for nothing.
        const svcOn = document.body.classList.contains('svc-col-visible');
        const pctVisible = visible.filter(n => {
            if (n === '#') return false;
            if (n === 'SERVICE PRICE' && !svcOn) return false;
            return true;
        });
        const total = pctVisible.reduce((s, n) => s + (Object.prototype.hasOwnProperty.call(WEIGHTS, n) ? WEIGHTS[n] : 1.2), 0) || 1;

        const sel = (n) => '#virtual-scroll-table th[data-col-name="' + n + '"],#virtual-scroll-table td[data-col-name="' + n + '"]';
        let css = '';
        HIDDEN.forEach(n => { css += sel(n) + '{display:none !important;}'; });
        // ~3 digits (999) — hard cap; must beat static CSS in style.css / tool-panels.css.
        css += sel('#') + '{width:28px !important;min-width:26px !important;max-width:30px !important;}';
        if (!svcOn) {
            css += sel('SERVICE PRICE') + '{display:none !important;}';
        }
        pctVisible.forEach(n => {
            const w = (Object.prototype.hasOwnProperty.call(WEIGHTS, n) ? WEIGHTS[n] : 1.2);
            css += sel(n) + '{width:' + ((w / total) * 100).toFixed(4) + '%;}';
        });

        let styleEl = document.getElementById('ft-col-widths');
        if (!styleEl) {
            styleEl = document.createElement('style');
            styleEl.id = 'ft-col-widths';
            document.head.appendChild(styleEl);
        }
        styleEl.textContent = css;
        headerCells.forEach(th => { th.style.position = 'sticky'; });
        table.dataset.resizableReady = '1';
    };

    // Exposed so the Excel-style column paste (excel_paste.js) can write a
    // value into a remark/revision/editable cell of ANY row (including rows not
    // currently rendered) and have it processed + saved exactly like typing.
    window.FT_TABLE_UI = {
        submitRow: function (tr) { if (tr) submitRow(tr, 'full'); },
        setCellValue: function (tr, colName, value) {
            if (!tr) return;
            var cell = getCell(tr, colName);
            if (!cell) return;
            var ta = cell.querySelector('textarea');
            if (ta) {
                ta.value = value;
                autoSizeCellTextarea(ta);
            } else if (cell.dataset.calcVariable) {
                cell.dataset.calcRaw = value;
                cell.dataset.calcBase = value;
                cell.dataset.calcValue = value;
                var disp = cell.querySelector('.calc-display-value');
                if (disp) disp.textContent = value; else cell.textContent = value;
            } else {
                cell.textContent = value;
            }
        }
    };

    document.addEventListener('DOMContentLoaded', () => {
        const table = document.getElementById('virtual-scroll-table');
        if (!table) return;

        table.addEventListener('focusin', (event) => {
            const textarea = event.target.closest('textarea');
            if (!textarea) return;
            const colName = textarea.closest('td')?.dataset.colName || '';
            if (AUTO_SIZE_COLS.has(colName) || textarea.classList.contains('pi-text-area')
                || textarea.classList.contains('remark-revision-textarea')) {
                autoSizeCellTextarea(textarea);
            }
        });

        // Enter → move down the same column for REMARK / REVISION / BRAND / TIME
        // (Excel-style), in BOTH TO and PI. Shift+Enter inserts a real newline.
        // Skips locked / disabled cells and lands on the next writable one.
        // pi_columns.js also handles PI BRAND/TIME/UNIT/SERVICE/remark — that
        // path wins for those on PI (see early return below).
        const NAV_COLS = new Set(['ریمارک', 'اصلاحیه', 'BRAND', 'TIME']);
        function fieldWritable(fld) {
            if (!fld) return false;
            if (fld.disabled || fld.readOnly) return false;
            var td = fld.closest && fld.closest('td');
            if (td && (td.getAttribute('data-locked') === '1' || td.classList.contains('svc-locked'))) return false;
            var tr = fld.closest && fld.closest('tr');
            if (tr && (tr.getAttribute('data-deleted') === '1' || tr.getAttribute('data-unsuppliable') === '1')) return false;
            return true;
        }
        function focusNextWritable(rows, fromIdx, colName) {
            for (var i = fromIdx + 1; i < rows.length; i++) {
                var next = rows[i];
                if (!next) continue;
                var cell = next.querySelector('td[data-col-name="' + colName + '"]');
                if (!cell) continue;
                var fld = cell.querySelector('textarea, input.cell-input, input.pi-unit-input, input.svc-price-input');
                if (!fieldWritable(fld)) continue;
                try { next.scrollIntoView({ block: 'nearest' }); } catch (_e) {}
                setTimeout(function () { fld.focus(); if (fld.select) fld.select(); }, 0);
                return true;
            }
            return false;
        }
        table.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter' || event.shiftKey) return;
            const ta = event.target.closest && event.target.closest('textarea, input');
            if (!ta) return;
            const cell = ta.closest('td');
            const colName = cell?.dataset.colName || '';
            if (!NAV_COLS.has(colName)) return;
            // In PI, pi_columns.js handles ریمارک / BRAND / TIME navigation.
            if (KIND === 'PI' && (colName === 'ریمارک' || colName === 'BRAND' || colName === 'TIME')) return;
            event.preventDefault();
            const tr = ta.closest('tr');
            const eng = window.VirtualScrollEngine;
            const rows = (eng && eng.getVisibleRows) ? eng.getVisibleRows()
                       : (eng && eng.getRows) ? eng.getRows() : [];
            const idx = rows.indexOf(tr);
            if (idx < 0) { ta.blur(); return; }
            if (!focusNextWritable(rows, idx, colName)) ta.blur();
        });

        table.addEventListener('input', (event) => {
            const textarea = event.target.closest('textarea');
            if (!textarea) return;
            const colName = textarea.closest('td')?.dataset.colName || '';
            const shouldSize = AUTO_SIZE_COLS.has(colName)
                || textarea.classList.contains('pi-text-area')
                || textarea.classList.contains('remark-revision-textarea');
            if (!shouldSize) return;
            autoSizeCellTextarea(textarea);
            const tr = textarea.closest('tr');
            // BRAND / TIME are commercial fields — no row coder.
            if (colName === 'BRAND' || colName === 'TIME') return;
            // In PI mode, ریمارک is a simple proforma-remark field managed by
            // pi_pricing.js — don't trigger the row coder.
            if (KIND === 'PI' && colName === 'ریمارک') return;
            if (colName !== 'ریمارک' && colName !== 'اصلاحیه') return;
            debouncedSubmit(tr);
        });

        table.addEventListener('focusout', (event) => {
            const textarea = event.target.closest('textarea');
            if (!textarea) return;
            const colName = textarea.closest('td')?.dataset.colName || '';
            if (colName !== 'ریمارک' && colName !== 'اصلاحیه') return;
            // In PI mode, ریمارک is a simple proforma-remark field — don't trigger
            // row processing on blur (pi_pricing.js handles it).
            if (KIND === 'PI' && colName === 'ریمارک') return;
            const row = textarea.closest('tr');
            const state = getRowState(row);
            clearTimeout(state.lightTimer);
            clearTimeout(state.fullTimer);
            const values = getRowValues(row);
            // Build TO already ran the full shared pipeline on entry. An empty
            // remark/revision blur must not re-hit the server and rewrite the row.
            // Reprocess only when remark/revision actually change (type or clear).
            const signature = rowSignature(row, values);
            if (!values.remark && !values.revision) {
                if (!state.lastSubmittedSignatureByMode.full) {
                    state.lastSubmittedSignatureByMode.full = signature;
                    state.lastSubmittedSignatureByMode.light = signature;
                    return;
                }
            }
            submitRow(row, 'full');
        });

        table.addEventListener('dblclick', (event) => {
            if (event.target.closest('.row-margin-panel')) return;
            const cell = event.target.closest('td');
            if (!cell) return;
            // PI always-on fields already have permanent inputs — never replace
            // them with basic-cell-editor (that was wiping UNIT PRICE on dblclick).
            const col = cell.dataset.colName || '';
            if (KIND === 'PI' && PI_ALWAYS_ON.indexOf(col) >= 0) return;
            if (cell.querySelector('input.pi-unit-input, textarea.pi-text-area, textarea.cell-input, input.cell-input')) return;
            beginBasicCellEdit(cell);
        });

        // Single click opens a clean inline field for the value columns, so the
        // user doesn't need to double-click. Scoped to these columns only so it
        // never interferes with the Item Code flag box or text selection.
        // TO: size/qty/unit stay locked like Client Description.
        // PI: only always-on commercial inputs.
        const FIELD_EDIT_COLUMNS = (KIND === 'PI')
            ? new Set(['UNIT PRICE', 'BRAND', 'TIME'])
            : new Set();
        table.addEventListener('click', (event) => {
            if (event.target.closest('.row-margin-panel')) return;
            if (event.target.closest('.ic-box')) return;
            const cell = event.target.closest('td');
            if (!cell || cell.dataset.editing === '1') return;
            const col = cell.dataset.colName || '';

            // PI: always-on input columns — pi_columns.js handles these in capture
            // phase, but as a fallback also focus the inner input here.
            if (KIND === 'PI' && PI_ALWAYS_ON.indexOf(col) >= 0) {
                const inp = cell.querySelector('input.cell-input, input.pi-unit-input');
                if (inp) { inp.focus(); if (inp.select) inp.select(); }
                return;
            }

            if (!FIELD_EDIT_COLUMNS.has(col)) return;
            beginBasicCellEdit(cell);
        });

        // Auto-size wrap textareas (remark / revision / BRAND / TIME) as each
        // row scrolls into view.
        if (window.VirtualScrollEngine && window.VirtualScrollEngine.onRender) {
            window.VirtualScrollEngine.onRender((rows, start, end) => {
                for (let i = start; i < end; i++) {
                    const tr = rows[i];
                    if (!tr) continue;
                    tr.querySelectorAll(AUTO_SIZE_TA_SEL).forEach((ta) => {
                        if (ta === document.activeElement) return; // don't disturb typing
                        autoSizeCellTextarea(ta);
                    });
                }
            });
            // The engine already did its first render before this hook existed, so
            // refresh once to size the rows currently on screen.
            if (window.VirtualScrollEngine.refresh) {
                setTimeout(() => window.VirtualScrollEngine.refresh(), 0);
            }
        }

        // Initial pass for rows already painted (TO BRAND has no pi_columns.js).
        setTimeout(() => {
            table.querySelectorAll('tr.row').forEach((tr) => {
                tr.querySelectorAll(AUTO_SIZE_TA_SEL).forEach(autoSizeCellTextarea);
            });
        }, 0);
    });
})(window, document);