/*
 * row_processor.js
 * ------------------------------------------------------------
 * Responsibility:
 * - Send one edited table row to the Django processor endpoint.
 * - Update only the server-calculated cells for that same row.
 * - Keep request cancellation per row so fast typing does not apply stale responses.
 *
 * This file intentionally does not attach UI events. Event binding, dropdown behavior,
 * and textarea debounce logic live in table_ui.js.
 */
(function (window, document) {
    'use strict';

    const activeRowRequests = new Map(); // Only the row currently being edited is reprocessed.

    function getCell(row, columnName) {
        return row?.querySelector(`td[data-col-name="${CSS.escape(columnName)}"]`) || null;
    }

    /** TO + REQUIRE_FTCO_CODE_TO_SUPPLY=False → FTCO DISCRIPTION is user-editable. */
    function ftcoDescEditable() {
        const cfg = window.FT_TOOL_SAVE || {};
        return String(cfg.kind || '').toUpperCase() === 'TO' && cfg.requireFtcoCode === false;
    }

    function htmlToPlain(html) {
        var s = String(html || '');
        var low = s.toLowerCase();
        // Escaped colour markup ("&lt;span…&gt;") must be unescaped first,
        // otherwise tags stay visible inside the editable textarea.
        if (low.indexOf('&lt;') !== -1
            && (low.indexOf('span') !== -1 || low.indexOf('bdi') !== -1 || low.indexOf('br') !== -1)) {
            try {
                var taUn = document.createElement('textarea');
                taUn.innerHTML = s;
                s = taUn.value;
            } catch (e) {
                s = s.replace(/&lt;/gi, '<').replace(/&gt;/gi, '>').replace(/&amp;/gi, '&')
                    .replace(/&quot;/gi, '"').replace(/&#39;/gi, "'");
            }
        }
        s = s
            .replace(/<br\s*\/?>/gi, ' ')
            .replace(/<[^>]+>/g, '')
            .replace(/&amp;/g, '&')
            .replace(/&lt;/g, '<')
            .replace(/&gt;/g, '>')
            .replace(/&quot;/g, '"')
            .replace(/&#39;/g, "'");
        return s.replace(/\s+/g, ' ').trim();
    }

    function ftcoTextarea(cell) {
        if (!cell) return null;
        return cell.querySelector('textarea.ftco-desc-textarea, textarea.ftco-self-textarea');
    }

    function showFtcoRegexHtml(cell, row, html) {
        if (!cell) return;
        const next = html || '';
        cell.innerHTML = next;
        delete cell.dataset.userEdited;
        if (row) row.removeAttribute('data-ftco-user-edited');
        delete cell.dataset.originalHtml;
        if (window.TranslationManager?.setOriginalHtml) {
            window.TranslationManager.setOriginalHtml(cell, next);
        } else {
            cell.dataset.originalHtml = next;
        }
        wireFtcoCellActivate(cell, row);
    }

    function wireFtcoDescTextarea(cell, ta, row) {
        if (!ta || ta.dataset.ftcoWired === '1') return;
        ta.dataset.ftcoWired = '1';
        ta.addEventListener('input', function () {
            const baseline = String(ta.dataset.ftcoBaseline || '');
            if ((ta.value || '') !== baseline) {
                cell.dataset.userEdited = '1';
                if (row) row.setAttribute('data-ftco-user-edited', '1');
                delete cell.dataset.originalHtml;
            }
        });
        ta.addEventListener('blur', function () {
            const val = (ta.value || '').trim();
            const baseline = String(ta.dataset.ftcoBaseline || '').trim();

            // Full clear → drop manual lock and re-run regex (colours + alarms).
            if (!val) {
                delete cell.dataset.userEdited;
                delete cell.dataset.originalHtml;
                if (row) row.removeAttribute('data-ftco-user-edited');
                const desc = getCell(row, 'description')?.textContent?.trim()
                    || getCell(row, 'Description')?.textContent?.trim()
                    || '';
                const remark = getCell(row, 'ریمارک')?.querySelector('textarea')?.value?.trim() || '';
                const revision = getCell(row, 'اصلاحیه')?.querySelector('textarea')?.value?.trim() || '';
                if (row && typeof sendToProcessor === 'function') {
                    sendToProcessor(
                        desc,
                        row.dataset.group || '',
                        row.dataset.type || '',
                        row,
                        remark,
                        revision,
                        'full'
                    );
                } else {
                    showFtcoRegexHtml(
                        cell,
                        row,
                        cell.dataset.regexFinalHtml || cell.dataset.regexFinalPlain || ''
                    );
                }
                return;
            }

            // Typed something different from regex → keep plain textarea text.
            if (cell.dataset.userEdited === '1' || val !== baseline) {
                cell.dataset.userEdited = '1';
                if (row) row.setAttribute('data-ftco-user-edited', '1');
                delete cell.dataset.originalHtml;
                if (typeof window.autoSizeCellTextarea === 'function') {
                    window.autoSizeCellTextarea(ta);
                }
                return;
            }

            // Opened editor but did not change → restore coloured regex HTML.
            showFtcoRegexHtml(
                cell,
                row,
                cell.dataset.regexFinalHtml || ta.value || ''
            );
        });
    }

    function beginFtcoEdit(cell, row, markEdited) {
        if (!cell || !ftcoDescEditable()) return null;
        let ta = ftcoTextarea(cell);
        if (ta) {
            wireFtcoDescTextarea(cell, ta, row);
            return ta;
        }
        const sourceHtml = cell.dataset.regexFinalHtml || cell.innerHTML || '';
        if (!cell.dataset.regexFinalHtml) cell.dataset.regexFinalHtml = sourceHtml;
        if (!cell.dataset.regexFinalPlain) {
            cell.dataset.regexFinalPlain = htmlToPlain(sourceHtml);
        }
        const plain = htmlToPlain(sourceHtml);
        cell.innerHTML = '';
        ta = document.createElement('textarea');
        ta.className = 'cell-input ftco-desc-textarea remark-revision-textarea';
        ta.value = plain;
        ta.dataset.ftcoBaseline = plain;
        ta.style.minHeight = '38px';
        ta.style.width = '100%';
        cell.appendChild(ta);
        cell.dataset.editable = '1';
        cell.dataset.ftcoDescEditable = '1';
        if (markEdited && plain.trim()) {
            cell.dataset.userEdited = '1';
            if (row) row.setAttribute('data-ftco-user-edited', '1');
            delete cell.dataset.originalHtml;
        }
        wireFtcoDescTextarea(cell, ta, row);
        if (typeof window.autoSizeCellTextarea === 'function') {
            window.autoSizeCellTextarea(ta);
        }
        return ta;
    }

    function wireFtcoCellActivate(cell, row) {
        if (!cell || !ftcoDescEditable() || cell.dataset.ftcoClickWired === '1') return;
        cell.dataset.ftcoClickWired = '1';
        cell.dataset.ftcoDescEditable = '1';
        cell.dataset.editable = '1';
        cell.addEventListener('click', function (event) {
            if (!ftcoDescEditable()) return;
            if (ftcoTextarea(cell)) return;
            if (event.target && event.target.closest && event.target.closest('textarea')) return;
            const ta = beginFtcoEdit(cell, row, false);
            if (ta) {
                try { ta.focus(); ta.select(); } catch (e) {}
            }
        });
    }

    function ensureFtcoDescEditable(cell, row, initialHtml, markEditedIfFilled) {
        if (!cell || !ftcoDescEditable()) return null;
        if (markEditedIfFilled || row?.getAttribute('data-ftco-user-edited') === '1') {
            if (initialHtml != null && !ftcoTextarea(cell)) {
                cell.dataset.regexFinalHtml = initialHtml;
                cell.dataset.regexFinalPlain = htmlToPlain(initialHtml);
                cell.innerHTML = initialHtml;
            }
            return beginFtcoEdit(cell, row, true);
        }
        // Unedited rows keep coloured HTML until the user clicks to type.
        if (!cell.dataset.regexFinalHtml) {
            cell.dataset.regexFinalHtml = cell.innerHTML || '';
        }
        if (!cell.dataset.regexFinalPlain) {
            cell.dataset.regexFinalPlain = htmlToPlain(cell.dataset.regexFinalHtml);
        }
        wireFtcoCellActivate(cell, row);
        return null;
    }

    function applyFtcoDescFromServer(cell, row, newFinalHtml) {
        if (!cell) return;
        const plainFromServer = htmlToPlain(newFinalHtml);
        cell.dataset.regexFinalPlain = plainFromServer;
        cell.dataset.regexFinalHtml = newFinalHtml || '';

        if (!ftcoDescEditable()) {
            delete cell.dataset.ftcoDescEditable;
            delete cell.dataset.selfMode;
            if (ftcoTextarea(cell) || cell.innerHTML !== newFinalHtml) {
                cell.innerHTML = newFinalHtml;
            }
            if (window.TranslationManager?.setOriginalHtml) {
                window.TranslationManager.setOriginalHtml(cell, newFinalHtml);
            }
            return;
        }

        const manual = cell.dataset.userEdited === '1'
            || (row && row.getAttribute('data-ftco-user-edited') === '1');
        if (manual) {
            // Keep the exact user text; only refresh stashed regex for a later clear.
            const ta = ftcoTextarea(cell);
            if (!ta) beginFtcoEdit(cell, row, true);
            return;
        }

        // Regex / Remark / Revision still drive colours for unedited rows.
        showFtcoRegexHtml(cell, row, newFinalHtml || '');
    }

    /**
     * Read a named cookie value. Used for Django CSRF protection.
     */
    function getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }


    /**
     * Collect manually edited calculation values from writable raw calculation
     * cells. Keys are both the visible column title and the internal
     * data-variable-name, so backend formulas can depend on stable variables
     * even when the visible title is renamed later.
     */
    function collectCalculationOverrides(row) {
        const overrides = {};
        row.querySelectorAll('td[data-editable="1"][data-user-edited="1"]').forEach((cell) => {
            const title = cell.dataset.colName || '';
            const variable = cell.dataset.variableName || '';
            const value = cell.textContent.trim();
            if (title) overrides[title] = value;
            if (variable) overrides[variable] = value;
        });
        return overrides;
    }

    function clearGroupChangeConfirm(row) {
        const cell = getCell(row, 'اصلاحیه');
        if (!cell) return;
        const bar = cell.querySelector('.rev-group-confirm');
        if (bar) bar.remove();
        cell.removeAttribute('data-group-pending');
    }

    function revisionIsEmpty(revision) {
        return !String(revision || '').trim();
    }

    function clearRevGroupSessionIfEmpty(row, revision) {
        if (revisionIsEmpty(revision)) {
            delete row.dataset.revGroupReject;
            delete row.dataset.revGroupLocked;
            delete row.dataset.revTypeLocked;
        }
    }

    function showGroupChangeConfirm(row, pending, original_text, group, type, remark, revision) {
        const cell = getCell(row, 'اصلاحیه');
        if (!cell || !pending) return;
        // Already rejected or confirmed for this non-empty Revision session —
        // never re-prompt until Revision is cleared entirely.
        if (row.dataset.revGroupReject === '1') return;
        if (row.dataset.revGroupLocked) return;
        clearGroupChangeConfirm(row);
        cell.setAttribute('data-group-pending', '1');

        const bar = document.createElement('div');
        bar.className = 'rev-group-confirm';
        bar.innerHTML =
            '<span class="rev-group-confirm-msg">Change group ' +
            '<b>' + String(pending.from || '').toUpperCase() + '</b> → ' +
            '<b>' + String(pending.to || '').toUpperCase() + '</b>?</span>' +
            '<span class="rev-group-confirm-actions">' +
            '<button type="button" class="rev-group-btn rev-group-confirm-btn">Confirm</button>' +
            '<button type="button" class="rev-group-btn rev-group-reject-btn">Reject</button>' +
            '</span>';
        const ta = cell.querySelector('textarea');
        if (ta && ta.parentNode) {
            ta.parentNode.insertBefore(bar, ta);
        } else {
            cell.insertBefore(bar, cell.firstChild);
        }

        bar.querySelector('.rev-group-confirm-btn').onclick = function (e) {
            e.preventDefault();
            e.stopPropagation();
            // Sticky Confirm: keep this group until Revision is emptied.
            const newGroup = String(pending.to || '');
            const newType = String(pending.to_type || '');
            row.dataset.revGroupLocked = newGroup;
            row.dataset.revTypeLocked = newType;
            delete row.dataset.revGroupReject;
            // Switch the visible/live group immediately so EA (and filters)
            // leave the old group before the reprocess round-trip returns.
            if (newGroup) {
                row.dataset.group = newGroup;
                const gCell = getCell(row, 'Group');
                if (gCell) gCell.textContent = newGroup;
            }
            if (newType) {
                row.dataset.type = newType;
                const tCell = getCell(row, 'Type');
                if (tCell) tCell.textContent = newType;
            }
            clearGroupChangeConfirm(row);
            document.dispatchEvent(new CustomEvent('ft-ea-group-switched', {
                detail: { row: row, group: newGroup, type: newType }
            }));
            sendToProcessor(original_text, newGroup, newType, row, remark, revision, 'full', true);
        };
        bar.querySelector('.rev-group-reject-btn').onclick = function (e) {
            e.preventDefault();
            e.stopPropagation();
            // Remember reject until Revision is cleared entirely.
            row.dataset.revGroupReject = '1';
            delete row.dataset.revGroupLocked;
            delete row.dataset.revTypeLocked;
            clearGroupChangeConfirm(row);
            sendToProcessor(original_text, group, type, row, remark, revision, 'full', false);
        };
    }

    /**
     * Send the current row values to /ajax/process-row/ and update the same row.
     *
     * Important behavior kept from the original code:
     * - Abort the previous pending request for the same row.
     * - Update Code, Final_Text, Filled_Features and Alarm only when values changed.
     * - Re-apply translation only for the edited row when current language is not English.
     *
     * ``confirmGroupChange``: null | true | false — Revision group-change gate.
     */
    function sendToProcessor(original_text, group, type, row, remark = '', revision = '', assignMode = 'full', confirmGroupChange = null) {
        if (!row) return;

        clearRevGroupSessionIfEmpty(row, revision);
        // After Reject: keep auto-denying group flips until Revision is emptied.
        if (confirmGroupChange == null && row.dataset.revGroupReject === '1' && !revisionIsEmpty(revision)) {
            confirmGroupChange = false;
        }

        const rowId = row.id || String(row.rowIndex);
        const previousController = activeRowRequests.get(rowId);
        if (previousController) previousController.abort();

        const controller = new AbortController();
        activeRowRequests.set(rowId, controller);

        const sizeCell = getCell(row, 'size');
        if (sizeCell && row.dataset.baseSize === undefined) {
            row.dataset.baseSize = sizeCell.textContent.trim() || '';
        }
        // Always send the stable base size. Remark/revision may temporarily
        // override the visible size, but clearing them must reprocess from the
        // original size again.
        const cleanSize = row.dataset.baseSize || sizeCell?.textContent.trim() || '';
        const qtyValue = getCell(row, 'qty')?.textContent.trim() || '';
        const unitValue = getCell(row, 'unit')?.textContent.trim() || '';
        const finalTextCell = getCell(row, 'Final Arranged Text');
        const filledFeaturesCell = getCell(row, 'Filled_Features');
        const alarmCell = getCell(row, 'Alarm_Features');

        const body = new URLSearchParams({
            text: original_text,
            group: group,
            type: type,
            remark: remark,
            revision: revision,
            clean_size: cleanSize,
            qty: qtyValue,
            unit: unitValue,
            calculation_overrides: JSON.stringify(collectCalculationOverrides(row)),
            row_index: String(
                row.dataset.virtualIndex != null && row.dataset.virtualIndex !== ''
                    ? row.dataset.virtualIndex
                    : (row.sectionRowIndex >= 0 ? row.sectionRowIndex : row.rowIndex - 1)
            ),
            assign_mode: assignMode
        });
        if (confirmGroupChange === true) body.set('confirm_group_change', '1');
        if (confirmGroupChange === false) body.set('confirm_group_change', '0');
        // After Confirm: pin the accepted group so further Revision typing cannot
        // re-prompt or silently switch to another detected group.
        if (row.dataset.revGroupLocked && !revisionIsEmpty(revision)) {
            body.set('locked_group', row.dataset.revGroupLocked);
            if (row.dataset.revTypeLocked) {
                body.set('locked_type', row.dataset.revTypeLocked);
            }
        }

        fetch('/ajax/process-row/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-CSRFToken': getCookie('csrftoken')
            },
            body: body.toString(),
            signal: controller.signal
        })
        .then(res => res.json())
        .then(res => {
            // Ignore old responses when a newer request has already been sent for this row.
            if (activeRowRequests.get(rowId) !== controller) return;
            activeRowRequests.delete(rowId);

            const codeCell = getCell(row, 'کد');
            const responseMode = res.Assign_Mode || assignMode || 'full';
            // Light responses update regex/alarm/final text quickly. They clear
            // stale code while the full lookup is pending, but never write an old
            // code back over what the user is typing.
            if (codeCell) {
                const nextCode = (responseMode === 'full') ? (res.Code || '') : '';
                if (codeCell.textContent !== nextCode) codeCell.textContent = nextCode;
            }

            if (res.Rule_Targets) {
                row.dataset.ruleTargets = JSON.stringify(res.Rule_Targets);
            }
            if (res.Group !== undefined) row.dataset.group = res.Group || '';
            if (res.Type !== undefined) row.dataset.type = res.Type || '';

            function setHiddenGroupTypeCell(cell, value) {
                if (!cell) return;
                const next = value || '';
                const sel = cell.querySelector('select');
                if (sel) {
                    // Legacy: replace leftover <select> with plain text (dropdowns removed).
                    cell.textContent = next;
                    return;
                }
                if (cell.textContent !== next) cell.textContent = next;
            }
            setHiddenGroupTypeCell(getCell(row, 'Group'), res.Group);
            setHiddenGroupTypeCell(getCell(row, 'Type'), res.Type);

            const newFinalText = res.Final_Text || '';
            if (finalTextCell) {
                applyFtcoDescFromServer(finalTextCell, row, newFinalText);
            }

            const newFilledFeatures = res.Filled_Features || '';
            if (filledFeaturesCell && filledFeaturesCell.innerHTML !== newFilledFeatures) {
                filledFeaturesCell.innerHTML = newFilledFeatures;
            }

            // Keep data-vars in sync so PI/TO feature filters can resolve values
            // from the live row (not only from Filled_Features HTML).
            if (res.Feature_Variables && typeof res.Feature_Variables === 'object') {
                try {
                    row.setAttribute('data-vars', JSON.stringify(res.Feature_Variables));
                } catch (e) {}
            }

            const alarmList = (res.Alarm || []);
            const newAlarm = alarmList.map(function (a) {
                return '<span class="alarm-chip">' + String(a) + '</span>';
            }).join(' ');
            if (alarmCell && alarmCell.innerHTML !== newAlarm) {
                alarmCell.innerHTML = newAlarm;
            }

            if (res.Pending_Group_Change && assignMode === 'full') {
                if (row.dataset.revGroupReject === '1' || row.dataset.revGroupLocked) {
                    // Session already rejected or confirmed — stay silent.
                    clearGroupChangeConfirm(row);
                } else {
                    showGroupChangeConfirm(
                        row, res.Pending_Group_Change,
                        original_text, group, type, remark, revision
                    );
                }
            } else if (!res.Pending_Group_Change) {
                clearGroupChangeConfirm(row);
            }

            if (sizeCell) {
                const overrideSize = String(res.Size_Override || '').trim();

                // Size typed in Remark/Revision is a temporary override: it must
                // appear both in Final_Text and in the visible size column, and
                // when the size token is removed the column must return to the
                // stable original upload/manual value kept in data-base-size.
                if (row.dataset.baseSize === undefined) {
                    row.dataset.baseSize = sizeCell.textContent.trim() || '';
                }

                if (overrideSize) {
                    row.dataset.sizeOverrideActive = '1';
                    const html = `<span class="highlight-color">${overrideSize}</span>`;
                    if (sizeCell.innerHTML !== html) sizeCell.innerHTML = html;
                } else if (row.dataset.sizeOverrideActive === '1') {
                    sizeCell.textContent = row.dataset.baseSize || '';
                    delete row.dataset.sizeOverrideActive;
                }
            }

            if (res.Extra_Columns) {
                Object.entries(res.Extra_Columns).forEach(([name, value]) => {
                    const extraCell = getCell(row, name);
                    if (!extraCell) return;
                    if (extraCell.dataset.userEdited === '1') return;
                    const valueText = String(value || '');
                    const textarea = extraCell.querySelector('textarea');
                    if (textarea) {
                        if (textarea.value !== valueText) textarea.value = valueText;
                    } else if (extraCell.textContent !== valueText) {
                        extraCell.textContent = valueText;
                    }
                    if (extraCell.dataset.calcVariable) {
                        extraCell.dataset.calcRaw = valueText;
                        extraCell.dataset.calcBase = valueText;
                        extraCell.dataset.calcValue = valueText;
                        delete extraCell.dataset.calcInputBase;
                    }
                });
            }

            if (window.CalculationControls?.refreshRow) {
                window.CalculationControls.refreshRow(row);
            }

            if (window.translateSingleRow) {
                const currentLang = document.querySelector('.lang-switch')?.value || 'en';
                if (currentLang !== 'en') window.translateSingleRow(row, currentLang);
            }

            // Tell the counters (rows without an FT code / no unit price) and any
            // other listeners that this row's code/price may have changed.
            document.dispatchEvent(new CustomEvent('ft-rows-changed', { detail: { row: row } }));
        })
        .catch(err => {
            if (err.name !== 'AbortError') console.error('Processor error:', err);
        });
    }

    // Public API used by table_ui.js.
    window.RowProcessor = {
        sendToProcessor,
        initFtcoDescEditable: function () {
            if (!ftcoDescEditable()) return;
            const eng = window.VirtualScrollEngine;
            const rows = (eng && eng.getRows) ? eng.getRows() : Array.prototype.slice.call(
                document.querySelectorAll('#excel-table-container tbody tr')
            );
            rows.forEach(function (row) {
                if (!row) return;
                const cell = getCell(row, 'Final Arranged Text');
                if (!cell) return;
                const wasManual = row.getAttribute('data-ftco-user-edited') === '1';
                ensureFtcoDescEditable(cell, row, null, wasManual);
            });
        }
    };

    document.addEventListener('DOMContentLoaded', function () {
        setTimeout(function () {
            if (window.RowProcessor && window.RowProcessor.initFtcoDescEditable) {
                window.RowProcessor.initFtcoDescEditable();
            }
        }, 0);
        if (window.VirtualScrollEngine && window.VirtualScrollEngine.onRender) {
            window.VirtualScrollEngine.onRender(function () {
                if (window.RowProcessor && window.RowProcessor.initFtcoDescEditable) {
                    window.RowProcessor.initFtcoDescEditable();
                }
            });
        }
    });
})(window, document);
