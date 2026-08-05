/*
 * calculation_controls.js
 * ------------------------------------------------------------
 * Frontend-only customization for calculation columns:
 * - currency/number formatting
 * - currency conversion for configured variables
 * - global sequential margins by group
 * - per-row sequential margins on configured variables
 *
 * It does not change backend item-coding logic. Base values are kept in
 * data-calc-base, and rendered values are recalculated in-place for speed.
 */
(function (window, document) {
    'use strict';

    const card = () => document.getElementById('calculation-control-card');
    const table = () => document.querySelector('#excel-table-container table');

    function allDataRows() {
        if (window.VirtualScrollEngine?.getRows) {
            const rows = window.VirtualScrollEngine.getRows();
            if (rows && rows.length) return Array.from(rows);
        }
        const tbl = table();
        return Array.from(tbl?.tBodies[0]?.rows || []);
    }

    function visibleDataRows() {
        if (window.VirtualScrollEngine?.getVisibleRows) {
            return Array.from(window.VirtualScrollEngine.getVisibleRows() || []);
        }
        const tbl = table();
        return Array.from(tbl?.tBodies[0]?.rows || []);
    }

    let config = { view: {}, calculate: [], currency_units: [], columns: [] };
    let rowMarginVisible = false;
    const rowMarginState = new WeakMap(); // row -> { variableName: [percent, ...] }
    // Saved per-group margins: { groupKeyLower: [percent, ...] }. "__all__" is a
    // valid key meaning every row. Rebuilt from saved state on load.
    const groupMarginStore = {};
    const fxBoard = { stale: false, loading: false, units: null };
    let refreshAllFrame = null;
    let refreshAllToken = 0;
    const REFRESH_BATCH_SIZE = 180;


    function cleanText(value) {
        if (value === null || value === undefined) return '';
        const text = String(value).trim();
        return /^(none|null|nan)$/i.test(text) ? '' : text;
    }

    function toNumber(value, fallback = 0) {
        const text = cleanText(value).replace(/٬/g, ',').replace(/٫/g, '.');
        if (!text) return fallback;
        const match = text.match(/[-+]?\d+(?:,\d{3})*(?:\.\d+)?|[-+]?\d*\.\d+/);
        if (!match) return fallback;
        const parsed = Number(match[0].replace(/,/g, ''));
        return Number.isFinite(parsed) ? parsed : fallback;
    }

    function formatWithCommas(value, decimals) {
        const number = toNumber(value, 0);
        const fixed = Number(decimals) >= 0 ? number.toFixed(Number(decimals)) : String(number);
        const parts = fixed.split('.');
        parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
        return parts.join('.');
    }

    /** Display-only FX rate with thousand separators (still parsed by toNumber). */
    function formatRateDisplay(value) {
        const text = cleanText(value);
        if (!text) return '';
        const number = toNumber(text, NaN);
        if (!Number.isFinite(number)) return text;
        let raw;
        if (Math.abs(number) >= 1) {
            raw = Number.isInteger(number)
                ? String(Math.round(number))
                : number.toFixed(6).replace(/\.?0+$/, '');
        } else {
            raw = number.toFixed(10).replace(/\.?0+$/, '') || '0';
        }
        const parts = String(raw).split('.');
        parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
        return parts.join('.');
    }

    function setRateInputValue(rateInput, value) {
        if (!rateInput) return;
        const text = cleanText(value);
        rateInput.value = text ? formatRateDisplay(text) : '';
    }

    function currencySymbol(unit) {
        const item = (config.currency_units || []).find((u) => u.value === unit);
        if (item && item.symbol) return item.symbol;
        if (fxBoard.units) {
            const hit = fxBoard.units.find((u) => u.code === unit);
            if (hit && hit.symbol) return hit.symbol;
        }
        if (unit === 'usd') return '$';
        if (unit === 'eur') return '€';
        if (unit === 'rial') return 'Rial';
        return String(unit || '').toUpperCase();
    }

    function getCell(row, variableName) {
        return row?.querySelector(`td[data-calc-variable="${CSS.escape(variableName)}"]`) || null;
    }

    function getColumnByVariable(variableName) {
        return (config.columns || []).find((col) => col.variable === variableName) || null;
    }

    function getVariableTitle(variableName) {
        const col = getColumnByVariable(variableName);
        return cleanText(col?.title) || variableName;
    }

    function ensureDisplaySpan(cell) {
        let span = cell.querySelector(':scope > .calc-display-value');
        if (!span) {
            span = document.createElement('span');
            span.className = 'calc-display-value';

            // Keep row-margin inputs, price-source tag, and pi-unit-input alive.
            const panel = cell.querySelector(':scope > .row-margin-panel');
            const srcTag = cell.querySelector(':scope > .price-src');
            const piInp = cell.querySelector(':scope > input.pi-unit-input');
            Array.from(cell.childNodes).forEach((node) => {
                if (node !== panel && node !== srcTag && node !== piInp) node.remove();
            });
            cell.insertBefore(span, panel || piInp || srcTag || null);
        }
        return span;
    }

    function readBaseNumber(row, variableName) {
        const cell = getCell(row, variableName);
        if (cell) {
            return toNumber(cell.dataset.calcBase ?? cell.dataset.calcRaw ?? cell.textContent, 0);
        }

        // Built-in row variables such as qty/size/unit are not calculation cells.
        const builtin = row?.querySelector(`td[data-col-name="${CSS.escape(variableName)}"]`);
        if (builtin) return toNumber(builtin.textContent, 0);

        return 0;
    }

    function readResolvedNumber(row, variableName) {
        const cell = getCell(row, variableName);
        if (cell && cleanText(cell.dataset.calcValue)) return toNumber(cell.dataset.calcValue, 0);
        return readBaseNumber(row, variableName);
    }

    function resolveInput(row, inputSpec) {
        if (Array.isArray(inputSpec) && inputSpec.length >= 2) {
            // CSV-based values are already materialized into the calculation cell's
            // base value on the backend, so direct CSV reads are not needed here.
            return 0;
        }
        const key = cleanText(inputSpec);
        if (!key) return 0;
        return readResolvedNumber(row, key);
    }

    // Decimals now follow the currency (no manual control): 2 for USD/EUR, 0 for
    // Rial. Applies to UNIT PRICE / TOTAL PRICE / GRAND TOTAL alike.
    function getDecimals(variableName) {
        const u = selectedCurrencyUnit();
        return (u === 'rial') ? 0 : 2;
    }

    // Effective display currency for the price columns. It follows the Unit
    // conversion field: the "To" currency when a real conversion is active,
    // otherwise the "From" currency (which itself defaults to the case's
    // inherent currency — Rial internal / USD external). The old standalone
    // "Currency & decimals" selector was removed.
    // Confirmed conversion target (applied only after Confirm). Pending picks
    // live in #calc-convert-to until the user confirms.
    let confirmedTo = '';

    function lockedFromUnit() {
        const ext = !!(window.FT_TOOL_SAVE && window.FT_TOOL_SAVE.externalCurrency);
        return ext ? 'usd' : 'rial';
    }

    function ensureFromLocked() {
        const fromSel = document.getElementById('calc-convert-from');
        if (!fromSel) return lockedFromUnit();
        const from = lockedFromUnit();
        fromSel.innerHTML = '';
        const opt = document.createElement('option');
        opt.value = from;
        opt.textContent = from === 'usd' ? 'USD ($)' : 'Rial';
        opt.selected = true;
        fromSel.appendChild(opt);
        fromSel.value = from;
        fromSel.disabled = true;
        return from;
    }

    function selectedCurrencyUnit() {
        const from = lockedFromUnit();
        const to = confirmedTo || from;
        return hasActiveConversion() ? to : from;
    }

    function conversionFactor() {
        const from = lockedFromUnit();
        const to = confirmedTo || from;
        if (!to || from === to) return 1;
        if (fxBoard.stale) return 1;
        const rateText = cleanText(document.getElementById('calc-convert-rate')?.value);
        const rate = toNumber(rateText, 0);
        if (!rateText || !rate) return 1;
        return 1 / rate;
    }

    function hasActiveConversion() {
        const from = lockedFromUnit();
        const to = confirmedTo || from;
        if (!to || from === to) return false;
        if (fxBoard.stale) return false;
        const rateText = cleanText(document.getElementById('calc-convert-rate')?.value);
        const rate = toNumber(rateText, 0);
        return Boolean(rateText && rate);
    }

    function unitLabelOf(u) {
        const sym = currencySymbol(u);
        if (sym) return sym;
        return (u === 'usd') ? '$' : (u === 'eur') ? '€' : (u === 'rial' ? 'Rial' : String(u || '').toUpperCase());
    }

    function resolvedFromTo() {
        const from = lockedFromUnit();
        const toSel = document.getElementById('calc-convert-to');
        const pending = (toSel && toSel.value) || confirmedTo || from;
        const to = confirmedTo || from;
        return { from, to, pending, different: from !== to };
    }

    // Save is blocked only when a confirmed conversion is active but the FX
    // rate cannot be resolved (stale / missing).
    function conversionNeedsRate() {
        const { from, to, different } = resolvedFromTo();
        if (!different) return false;
        if (fxBoard.stale) return true;
        const rateText = cleanText(document.getElementById('calc-convert-rate')?.value);
        const rate = toNumber(rateText, 0);
        return !(rateText && rate);
    }
    window.CalcConversionNeedsRate = conversionNeedsRate;
    // Read-only exports for service_price.js: Service Price must convert
    // currency exactly the same way UNIT PRICE does — reusing this function
    // (rather than re-deriving the same value a second way) is what
    // guarantees that "same currency unit... subject to the same unit
    // conversions" actually holds, instead of two independent
    // implementations quietly drifting apart later.
    window.CalcConversionFactor = conversionFactor;
    window.CalcSelectedCurrencyUnit = selectedCurrencyUnit;

    function fxApiBase() {
        const grid = document.querySelector('.calc-grid-pi') || document.querySelector('.calc-section-convert');
        return (grid && grid.getAttribute('data-fx-api')) || '/fx-rates/api/';
    }

    function fillToSelect(units, preferred) {
        const sel = document.getElementById('calc-convert-to');
        if (!sel) return;
        const ext = !!(window.FT_TOOL_SAVE && window.FT_TOOL_SAVE.externalCurrency);
        const cur = preferred || sel.value || lockedFromUnit();
        sel.innerHTML = '';
        (units || []).forEach((u) => {
            if (ext && u.code === 'rial') return;
            const opt = document.createElement('option');
            opt.value = u.code;
            const sym = u.symbol || u.code.toUpperCase();
            opt.textContent = sym + ' - ' + (u.name || u.code.toUpperCase());
            sel.appendChild(opt);
        });
        if (!sel.options.length) {
            const from = lockedFromUnit();
            const opt = document.createElement('option');
            opt.value = from;
            opt.textContent = from === 'usd' ? '$ - US Dollar' : 'Rial - Iranian Rial';
            sel.appendChild(opt);
        }
        if (cur && Array.from(sel.options).some((o) => o.value === cur)) sel.value = cur;
        else sel.value = sel.options[0].value;
        rebuildToCombo();
    }

    function rebuildToCombo() {
        const host = document.getElementById('calc-convert-to-combo');
        const sel = document.getElementById('calc-convert-to');
        if (!host || !sel) return;
        host.innerHTML = '';
        const input = document.createElement('input');
        input.type = 'text';
        input.className = 'gm-combo-input';
        input.autocomplete = 'off';
        input.placeholder = 'Search currency…';
        const menu = document.createElement('div');
        menu.className = 'gm-combo-menu';
        menu.hidden = true;
        host.appendChild(input);
        host.appendChild(menu);

        function labelOf(val) {
            const opt = Array.from(sel.options).find((o) => o.value === val);
            return opt ? opt.textContent : val;
        }
        function syncInput() { input.value = labelOf(sel.value); }
        function render(filter) {
            const q = String(filter || '').trim().toLowerCase();
            menu.innerHTML = '';
            let n = 0;
            Array.from(sel.options).forEach((o) => {
                const lab = o.textContent.trim();
                if (q && lab.toLowerCase().indexOf(q) < 0 && o.value.indexOf(q) < 0) return;
                const row = document.createElement('div');
                row.textContent = lab;
                row.addEventListener('mousedown', (ev) => {
                    ev.preventDefault();
                    sel.value = o.value;
                    syncInput();
                    menu.hidden = true;
                    onPendingToChanged();
                });
                menu.appendChild(row);
                n += 1;
            });
            if (!n) {
                const empty = document.createElement('div');
                empty.className = 'gm-combo-empty';
                empty.textContent = 'No match';
                menu.appendChild(empty);
            }
        }
        input.addEventListener('focus', () => { render(''); menu.hidden = false; });
        input.addEventListener('input', () => { render(input.value); menu.hidden = false; });
        input.addEventListener('blur', () => { setTimeout(() => { menu.hidden = true; syncInput(); }, 120); });
        syncInput();
    }

    function updateConfirmButton() {
        const btn = document.getElementById('calc-convert-confirm');
        const resetBtn = document.getElementById('calc-convert-reset');
        const note = document.getElementById('calc-fx-note');
        const from = lockedFromUnit();
        const toSel = document.getElementById('calc-convert-to');
        const pending = (toSel && toSel.value) || from;
        const can = pending && pending !== from && !fxBoard.stale && toNumber(document.getElementById('calc-convert-rate')?.value, 0) > 0;
        if (btn) btn.disabled = !can;
        if (resetBtn) {
            const active = Boolean(confirmedTo && confirmedTo !== from);
            resetBtn.hidden = !active;
            resetBtn.disabled = false;
        }
        if (note) note.textContent = '';
        syncRateDisplay(from, pending);
    }

    function syncRateDisplay(from, to) {
        const wrap = document.getElementById('calc-convert-rate-wrap');
        const rateInput = document.getElementById('calc-convert-rate');
        const unitEl = document.getElementById('calc-convert-rate-unit');
        const show = Boolean(to && from && to !== from);
        if (wrap) wrap.hidden = !show;
        if (!show) {
            if (rateInput && !confirmedTo) setRateInputValue(rateInput, '');
            if (unitEl) unitEl.textContent = '';
            return;
        }
        if (unitEl) {
            // Rate convention: how many FROM units equal one TO unit.
            unitEl.textContent = unitLabelOf(from) + ' / ' + unitLabelOf(to);
        }
    }

    function onPendingToChanged() {
        const from = lockedFromUnit();
        const toSel = document.getElementById('calc-convert-to');
        const pending = (toSel && toSel.value) || from;
        const pendingEl = document.getElementById('calc-convert-pending-to');
        if (pendingEl) pendingEl.value = pending;
        syncRateDisplay(from, pending);
        // Fetch rate for the pending pair; do NOT apply until Confirm.
        fetchRateFor(from, pending, function () { updateConfirmButton(); });
    }

    function fetchRateFor(from, to, done) {
        const rateInput = document.getElementById('calc-convert-rate');
        if (!to || from === to) {
            setRateInputValue(rateInput, '');
            fxBoard.stale = false;
            syncRateDisplay(from, to);
            if (typeof done === 'function') done();
            return;
        }
        const url = fxApiBase() + '?from=' + encodeURIComponent(from) + '&to=' + encodeURIComponent(to);
        fetch(url, { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data || data.ok === false) throw new Error('FX lookup failed');
                fxBoard.stale = !!data.stale;
                fxBoard.units = data.units || fxBoard.units;
                if (data.units) fillToSelect(data.units, to);
                if (data.convertible && data.rate != null && !data.stale) {
                    setRateInputValue(rateInput, data.rate);
                } else {
                    setRateInputValue(rateInput, '');
                }
                syncRateDisplay(from, to);
                if (typeof done === 'function') done();
            })
            .catch(function () {
                fxBoard.stale = true;
                setRateInputValue(rateInput, '');
                syncRateDisplay(from, to);
                if (typeof done === 'function') done();
            });
    }

    function confirmConversion() {
        const from = lockedFromUnit();
        const toSel = document.getElementById('calc-convert-to');
        const pending = (toSel && toSel.value) || from;
        if (!pending || pending === from) return;
        if (fxBoard.stale) return;
        const rate = toNumber(document.getElementById('calc-convert-rate')?.value, 0);
        if (!rate) return;
        confirmedTo = pending;
        syncConversionUi();
        scheduleRefreshAll();
        buildMarginReport();
        if (window.PIReformatUnits) window.PIReformatUnits();
        updateConfirmButton();
        logCurrencyConversion(from, pending, rate);
    }

    function resetConversion() {
        const from = ensureFromLocked();
        const prevTo = confirmedTo || from;
        confirmedTo = from;
        const toSel = document.getElementById('calc-convert-to');
        const rateInput = document.getElementById('calc-convert-rate');
        if (toSel) {
            if (Array.from(toSel.options).some((o) => o.value === from)) toSel.value = from;
            else {
                // Ensure From is present as a selectable To target for display sync.
                const opt = document.createElement('option');
                opt.value = from;
                opt.textContent = from === 'usd' ? '$ - US Dollar' : (from === 'rial' ? 'Rial - Iranian Rial' : from.toUpperCase());
                toSel.insertBefore(opt, toSel.firstChild);
                toSel.value = from;
            }
            rebuildToCombo();
        }
        if (rateInput) setRateInputValue(rateInput, '');
        const pendingEl = document.getElementById('calc-convert-pending-to');
        if (pendingEl) pendingEl.value = from;
        syncRateDisplay(from, from);
        syncConversionUi();
        scheduleRefreshAll();
        buildMarginReport();
        if (window.PIReformatUnits) window.PIReformatUnits();
        updateConfirmButton();
        if (prevTo && prevTo !== from) {
            logCurrencyConversion(prevTo, from, 1, true);
        }
    }

    function logCurrencyConversion(from, to, rate, isReset) {
        const cfg = window.FT_TOOL_SAVE || {};
        const url = cfg.currencyLogUrl;
        if (!url || !from || !to || from === to) return;
        const body = new URLSearchParams();
        body.set('from_unit', from);
        body.set('to_unit', to);
        body.set('rate', String(rate || ''));
        body.set('side', cfg.side || '');
        if (isReset) body.set('reset', '1');
        fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-CSRFToken': cfg.csrfToken || '',
                'X-Requested-With': 'XMLHttpRequest',
            },
            body: body.toString(),
        }).catch(function () { /* audit is best-effort */ });
    }

    function syncManagedFxRate(done) {
        const from = ensureFromLocked();
        if (!confirmedTo) confirmedTo = from;
        const url = fxApiBase();
        fetch(url, { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then((r) => r.json())
            .then((data) => {
                if (!data || data.ok === false) throw new Error('FX lookup failed');
                fxBoard.stale = !!data.stale;
                fxBoard.units = data.units || [];
                const toSel = document.getElementById('calc-convert-to');
                const prefer = (toSel && toSel.value) || confirmedTo || from;
                fillToSelect(data.units, prefer);
                fetchRateFor(from, prefer, () => {
                    updateConfirmButton();
                    syncConversionUi();
                    if (typeof done === 'function') done();
                });
            })
            .catch(() => {
                fxBoard.stale = true;
                fillToSelect([{ code: from, name: from === 'usd' ? 'US Dollar' : 'Iranian Rial', symbol: from === 'usd' ? '$' : 'Rial' }], from);
                updateConfirmButton();
                syncConversionUi();
                if (typeof done === 'function') done();
            });
    }

    function syncConversionUi() {
        // No on-screen FX warning banners — Confirm is simply disabled when the
        // pending pair cannot be converted.
        updateConfirmButton();
    }
    window.CalcSyncConversion = syncConversionUi;

    /* ── Combined margin: per-group saved margins + per-row margins ──
       groupMarginStore holds the SAVED margins for each group ("__all__" means
       every row). The editor is just a live view of the selected group's list;
       typing updates the store so the price preview follows immediately. */

    function currentMarginGroupKey() {
        const v = document.getElementById('global-margin-group')?.value || '__all__';
        return v === '__all__' ? '__all__' : v.toLowerCase();
    }

    function readEditorMargins() {
        return Array.from(document.querySelectorAll('.global-margin-row .global-margin-percent'))
            .map((inp) => inp.value)
            .filter((v) => cleanText(v) !== '' && toNumber(v, 0) !== 0)
            .map((v) => toNumber(v, 0));
    }

    function loadGroupMarginsIntoEditor(key) {
        const list = document.getElementById('global-margin-list');
        if (!list) return;
        const vals = groupMarginStore[key] || [];
        const arr = vals.length ? vals.slice() : [''];
        list.innerHTML = '';
        arr.forEach((v) => {
            const row = document.createElement('div');
            row.className = 'global-margin-row';
            const input = document.createElement('input');
            input.className = 'global-margin-percent';
            input.type = 'text';
            input.inputMode = 'decimal';
            input.placeholder = 'Margin %';
            input.value = (v === '' ? '' : String(v));
            row.appendChild(input);
            list.appendChild(row);
        });
    }

    function commitEditorToStore() {
        const key = currentMarginGroupKey();
        const vals = readEditorMargins();
        if (vals.length) groupMarginStore[key] = vals;
        else delete groupMarginStore[key];
    }

    function onMarginGroupChanged() {
        loadGroupMarginsIntoEditor(currentMarginGroupKey());
    }

    // Product of every margin factor that applies to this row (all-rows margins,
    // its group's margins, and any per-row margins for the variable). Returns a
    // multiplier, e.g. 1.155 for a combined +15.5%.
    function marginFactor(row, variableName) {
        let factor = 1;
        const group = cleanText(row.dataset.group).toLowerCase();
        (groupMarginStore['__all__'] || []).forEach((p) => {
            const n = toNumber(p, 0); if (n) factor *= (1 + n / 100);
        });
        if (group && groupMarginStore[group]) {
            groupMarginStore[group].forEach((p) => {
                const n = toNumber(p, 0); if (n) factor *= (1 + n / 100);
            });
        }
        for (const percent of getRowMargins(row, variableName)) {
            const p = toNumber(percent, 0);
            if (p !== 0) factor *= (1 + p / 100);
        }
        return factor;
    }

    function getRowMargins(row, variableName) {
        const state = rowMarginState.get(row);
        return state?.[variableName] || [];
    }

    function setRowMargins(row, variableName, values) {
        let state = rowMarginState.get(row);
        if (!state) {
            state = {};
            rowMarginState.set(row, state);
        }
        state[variableName] = values;
    }

    function applyMargins(value, row, variableName) {
        return value * marginFactor(row, variableName);
    }

    function readCsvFirstInputBase(row, variableName, secondValue) {
        const cell = getCell(row, variableName);
        if (!cell) return 0;

        // Backend renders calculated cells as their final result, not the raw
        // CSV value. For columns like weight = CSV weight × qty, using that
        // rendered result as the base would multiply by qty again in the
        // browser. Store the derived raw CSV value once and reuse it.
        if (cleanText(cell.dataset.calcInputBase)) {
            return toNumber(cell.dataset.calcInputBase, 0);
        }

        const renderedOrServerValue = toNumber(cell.dataset.calcBase ?? cell.dataset.calcRaw ?? cell.textContent, 0);
        const divisor = Number(secondValue);
        const rawValue = divisor && Number.isFinite(divisor)
            ? renderedOrServerValue / divisor
            : renderedOrServerValue;
        cell.dataset.calcInputBase = String(rawValue);
        return rawValue;
    }

    function calculateVariable(row, variableName, memo) {
        if (memo.has(variableName)) return memo.get(variableName);

        const col = getColumnByVariable(variableName);
        let value;
        if (!col || !cleanText(col.second_input)) {
            value = readBaseNumber(row, variableName);
        } else {
            const secondValue = resolveInput(row, col.second_input);
            const first = cleanText(col.first_input);
            const firstValue = Array.isArray(col.first_input)
                ? readCsvFirstInputBase(row, variableName, secondValue)
                : calculateVariable(row, first, memo);
            value = firstValue * secondValue;
        }

        if ((config.calculate || []).includes(variableName)) {
            value *= conversionFactor();
            value = applyMargins(value, row, variableName);
        }

        memo.set(variableName, value);
        return value;
    }

    function renderCell(cell, variableName, value) {
        // While the user is typing in this cell's inline editor, repainting would
        // remove the active <input> (no .calc-display-value span exists yet) and
        // throw away focus after the first keypress. Skip until the edit commits.
        if (cell.dataset.editing === '1') {
            cell.dataset.calcValue = String(value);
            return;
        }
        // PI mode: if this cell has a permanent always-on unit-price input, update
        // the input value directly. When the input is NOT focused, show the
        // formatted price WITH the currency unit ("1,234 Rial") so the cell is a
        // single box that carries the unit. When focused, the pi_columns focus
        // handler shows the raw number for editing, so we skip painting then.
        const piInput = cell.querySelector(':scope > input.pi-unit-input');
        if (piInput) {
            const piDec = getDecimals(variableName);
            cell.dataset.calcValue = String(value);
            // Keep dataset.raw as the pre-margin BASE so editing / clearing
            // per-row margin never rewrites data-calc-base from a painted price.
            const baseNum = toNumber(cell.dataset.calcBase ?? cell.dataset.calcRaw, 0);
            if (document.activeElement !== piInput) {
                if (isFinite(value) && value !== 0) {
                    let sym = currencySymbol(selectedCurrencyUnit());
                    if (!sym) {
                        const u = selectedCurrencyUnit();
                        sym = (u === 'usd') ? '$' : (u === 'eur') ? '€' : (u === 'rial' ? 'Rial' : String(u || '').toUpperCase());
                    }
                    const num = formatWithCommas(value, piDec);
                    piInput.value = num + ' ' + sym;
                } else {
                    piInput.value = '';
                }
                piInput.dataset.raw = (isFinite(baseNum) && baseNum !== 0) ? String(baseNum) : '';
            }
            return;
        }
        const viewType = (config.view || {})[variableName] || 'number';
        const decimals = getDecimals(variableName);
        const formatted = formatWithCommas(value, decimals);
        cell.dataset.calcValue = String(value);
        if (viewType === 'currency') {
            const symbol = currencySymbol(selectedCurrencyUnit());
            // Number stays on one line (no internal wrap even for 12 digits); the
            // unit may wrap to the next line.
            ensureDisplaySpan(cell).innerHTML =
                '<span class="calc-num">' + formatted + '</span>' +
                (symbol ? ' <span class="calc-unit">' + symbol + '</span>' : '');
        } else {
            ensureDisplaySpan(cell).textContent = formatted;
        }

        if (rowMarginVisible && (config.calculate || []).includes(variableName)) {
            ensureRowMarginPanel(cell, variableName);
        }
    }

    function refreshRow(row) {
        if (!row) return;
        const memo = new Map();
        for (const col of (config.columns || [])) {
            const variableName = col.variable;
            const cell = getCell(row, variableName);
            if (!cell) continue;
            const value = calculateVariable(row, variableName, memo);
            renderCell(cell, variableName, value);
        }
    }

    function refreshAll() {
        const tbl = table();
        if (!tbl) return;

        // Large uploads can contain thousands of rows.  Refresh in small
        // animation-frame batches so the browser keeps typing/scrolling smooth.
        const rows = allDataRows();
        const token = ++refreshAllToken;
        let index = 0;

        function paintBatch() {
            if (token !== refreshAllToken) return;
            const end = Math.min(index + REFRESH_BATCH_SIZE, rows.length);
            for (; index < end; index += 1) {
                refreshRow(rows[index]);
            }
            if (index < rows.length) {
                refreshAllFrame = requestAnimationFrame(paintBatch);
            } else {
                refreshAllFrame = null;
                // Let the proforma pricing layer recompute its grand total from
                // the freshly calculated TOTAL PRICE cells.
                document.dispatchEvent(new CustomEvent('ft-calc-refreshed'));
                buildMarginReport();
            }
        }

        if (refreshAllFrame) cancelAnimationFrame(refreshAllFrame);
        refreshAllFrame = requestAnimationFrame(paintBatch);
    }

    function scheduleRefreshAll() {
        // Coalesce rapid changes from margin/rate inputs into one async refresh.
        if (refreshAllFrame) cancelAnimationFrame(refreshAllFrame);
        refreshAllFrame = requestAnimationFrame(() => {
            refreshAllFrame = null;
            refreshAll();
        });
    }

    function initBaseValues() {
        const tbl = table();
        if (!tbl) return;
        allDataRows().forEach((row) => {
            row.querySelectorAll('td[data-calc-variable]').forEach((cell) => {
                if (!cell.dataset.calcBase) {
                    cell.dataset.calcBase = cell.dataset.calcRaw || cell.textContent.trim();
                }
            });
        });
    }

    function addGlobalMarginRow() {
        const list = document.getElementById('global-margin-list');
        if (!list) return;

        const row = document.createElement('div');
        row.className = 'global-margin-row';

        const input = document.createElement('input');
        input.className = 'global-margin-percent';
        input.type = 'text';
        input.inputMode = 'decimal';
        input.placeholder = 'Margin %';

        row.appendChild(input);
        list.appendChild(row);
    }

    function removeGlobalMarginRow() {
        const list = document.getElementById('global-margin-list');
        if (!list) return;
        const rows = list.querySelectorAll('.global-margin-row');
        if (rows.length > 1) rows[rows.length - 1].remove();
        else rows[0]?.querySelector('.global-margin-percent') && (rows[0].querySelector('.global-margin-percent').value = '');
    }

    function ensureRowMarginPanel(cell, variableName) {
        if (cell.querySelector('.row-margin-panel')) return;

        const row = cell.closest('tr');
        const panel = document.createElement('div');
        panel.className = 'row-margin-panel';
        panel.dataset.variable = variableName;

        function addInput(value = '') {
            const input = document.createElement('input');
            input.type = 'text';
            input.inputMode = 'decimal';
            input.placeholder = '%';
            input.value = value;
            input.className = 'row-margin-percent';
            input.style.width = '42px';
            panel.insertBefore(input, panel.querySelector('.row-margin-actions'));
        }

        const actions = document.createElement('span');
        actions.className = 'row-margin-actions';

        const add = document.createElement('button');
        add.type = 'button';
        add.textContent = '+';
        add.className = 'row-margin-add';
        add.addEventListener('click', (ev) => {
            if (ev) { ev.preventDefault(); ev.stopPropagation(); }
            addInput('');
            syncRowMarginPanel(row, variableName, panel);
            refreshRow(row);
            buildMarginReport();
            document.dispatchEvent(new CustomEvent('ft-calc-refreshed'));
        });

        const remove = document.createElement('button');
        remove.type = 'button';
        remove.textContent = '−';
        remove.className = 'row-margin-remove';
        remove.addEventListener('click', (ev) => {
            if (ev) { ev.preventDefault(); ev.stopPropagation(); }
            const inputs = panel.querySelectorAll('.row-margin-percent');
            if (inputs.length > 1) inputs[inputs.length - 1].remove();
            else if (inputs[0]) inputs[0].value = '';
            syncRowMarginPanel(row, variableName, panel);
            refreshRow(row);
            buildMarginReport();
            document.dispatchEvent(new CustomEvent('ft-calc-refreshed'));
        });

        panel.addEventListener('input', (ev) => {
            if (ev) { ev.stopPropagation(); }
            syncRowMarginPanel(row, variableName, panel);
            refreshRow(row);
            buildMarginReport();
            document.dispatchEvent(new CustomEvent('ft-calc-refreshed'));
        });
        panel.addEventListener('keydown', (ev) => { if (ev) ev.stopPropagation(); });
        panel.addEventListener('click', (ev) => { if (ev) ev.stopPropagation(); });
        panel.addEventListener('mousedown', (ev) => { if (ev) ev.stopPropagation(); });

        actions.appendChild(add);
        actions.appendChild(remove);
        panel.appendChild(actions);
        const existingValues = getRowMargins(row, variableName);
        if (existingValues.length) {
            existingValues.forEach((value) => addInput(value));
        } else {
            addInput('');
        }
        cell.style.position = 'relative';
        cell.appendChild(panel);
    }

    function syncRowMarginPanel(row, variableName, panel) {
        const values = Array.from(panel.querySelectorAll('.row-margin-percent'))
            .map((input) => input.value)
            .filter((v) => cleanText(v) !== '');
        setRowMargins(row, variableName, values);
    }

    function showRowMarginPanels() {
        const tbl = table();
        if (!tbl) return;
        // Operate on EVERY persistent row (virtual-scroll keeps all <tr> in
        // memory) so panels do not reappear/linger when the user scrolls.
        allDataRows().forEach((row) => {
            (config.calculate || []).forEach((variableName) => {
                const cell = getCell(row, variableName);
                if (cell) ensureRowMarginPanel(cell, variableName);
            });
        });
    }

    function hideRowMarginPanels() {
        allDataRows().forEach((row) => {
            row.querySelectorAll('.row-margin-panel').forEach((panel) => panel.remove());
        });
        // Safety net for any panel still attached in the live DOM.
        document.querySelectorAll('.row-margin-panel').forEach((panel) => panel.remove());
    }

    function qtyOfRow(row) {
        const c = row.querySelector('td[data-col-name="qty"]');
        const n = c ? toNumber(c.textContent, 0) : 0;
        return n > 0 ? n : 1;
    }

    function rowHasPerRowMargin(row) {
        const st = rowMarginState.get(row);
        if (!st) return false;
        return Object.keys(st).some((k) => (st[k] || []).some((v) => toNumber(v, 0) !== 0));
    }

    function fmtPct(p) {
        const sign = p > 0 ? '+' : '';
        return sign + (Math.round(p * 100) / 100).toLocaleString('en-US', { maximumFractionDigits: 2 }) + '%';
    }

    function fmtMoneyDelta(n) {
        const unit = selectedCurrencyUnit();
        const decimals = (unit === 'rial') ? 0 : 2;
        const abs = Math.abs(n);
        const num = formatWithCommas(abs, decimals);
        const sign = n > 0 ? '+' : (n < 0 ? '−' : '');
        const sym = currencySymbol(unit) || unitLabelOf(unit);
        return sign + num + (sym ? (' ' + sym) : '');
    }

    // Build the per-group margin report: each group the user gave margins to (or
    // that carries per-row margins) plus a final "all rows" line, showing the
    // margins applied and the weighted average price change vs the pre-margin
    // price. Per-row margins are included in the averages.
    function buildMarginReport() {
        const host = document.getElementById('gm-report');
        if (!host) return;

        const conv = conversionFactor();
        const groups = {}; // key -> { label, base, final, perRow }
        let allBase = 0, allFinal = 0;
        allDataRows().forEach((row) => {
            if (row.getAttribute('data-deleted') === '1') return;
            if (row.getAttribute('data-unsuppliable') === '1') return;
            const label = cleanText(row.dataset.group) || '—';
            const key = label.toLowerCase();
            const baseUnit = readBaseNumber(row, 'unit_price') * conv;
            // When Service Price is ON, UNIT SVC PRICE follows the same margins
            // as UNIT PRICE — include its base in the weighted report too.
            let baseSvc = 0;
            if (typeof window.PIServiceFeatureOn === 'function' && window.PIServiceFeatureOn()) {
                const comment = cleanText(row.getAttribute('data-service-comment'));
                if (comment && !/^(nan|none|<na>|null)$/i.test(comment)) {
                    const raw = toNumber(
                        row.getAttribute('data-service-price-raw')
                        || row.querySelector('td[data-col-name="SERVICE PRICE"] input')?.dataset?.raw
                        || '',
                        0
                    );
                    baseSvc = raw * conv;
                }
            }
            const w = (baseUnit + baseSvc) * qtyOfRow(row);
            const factor = marginFactor(row, 'unit_price');
            if (!groups[key]) groups[key] = { label, base: 0, final: 0, perRow: false };
            groups[key].base += w;
            groups[key].final += w * factor;
            if (rowHasPerRowMargin(row)) groups[key].perRow = true;
            allBase += w;
            allFinal += w * factor;
        });

        const lines = [];
        Object.keys(groups).sort().forEach((key) => {
            const g = groups[key];
            const stored = groupMarginStore[key] && groupMarginStore[key].length;
            if (!stored && !g.perRow) return; // no margins targeted at this group
            const pct = g.base > 0 ? (g.final / g.base - 1) * 100 : 0;
            const delta = g.final - g.base;
            const marginList = (groupMarginStore[key] || []).map((m) => m + '%').join(' + ')
                || (g.perRow ? 'per-row only' : '—');
            lines.push(
                '<tr><td class="gm-rep-grp">' + escapeHtml(g.label.toUpperCase()) + '</td>' +
                '<td class="gm-rep-mrg">' + escapeHtml(marginList) + '</td>' +
                '<td class="gm-rep-pct ' + (pct >= 0 ? 'up' : 'down') + '">' + fmtPct(pct) + '</td>' +
                '<td class="gm-rep-amt ' + (delta >= 0 ? 'up' : 'down') + '">' + escapeHtml(fmtMoneyDelta(delta)) + '</td></tr>'
            );
        });

        const allPct = allBase > 0 ? (allFinal / allBase - 1) * 100 : 0;
        const allDelta = allFinal - allBase;
        const allMargins = (groupMarginStore['__all__'] || []).map((m) => m + '%').join(' + ') || '—';

        host.hidden = false;
        host.innerHTML =
            '<table class="gm-rep-table"><thead><tr><th>Group</th><th>Margins</th><th>Avg change</th><th>Amount</th></tr></thead>' +
            '<tbody>' + (lines.join('') ||
                '<tr><td colspan="4" class="gm-rep-empty">No group margins yet.</td></tr>') + '</tbody>' +
            '<tfoot><tr><td class="gm-rep-grp">ALL ROWS</td><td class="gm-rep-mrg">' +
                escapeHtml(allMargins) + '</td><td class="gm-rep-pct ' + (allPct >= 0 ? 'up' : 'down') + '">' +
                fmtPct(allPct) + '</td><td class="gm-rep-amt ' + (allDelta >= 0 ? 'up' : 'down') + '">' +
                escapeHtml(fmtMoneyDelta(allDelta)) + '</td></tr></tfoot></table>';
    }
    window.CalcBuildMarginReport = buildMarginReport;

    // Serialize the calc state (currency conversion + margins) so the tool save
    // can persist it WITH the version, and it can be restored on edit / carried
    // into a new version. Row margins are keyed by the row's client "#".
    function serializeCalcState() {
        const rowMargins = {};
        allDataRows().forEach((row) => {
            const st = rowMarginState.get(row);
            if (!st) return;
            const clean = {};
            Object.keys(st).forEach((k) => {
                const vals = (st[k] || []).map((v) => toNumber(v, 0)).filter((v) => v !== 0);
                if (vals.length) clean[k] = vals;
            });
            if (!Object.keys(clean).length) return;
            const hashCell = row.querySelector('td[data-col-name="#"]');
            const no = hashCell ? (hashCell.getAttribute('data-raw-hash') ||
                (hashCell.querySelector('.client-no-text') || hashCell).textContent || '').replace(/[−+\s]/g, '').trim() : '';
            if (no) rowMargins[no] = clean;
        });
        const from = lockedFromUnit();
        const to = confirmedTo || from;
        return {
            from: from,
            to: to,
            rate: toNumber(document.getElementById('calc-convert-rate')?.value, 1),
            currency: selectedCurrencyUnit(),
            groupMargins: JSON.parse(JSON.stringify(groupMarginStore)),
            rowMargins,
        };
    }
    window.CalcSerializeState = serializeCalcState;

    // Restore a previously saved calc state (from the version being edited or
    // carried forward into a new version).
    function restoreSavedCalcState() {
        let saved = null;
        try {
            const el = document.getElementById('ft-saved-calc');
            if (el && el.textContent) saved = JSON.parse(el.textContent);
        } catch (_e) { saved = null; }
        if (!saved) {
            try { saved = (window.FT_TOOL_SAVE && window.FT_TOOL_SAVE.savedCalc) || null; } catch (_e2) { saved = null; }
        }
        if (!saved || typeof saved !== 'object') return;
        const fromSel = document.getElementById('calc-convert-from');
        const toSel = document.getElementById('calc-convert-to');
        const rateInp = document.getElementById('calc-convert-rate');
        ensureFromLocked();
        if (toSel && saved.to) {
            toSel.value = saved.to;
            confirmedTo = saved.to;
        }
        if (rateInp && saved.rate != null && Number(saved.rate) !== 1) setRateInputValue(rateInp, saved.rate);
        try {
            const from0 = lockedFromUnit();
            const to0 = (toSel && toSel.value) || from0;
            syncRateDisplay(from0, to0);
        } catch (_e3) { /* ignore */ }
        Object.keys(groupMarginStore).forEach((k) => delete groupMarginStore[k]);
        if (saved.groupMargins && typeof saved.groupMargins === 'object') {
            Object.keys(saved.groupMargins).forEach((k) => {
                const arr = (saved.groupMargins[k] || []).map((v) => toNumber(v, 0)).filter((v) => v !== 0);
                if (arr.length) groupMarginStore[k] = arr;
            });
        }
        // Per-row margins are restored once the rows exist.
        if (saved.rowMargins && typeof saved.rowMargins === 'object') {
            pendingRowMargins = saved.rowMargins;
            applyPendingRowMargins();
        }
    }

    let pendingRowMargins = null;
    function applyPendingRowMargins() {
        if (!pendingRowMargins) return;
        allDataRows().forEach((row) => {
            const hashCell = row.querySelector('td[data-col-name="#"]');
            const no = hashCell ? (hashCell.getAttribute('data-raw-hash') ||
                (hashCell.querySelector('.client-no-text') || hashCell).textContent || '').replace(/[−+\s]/g, '').trim() : '';
            if (no && pendingRowMargins[no]) {
                const st = {};
                Object.keys(pendingRowMargins[no]).forEach((k) => { st[k] = pendingRowMargins[no][k].slice(); });
                rowMarginState.set(row, st);
            }
        });
    }



    function normalizeRemarkToken(value) {
        return String(value || '').toLowerCase().replace(/[^a-z0-9]+/g, '');
    }

    function isExportHighlightSpan(node) {
        if (!node || node.nodeType !== 1 || node.tagName.toLowerCase() !== 'span') return false;
        const styleColor = String(node.getAttribute('style') || '').toLowerCase();
        const cls = String(node.getAttribute('class') || '').toLowerCase();
        return (
            styleColor.includes('#001aff') ||
            styleColor.includes('rgb(0, 26, 255)') ||
            styleColor.includes('color:blue') ||
            styleColor.includes('color: green') ||
            styleColor.includes('color:green') ||
            cls.includes('highlight-color')
        );
    }

    function hasKeptHighlightedValue(root, revisionNorm) {
        return Array.from(root.querySelectorAll('span')).some((span) => {
            if (!isExportHighlightSpan(span)) return false;
            const textNorm = normalizeRemarkToken(span.textContent);
            if (!textNorm) return false;
            // Values typed in Revision are changes, not remarks.  Remove them
            // from the exported Remark while keeping remark/rule highlights.
            if (revisionNorm && (revisionNorm.includes(textNorm) || textNorm.includes(revisionNorm))) return false;
            return true;
        });
    }

    function removeRevisionOnlyHighlights(root, revisionNorm) {
        Array.from(root.querySelectorAll('span')).forEach((span) => {
            if (!isExportHighlightSpan(span)) return;
            const textNorm = normalizeRemarkToken(span.textContent);
            if (revisionNorm && textNorm && (revisionNorm.includes(textNorm) || textNorm.includes(revisionNorm))) {
                span.remove();
            }
        });
    }

    function detectFinalTextSeparator(fragment) {
        const counts = new Map();
        Array.from(fragment.childNodes).forEach((node) => {
            if (node.nodeType !== Node.TEXT_NODE) return;
            const value = node.textContent || '';
            const trimmed = value.trim();
            const simpleSeparator = trimmed && !/[A-Za-z0-9\u0600-\u06FF]/.test(trimmed) && !/[\/()\[\]{}:]/.test(trimmed);
            const blankSeparator = !trimmed && value.length > 0 && value.length <= 6;
            if (!simpleSeparator && !blankSeparator) return;
            const key = value;
            counts.set(key, (counts.get(key) || 0) + 1);
        });

        let best = null;
        let bestCount = 0;
        counts.forEach((count, key) => {
            if (count > bestCount) {
                best = key;
                bestCount = count;
            }
        });
        return bestCount >= 1 ? best : null;
    }

    function textNodeIsSeparator(node, separator) {
        if (!separator || !node || node.nodeType !== Node.TEXT_NODE) return false;
        const value = node.textContent || '';
        if (!separator.trim()) return value === separator;
        return value.trim() === separator.trim();
    }


    function textNodeEndsWithSeparator(node, separator) {
        if (!separator || !node || node.nodeType !== Node.TEXT_NODE) return false;
        const value = node.textContent || '';
        if (!separator.trim()) return /\s+$/.test(value) && value.trim().length > 0;
        return value.trim().endsWith(separator.trim()) && value.trim() !== separator.trim();
    }

    function cleanExportSegmentText(value) {
        return String(value || '')
            .replace(/\s+/g, ' ')
            .replace(/\s+([,\-\/\)])/g, ' $1')
            .replace(/([\(\/\-])\s+/g, '$1 ')
            .trim();
    }

    function segmentTextFromNodes(nodes, revisionNorm) {
        const holder = document.createElement('span');
        nodes.forEach((node) => holder.appendChild(node.cloneNode(true)));
        removeRevisionOnlyHighlights(holder, revisionNorm);

        const highlightedTokens = [];

        function visit(node) {
            if (!node) return;

            if (node.nodeType === Node.TEXT_NODE) {
                highlightedTokens.push({
                    highlight: false,
                    text: node.textContent || '',
                });
                return;
            }

            if (node.nodeType !== Node.ELEMENT_NODE) return;

            if (isExportHighlightSpan(node)) {
                const textNorm = normalizeRemarkToken(node.textContent);
                if (textNorm && !(revisionNorm && (revisionNorm.includes(textNorm) || textNorm.includes(revisionNorm)))) {
                    highlightedTokens.push({
                        highlight: true,
                        text: node.textContent || '',
                    });
                }
                return;
            }

            Array.from(node.childNodes).forEach(visit);
        }

        Array.from(holder.childNodes).forEach(visit);

        const highlightIndexes = highlightedTokens
            .map((token, index) => token.highlight ? index : -1)
            .filter((index) => index >= 0);

        if (!highlightIndexes.length) return '';

        // If only one highlighted value exists in this final-text segment, export
        // only that exact highlighted value. This prevents black template words
        // such as ASTM/ASME/standard values from leaking into Remark.
        if (highlightIndexes.length === 1) {
            return cleanExportSegmentText(highlightedTokens[highlightIndexes[0]].text);
        }

        // When multiple highlighted values are inside one JSON arrange template,
        // keep only the highlighted values and the literal punctuation between
        // them. Example: <blue>ASTM A106</blue> / (<blue>Gr.B</blue>)
        // exports as ASTM A106 / (Gr.B). Non-highlighted words are skipped.
        const first = highlightIndexes[0];
        const last = highlightIndexes[highlightIndexes.length - 1];
        const parts = [];

        for (let i = first; i <= last; i += 1) {
            const token = highlightedTokens[i];
            if (token.highlight) {
                parts.push(token.text);
                continue;
            }

            const text = token.text || '';
            // Keep only connector/punctuation text between highlighted spans.
            // If a text node contains letters/digits, it is a black value and
            // must not be exported into Remark.
            if (text && !/[A-Za-z0-9\u0600-\u06FF]/.test(text)) {
                parts.push(text);
            }
        }

        return cleanExportSegmentText(parts.join(''));
    }


    function buildRemarkFromHighlights(row) {
        const finalCell = row.querySelector('td[data-col-name="Final Arranged Text"]');
        const remarkCell = row.querySelector('td[data-col-name="ریمارک"]');
        const revisionCell = row.querySelector('td[data-col-name="اصلاحیه"]');
        const fallbackRemark = cellExportText(remarkCell);
        if (!finalCell) return fallbackRemark;

        const revisionText = revisionCell?.querySelector('textarea')?.value || revisionCell?.textContent || '';
        const revisionNorm = normalizeRemarkToken(revisionText);
        const sourceHtml = finalCell.dataset.originalHtml || finalCell.innerHTML || '';
        const template = document.createElement('template');
        template.innerHTML = sourceHtml;
        const fragment = template.content;
        const separator = detectFinalTextSeparator(fragment) || ' , ';

        const pieces = [];
        let current = [];
        Array.from(fragment.childNodes).forEach((node) => {
            if (textNodeIsSeparator(node, separator)) {
                const piece = segmentTextFromNodes(current, revisionNorm);
                if (piece) pieces.push(piece);
                current = [];
            } else if (!current.length && textNodeEndsWithSeparator(node, separator)) {
                // The first text node is usually the product prefix plus the
                // separator, e.g. "pipe-".  It is not a remark value, so it is
                // intentionally discarded before collecting highlighted items.
                current = [];
            } else {
                current.push(node);
            }
        });
        const lastPiece = segmentTextFromNodes(current, revisionNorm);
        if (lastPiece) pieces.push(lastPiece);

        return pieces.length ? pieces.join(separator).replace(/\s+/g, ' ').trim() : fallbackRemark;
    }

    function cellExportText(cell) {
        if (!cell) return '';
        const textarea = cell.querySelector('textarea');
        if (textarea) return textarea.value || '';
        const input = cell.querySelector('input:not(.row-margin-percent)');
        if (input) return input.value || '';
        const display = cell.querySelector('.calc-display-value');
        if (display) return display.textContent || '';
        return cell.innerText.replace(/\s+/g, ' ').trim();
    }

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }


    function attachEvents() {
        const controlCard = card();
        if (!controlCard) return;

        controlCard.querySelectorAll('.global-margin-percent').forEach((input) => {
            input.type = 'text';
            input.inputMode = 'decimal';
        });

        controlCard.addEventListener('input', (event) => {
            if (event.target.matches('.global-margin-percent')) {
                commitEditorToStore();
                buildMarginReport();
                scheduleRefreshAll();
            }
        });

        controlCard.addEventListener('change', (event) => {
            if (event.target.matches('#calc-convert-to')) {
                onPendingToChanged();
                return;
            }
            if (event.target.matches('#global-margin-group')) {
                onMarginGroupChanged();
                buildMarginReport();
                scheduleRefreshAll();
            }
        });

        const confirmBtn = document.getElementById('calc-convert-confirm');
        if (confirmBtn) {
            confirmBtn.addEventListener('click', (e) => {
                e.preventDefault();
                confirmConversion();
            });
        }
        // Rate is display-only: block focus/click; cursor is not-allowed via CSS.
        const rateWrap = document.getElementById('calc-convert-rate-wrap');
        if (rateWrap) {
            rateWrap.addEventListener('mousedown', function (e) {
                e.preventDefault();
                e.stopPropagation();
            });
            rateWrap.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
            });
        }
        const rateLocked = document.getElementById('calc-convert-rate');
        if (rateLocked) {
            rateLocked.addEventListener('focus', function () {
                try { rateLocked.blur(); } catch (_e) {}
            });
        }
        const resetBtn = document.getElementById('calc-convert-reset');
        if (resetBtn) {
            resetBtn.addEventListener('click', (e) => {
                e.preventDefault();
                resetConversion();
            });
        }

        document.getElementById('add-global-margin')?.addEventListener('click', () => {
            addGlobalMarginRow();
            commitEditorToStore();
            buildMarginReport();
            scheduleRefreshAll();
        });

        document.getElementById('remove-global-margin')?.addEventListener('click', () => {
            removeGlobalMarginRow();
            commitEditorToStore();
            buildMarginReport();
            scheduleRefreshAll();
        });

        const rowMarginToggle = document.getElementById('toggle-row-margin');
        if (rowMarginToggle) {
            rowMarginToggle.addEventListener('change', () => {
                rowMarginVisible = rowMarginToggle.checked;
                if (rowMarginVisible) showRowMarginPanels();
                else hideRowMarginPanels();
                buildMarginReport();
                scheduleRefreshAll();
                // The panels change row heights — let the virtual-scroll engine
                // re-measure so the scrollbar length stays correct.
                if (window.VirtualScrollEngine && window.VirtualScrollEngine.refresh) {
                    setTimeout(() => window.VirtualScrollEngine.refresh(), 0);
                }
            });
        }
    }

    function bootstrap() {
        const controlCard = card();
        if (!controlCard) return;
        try {
            config = JSON.parse(controlCard.dataset.config || '{}');
        } catch (_err) {
            config = { view: {}, calculate: [], currency_units: [], columns: [] };
        }
        initBaseValues();
        restoreSavedCalcState();
        syncManagedFxRate(() => {
            syncConversionUi();
            scheduleRefreshAll();
        });
        attachEvents();
        buildGlobalMarginGroupCombo();
        buildMarginReport();
        scheduleRefreshAll();
    }

    // Build a searchable dropdown for the Global-margin Group, populated from the
    // groups present in the CURRENT table (same UX as the PI filter Group field).
    // Writes the chosen value into the hidden #global-margin-group <select> and
    // fires its change event so the existing margin logic keeps working.
    function buildGlobalMarginGroupCombo() {
        const host = document.getElementById('gm-group-combo');
        const sel = document.getElementById('global-margin-group');
        if (!host || !sel) return;

        function tableGroups() {
            const eng = window.VirtualScrollEngine;
            const rows = (eng && eng.getRows) ? eng.getRows() : [];
            const seen = {}, out = [];
            rows.forEach((tr) => {
                const g = (tr.getAttribute('data-group') || '').trim();
                if (g && !seen[g.toLowerCase()]) { seen[g.toLowerCase()] = 1; out.push(g); }
            });
            return out.sort();
        }

        // Rebuild the hidden <select> from the groups ACTUALLY present in the
        // table so its .value can always hold the chosen group (server-side
        // group_options may not match the live data-group values exactly).
        const liveGroups = tableGroups();
        sel.innerHTML = '<option value="__all__">All</option>' +
            liveGroups.map((g) => `<option value="${escapeHtml(g)}">${escapeHtml(g)}</option>`).join('');

        host.innerHTML = '<input type="text" class="gm-combo-input" placeholder="All groups…" autocomplete="off">'
                       + '<div class="gm-combo-menu" hidden></div>';
        const input = host.querySelector('input');
        const menu = host.querySelector('.gm-combo-menu');

        function items() {
            // "All" first, then each group in the current table.
            return [{ value: '__all__', label: 'All groups' }]
                .concat(liveGroups.map((g) => ({ value: g, label: g.toUpperCase() })));
        }
        function render(q) {
            q = (q || '').toLowerCase();
            const list = items().filter((it) => it.label.toLowerCase().indexOf(q) >= 0);
            menu.innerHTML = list.length
                ? list.map((it) => `<div data-val="${encodeURIComponent(it.value)}">${it.label}</div>`).join('')
                : '<div class="gm-combo-empty">No groups</div>';
        }
        function commit(val, label) {
            input.value = (val === '__all__') ? '' : label;
            input.dataset.committed = (val === '__all__') ? '' : '1';
            sel.value = val;
            sel.dispatchEvent(new Event('change', { bubbles: true }));
        }
        input.addEventListener('focus', () => { render(input.value); menu.hidden = false; });
        input.addEventListener('input', () => { render(input.value); menu.hidden = false; });
        input.addEventListener('blur', () => setTimeout(() => { menu.hidden = true; }, 150));
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Delete' || e.key === 'Backspace') {
                if (e.key === 'Delete' || !input.value || input.dataset.committed === '1') {
                    e.preventDefault(); menu.hidden = true; commit('__all__', '');
                }
            }
        });
        menu.addEventListener('mousedown', (e) => {
            const it = e.target.closest('[data-val]'); if (!it) return;
            commit(decodeURIComponent(it.getAttribute('data-val')), it.textContent.trim());
            menu.hidden = true;
        });
        // Default shows "All groups".
        commit('__all__', '');
    }

    document.addEventListener('DOMContentLoaded', bootstrap);

    window.CalcCurrentCurrency = selectedCurrencyUnit;
    window.CalcCurrencySymbol = currencySymbol;
    window.CalcCurrentDecimals = () => getDecimals('unit_price');
    // Used by service_price.js so UNIT SVC PRICE gets the same margin stack
    // (all-rows + group + per-row unit_price) as UNIT PRICE when Service is on.
    window.CalcMarginFactor = marginFactor;

    window.CalculationControls = {
        refreshRow,
        refreshAll,
        initBaseValues,
        refreshVisibleRows: (rows) => {
            (rows || visibleDataRows()).forEach((row) => {
                refreshRow(row);
                if (rowMarginVisible) {
                    (config.calculate || []).forEach((variableName) => {
                        const cell = getCell(row, variableName);
                        if (cell) ensureRowMarginPanel(cell, variableName);
                    });
                }
            });
        }
    };
})(window, document);
