/* service_price.js — PI only. Optional "Service Price" (قیمت خدمات) column.
 *
 * Flow (as specified):
 *   1. Toggle ON → assignment card appears; SERVICE PRICE column stays hidden
 *      until at least one row has a service comment attached.
 *   2. Click Client Description … FTCO Description to select rows (hand cursor).
 *      Item No / ban (not-suppliable) never selects for service.
 *   3. Selected rows show a bordered "service" tag inside FTCO Description
 *      (same chip language as NOT SUPPLIABLE).
 *   4. Attach comment → column appears; commented rows get an editable field
 *      like UNIT PRICE (same currency unit). Service × QTY shows as a second
 *      line under TOTAL PRICE. Strip: Subtotal → Total service → VAT → Grand.
 *
 * TOTAL PRICE base (unit×qty) stays in data-calc-value so Subtotal/VAT are
 * unaffected; service is summed separately into Total service price and
 * added only into Grand total.
 */
(function (window, document) {
  'use strict';

  function KIND() { return ((window.FT_TOOL_SAVE && window.FT_TOOL_SAVE.kind) || '').toUpperCase(); }

  var COL = 'SERVICE PRICE';
  var COL_TITLE = 'UNIT SVC PRICE';
  var BAD_COMMENT = /^(nan|none|<na>|null)$/i;
  var FTCO_COLS = ['Final Arranged Text', 'FTCO DISCRIPTION', 'FTCO DESCRIPTION'];
  // Only Client Description → FTCO Description. Item Code / # / ban are excluded
  // so Not Suppliable never also applies a service tag.
  var SELECTABLE_COLS = [
    'description', 'Description', 'CLIENT DESCRIPTION', 'CLIENT DISCRIPTION'
  ].concat(FTCO_COLS);

  var state = { on: false, picking: false, columnVisible: false };

  function eng() { return window.VirtualScrollEngine || null; }
  function allRows() { var e = eng(); return (e && e.getRows) ? e.getRows() : []; }
  function liveRows() {
    return allRows().filter(function (tr) {
      return tr.getAttribute('data-deleted') !== '1' && tr.getAttribute('data-unsuppliable') !== '1';
    });
  }

  function decPlaces() {
    if (window.CalcCurrentDecimals) {
      try { return window.CalcCurrentDecimals(); } catch (_e) {}
    }
    return (String(currencyUnit()).toLowerCase() === 'rial') ? 0 : 2;
  }
  function fmtNum(n) {
    if (!isFinite(n) || n === 0) return '';
    return n.toLocaleString('en-US', {
      minimumFractionDigits: decPlaces(),
      maximumFractionDigits: decPlaces()
    });
  }
  function toNum(s) {
    var n = parseFloat(String(s == null ? '' : s).replace(/[^0-9.\-]/g, ''));
    return isFinite(n) ? n : 0;
  }
  function currencyUnit() {
    if (window.CalcCurrentCurrency) {
      try { return window.CalcCurrentCurrency(); } catch (_e) {}
    }
    if (window.CalcSelectedCurrencyUnit) {
      try { return window.CalcSelectedCurrencyUnit(); } catch (_e) {}
    }
    return 'rial';
  }
  function conversionFactor() {
    return (window.CalcConversionFactor ? window.CalcConversionFactor() : 1);
  }
  /** Same margin stack as UNIT PRICE (all / group / per-row), but only while
   *  the Service Price toggle is ON. Conversion always applies. */
  function serviceDisplayFactor(tr) {
    var f = conversionFactor();
    if (!isFinite(f) || f <= 0) f = 1;
    if (state.on && window.CalcMarginFactor && tr) {
      try {
        var m = window.CalcMarginFactor(tr, 'unit_price');
        if (isFinite(m) && m > 0) f *= m;
      } catch (_e) {}
    }
    return f;
  }
  window.PIServiceFeatureOn = function () { return !!state.on; };
  function currencyLabel() {
    var u = currencyUnit();
    if (window.CalcCurrencySymbol) {
      try {
        var sym = window.CalcCurrencySymbol(u);
        if (sym) return sym;
      } catch (_e) {}
    }
    if (String(u).toLowerCase() === 'rial') return 'Rial';
    if (String(u).toLowerCase() === 'usd') return '$';
    if (String(u).toLowerCase() === 'eur') return '€';
    return String(u || '').toUpperCase();
  }

  function itemNoOf(tr) {
    if (!tr) return '';
    var cell = tr.querySelector('td[data-col-name="Item Code"] .ic-num') ||
               tr.querySelector('td[data-col-name="Item Code"]') ||
               tr.querySelector('td[data-col-name="#"]');
    if (!cell) return '';
    var num = cell.querySelector('.ic-num');
    return ((num ? num.textContent : cell.textContent) || '').trim();
  }
  function clientNoOf(tr) {
    if (!tr) return '';
    var cell = tr.querySelector('td[data-col-name="#"]');
    if (!cell) return itemNoOf(tr);
    var t = cell.querySelector('.client-no-text');
    return ((t ? t.textContent : cell.textContent) || '').replace(/[−+\s]/g, '').trim() || itemNoOf(tr);
  }

  function ftcoCell(tr) {
    if (!tr) return null;
    for (var i = 0; i < FTCO_COLS.length; i++) {
      var td = tr.querySelector('td[data-col-name="' + FTCO_COLS[i] + '"]');
      if (td) return td;
    }
    return null;
  }

  function anyAttached() {
    return allRows().some(function (tr) { return hasComment(tr); });
  }

  /* ---- SERVICE PRICE column ---- */
  function mkPriceInput() {
    var inp = document.createElement('input');
    inp.type = 'text';
    inp.inputMode = 'decimal';
    inp.autocomplete = 'off';
    // Do NOT reuse pi-unit-input — that class is owned by UNIT PRICE handlers
    // in pi_pricing.js / pi_columns.js and would fight service edits.
    inp.className = 'cell-input svc-price-input';
    // Empty when idle — never show a dash placeholder.
    inp.placeholder = '';
    return inp;
  }

  function anchorFor(tr) {
    return tr.querySelector('td[data-col-name="UNIT PRICE"]') ||
           tr.querySelector('td[data-calc-variable="unit_price"]');
  }

  /** Ensure every row has a real <input> in SERVICE PRICE (like UNIT PRICE). */
  function ensureServicePriceOnRow(tr) {
    if (!tr || !state.columnVisible) return null;
    var td = tr.querySelector('td[data-col-name="' + COL + '"]');
    if (!td) {
      td = document.createElement('td');
      td.className = 'col-SERVICE-PRICE svc-locked';
      td.setAttribute('data-col-name', COL);
      td.setAttribute('data-display-name', COL_TITLE);
      var anchor = anchorFor(tr);
      if (anchor) tr.insertBefore(td, anchor.nextSibling);
      else tr.appendChild(td);
    }
    var inp = td.querySelector('input.svc-price-input');
    if (!inp) {
      // Server-rendered empty cell (or a cell that lost its input) — wipe
      // leftover text and install a fresh always-on field.
      td.textContent = '';
      inp = mkPriceInput();
      inp.disabled = true;
      td.appendChild(inp);
    }
    // Drop legacy class if an older build left it on the input.
    if (inp.classList.contains('pi-unit-input')) inp.classList.remove('pi-unit-input');
    wireSvcInput(inp);
    return td;
  }

  function ensureServicePriceInTemplate() {
    if (!state.columnVisible) return;
    var tpl = document.getElementById('raw-excel-template');
    if (!tpl) return;
    // Prefer live VSE rows; template.content is a separate copy and is not
    // what the user sees. Still update it so any late clone stays consistent.
    var root = tpl.content || tpl;
    try {
      root.querySelectorAll('tr.row').forEach(ensureServicePriceOnRow);
    } catch (_e) {}
  }

  function ensureHeaderCell() {
    if (!state.columnVisible) return;
    var headRow = document.getElementById('virtual-scroll-header-row') ||
                  document.querySelector('#virtual-scroll-table thead tr') ||
                  document.querySelector('thead tr');
    if (!headRow) return;
    var existing = headRow.querySelector('th[data-col-name="' + COL + '"]');
    if (!existing) {
      var unitTh = headRow.querySelector('th[data-col-name="UNIT PRICE"]');
      var th = document.createElement('th');
      th.setAttribute('data-col-name', COL);
      th.setAttribute('data-display-name', COL_TITLE);
      th.textContent = COL_TITLE;
      if (unitTh) headRow.insertBefore(th, unitTh.nextSibling);
      else headRow.appendChild(th);
    } else {
      existing.setAttribute('data-display-name', COL_TITLE);
      existing.textContent = COL_TITLE;
    }
    // Keep header cell order aligned with the first data row (same helper
    // pattern as pi_columns.js after injecting TIME).
    syncHeaderOrderFromFirstRow();
  }

  function syncHeaderOrderFromFirstRow() {
    var theadRow = document.getElementById('virtual-scroll-header-row');
    var first = allRows()[0];
    if (!theadRow || !first) return;
    var byName = {};
    Array.prototype.forEach.call(theadRow.querySelectorAll('th'), function (th) {
      var n = th.getAttribute('data-col-name') || '';
      if (n) byName[n] = th;
    });
    var ordered = [];
    var used = {};
    Array.prototype.forEach.call(first.querySelectorAll('td'), function (td) {
      var n = td.getAttribute('data-col-name') || '';
      if (n && byName[n] && !used[n]) {
        ordered.push(byName[n]);
        used[n] = true;
      }
    });
    Object.keys(byName).forEach(function (n) {
      if (!used[n]) ordered.push(byName[n]);
    });
    ordered.forEach(function (th) { theadRow.appendChild(th); });
  }

  function refreshColumnLayout() {
    var table = document.getElementById('virtual-scroll-table');
    if (table && window.enableResizableColumns) {
      // Allow a full recompute so SERVICE PRICE gets a real % width like UNIT PRICE.
      delete table.dataset.resizableReady;
      window.enableResizableColumns(table);
    }
    if (window.VirtualScrollEngine && window.VirtualScrollEngine.refresh) {
      window.VirtualScrollEngine.refresh();
    }
  }

  function showSvcColumn() {
    state.columnVisible = true;
    document.body.classList.add('svc-col-visible');
    // 1) Inject cells + inputs on every live row first (source of truth).
    allRows().forEach(ensureServicePriceOnRow);
    // 2) Header to match.
    ensureHeaderCell();
    // 3) Template copy (best-effort).
    ensureServicePriceInTemplate();
    // 4) Unlock rows that already have a service comment.
    allRows().forEach(syncRowLock);
    document.querySelectorAll('td[data-col-name="' + COL + '"], th[data-col-name="' + COL + '"]')
      .forEach(function (el) { el.style.display = ''; });
    // 5) Recompute widths + re-render so the new column is actually visible
    //    and editable (table-layout:fixed + stale colspan was collapsing it).
    refreshColumnLayout();
    // syncRowLock again after refresh (onRender may re-touch rows).
    allRows().forEach(syncRowLock);
  }

  function hideSvcColumn() {
    state.columnVisible = false;
    document.body.classList.remove('svc-col-visible');
    document.querySelectorAll('td[data-col-name="' + COL + '"], th[data-col-name="' + COL + '"]')
      .forEach(function (el) { el.style.display = 'none'; });
    // Give CLIENT / FTCO / REMARK back the % space that SERVICE PRICE held.
    refreshColumnLayout();
  }

  function anyPriced() {
    return allRows().some(function (tr) {
      if (!hasComment(tr)) return false;
      if (servicePriceRaw(tr) > 0) return true;
      var td = tr.querySelector('td[data-col-name="' + COL + '"] input');
      return !!(td && toNum(td.dataset.raw || td.value) > 0);
    });
  }

  function shouldKeepColumn() {
    return anyAttached() || anyPriced();
  }

  function syncColumnVisibility() {
    if (shouldKeepColumn()) showSvcColumn();
    else hideSvcColumn();
  }

  function turnOff() {
    // Close the assignment workflow/card. If any service data exists, keep
    // the SERVICE PRICE column visible so prices stay editable.
    state.on = false;
    setPicking(false);
    var card = document.getElementById('svc-card');
    if (card) card.hidden = true;
    if (shouldKeepColumn()) showSvcColumn();
    else hideSvcColumn();
    // Toggle OFF → drop margin effect on UNIT SVC PRICE (conversion stays).
    recomputeAllTotals();
  }

  function cleanComment(raw) {
    var t = String(raw || '').trim();
    if (!t || BAD_COMMENT.test(t)) return '';
    return t;
  }

  function hasComment(tr) {
    return !!(tr && cleanComment(tr.getAttribute('data-service-comment')));
  }

  function paintSvcInput(inp) {
    if (!inp) return;
    var raw = toNum(inp.dataset.raw != null ? inp.dataset.raw : inp.value);
    if (!raw) {
      inp.value = '';
      inp.dataset.raw = '';
      return;
    }
    inp.dataset.raw = String(raw);
    if (document.activeElement === inp) {
      // Edit the base number (pre-conversion / pre-margin), same as UNIT PRICE.
      inp.value = String(raw);
    } else {
      var tr = inp.closest('tr');
      var display = raw * serviceDisplayFactor(tr);
      inp.value = fmtNum(display) + ' ' + currencyLabel();
    }
  }

  function wireSvcInput(inp) {
    if (!inp || inp.dataset.svcWired === '1') return;
    inp.dataset.svcWired = '1';
    inp.addEventListener('focus', function () {
      var raw = toNum(inp.dataset.raw != null ? inp.dataset.raw : inp.value);
      inp.value = raw ? String(raw) : '';
    });
    inp.addEventListener('blur', function () {
      var raw = toNum(inp.value);
      inp.dataset.raw = raw ? String(raw) : '';
      var tr = inp.closest('tr');
      if (tr) tr.setAttribute('data-service-price-raw', inp.dataset.raw || '');
      paintSvcInput(inp);
      if (tr) recomputeRowTotal(tr);
      if (window.PIRefreshCalc) window.PIRefreshCalc();
    });
    inp.addEventListener('input', function () {
      var tr = inp.closest('tr');
      var raw = toNum(inp.value);
      inp.dataset.raw = raw ? String(raw) : '';
      if (tr) {
        tr.setAttribute('data-service-price-raw', inp.dataset.raw || '');
        recomputeRowTotal(tr);
      }
    });
  }

  function syncRowLock(tr) {
    if (!tr || !state.columnVisible) return;
    var td = ensureServicePriceOnRow(tr);
    if (!td) return;
    var input = td.querySelector('input.svc-price-input');
    if (!input) {
      td.textContent = '';
      input = mkPriceInput();
      td.appendChild(input);
      wireSvcInput(input);
    }
    var unlocked = hasComment(tr) && tr.getAttribute('data-deleted') !== '1'
      && tr.getAttribute('data-unsuppliable') !== '1'
      && tr.getAttribute('data-remark-cleared') !== '1'
      && tr.getAttribute('data-brand-cleared') !== '1';
    td.classList.toggle('svc-locked', !unlocked);
    input.disabled = !unlocked;
    input.readOnly = !unlocked;
    if (!unlocked) {
      if (input.value || input.dataset.raw) {
        if (!tr.hasAttribute('data-saved-svc') && (input.dataset.raw || input.value)) {
          tr.setAttribute('data-saved-svc', input.dataset.raw || input.value || '');
        }
        input.value = '';
        input.dataset.raw = '';
      }
      tr.removeAttribute('data-service-price-raw');
      recomputeRowTotal(tr);
    } else {
      var saved = tr.getAttribute('data-saved-svc') || tr.getAttribute('data-service-price-raw');
      if (saved != null && saved !== '' && !input.dataset.raw) {
        input.dataset.raw = saved;
        tr.setAttribute('data-service-price-raw', saved);
      }
      paintSvcInput(input);
    }
    syncAttachedTag(tr);
  }

  /* ---- TOTAL PRICE: base on top, service line below ---- */
  function totalCell(tr) {
    return tr.querySelector('td[data-calc-variable="total_price"]') ||
           tr.querySelector('td[data-col-name="TOTAL PRICE"]');
  }
  function qtyOf(tr) {
    var c = tr.querySelector('td[data-col-name="qty"]') || tr.querySelector('td[data-col-name="Qty"]');
    var n = c ? toNum(c.textContent) : 0;
    return n > 0 ? n : 1;
  }
  function servicePriceRaw(tr) {
    var td = tr.querySelector('td[data-col-name="' + COL + '"]');
    var input = td && td.querySelector('input');
    var raw = tr.getAttribute('data-service-price-raw');
    if ((raw == null || raw === '') && input) {
      raw = input.dataset.raw != null ? input.dataset.raw : input.value;
    }
    return toNum(raw);
  }

  function readTotalDisplay(td) {
    var n = parseFloat((td.getAttribute('data-calc-value') || td.getAttribute('data-calc-raw') ||
                        (td.textContent || '')).replace(/[^0-9.\-]/g, ''));
    return isFinite(n) ? n : 0;
  }

  function paintDualTotal(td, baseTotal, svcLine) {
    if (!td) return;
    // Keep data-calc-value as BASE only so Subtotal/VAT ignore service.
    td.setAttribute('data-calc-value', String(baseTotal));
    td.setAttribute('data-svc-line', svcLine > 0 ? String(svcLine) : '');

    var wrap = td.querySelector(':scope > .svc-total-wrap');
    if (!wrap) {
      // Preserve engine markup when possible; otherwise rebuild.
      var existing = td.querySelector('.calc-display-value, .calc-num');
      wrap = document.createElement('div');
      wrap.className = 'svc-total-wrap';
      var base = document.createElement('div');
      base.className = 'svc-total-base';
      if (existing && existing.classList.contains('calc-display-value')) {
        base.appendChild(existing.cloneNode(true));
      } else {
        base.innerHTML = '<span class="calc-num"></span> <span class="calc-unit"></span>';
      }
      var svc = document.createElement('div');
      svc.className = 'svc-total-svc';
      svc.hidden = true;
      svc.innerHTML = '<span class="calc-num"></span> <span class="calc-unit"></span>';
      wrap.appendChild(base);
      wrap.appendChild(svc);
      td.textContent = '';
      td.appendChild(wrap);
    }

    var baseNum = wrap.querySelector('.svc-total-base .calc-num') ||
                  wrap.querySelector('.svc-total-base .calc-display-value .calc-num');
    var baseUnit = wrap.querySelector('.svc-total-base .calc-unit');
    var baseDisp = wrap.querySelector('.svc-total-base .calc-display-value');
    var formatted = fmtNum(baseTotal) || '0';
    var unit = currencyLabel();
    if (baseDisp && !baseNum) {
      baseDisp.innerHTML = '<span class="calc-num">' + formatted + '</span>' +
        (unit ? ' <span class="calc-unit">' + unit + '</span>' : '');
    } else {
      if (baseNum) baseNum.textContent = formatted;
      if (baseUnit) baseUnit.textContent = unit;
    }

    var svcEl = wrap.querySelector('.svc-total-svc');
    if (svcEl) {
      if (svcLine > 0) {
        svcEl.hidden = false;
        var sn = svcEl.querySelector('.calc-num');
        var su = svcEl.querySelector('.calc-unit');
        if (sn) sn.textContent = fmtNum(svcLine);
        if (su) su.textContent = unit;
      } else {
        svcEl.hidden = true;
      }
    }
  }

  function clearDualTotalOverlay(td) {
    if (!td) return;
    td.removeAttribute('data-svc-line');
    var wrap = td.querySelector(':scope > .svc-total-wrap');
    if (!wrap) return;
    // Leave data-calc-value; next calc refresh will repaint normally.
    var svcEl = wrap.querySelector('.svc-total-svc');
    if (svcEl) svcEl.hidden = true;
  }

  function recomputeRowTotal(tr) {
    var td = totalCell(tr);
    if (!td) return;
    var svc = hasComment(tr) ? servicePriceRaw(tr) : 0;
    var factor = serviceDisplayFactor(tr);
    var svcLine = svc > 0 ? (svc * factor * qtyOf(tr)) : 0;

    // Capture engine base once per refresh cycle.
    if (td.dataset.svcAdjusted !== '1') {
      td.dataset.svcBaseTotal = String(readTotalDisplay(td));
    }
    var baseTotal = parseFloat(td.dataset.svcBaseTotal);
    if (!isFinite(baseTotal)) baseTotal = readTotalDisplay(td);

    if (svcLine > 0) {
      paintDualTotal(td, baseTotal, svcLine);
      td.dataset.svcAdjusted = '1';
    } else {
      // Restore base-only value; do not leave inflated totals.
      td.setAttribute('data-calc-value', String(baseTotal));
      clearDualTotalOverlay(td);
      delete td.dataset.svcAdjusted;
      // If we previously replaced markup, put a simple display back.
      if (td.querySelector(':scope > .svc-total-wrap')) {
        paintDualTotal(td, baseTotal, 0);
      }
    }
  }

  function serviceTotalSum() {
    var sum = 0;
    liveRows().forEach(function (tr) {
      // liveRows already skips deleted + NOT SUPPLIABLE.
      if (!hasComment(tr)) return;
      var svc = servicePriceRaw(tr);
      if (!svc) return;
      sum += svc * serviceDisplayFactor(tr) * qtyOf(tr);
    });
    return sum;
  }
  window.PIServiceTotalSum = serviceTotalSum;

  function updateServiceTotalPill() {
    var pill = document.getElementById('pi-svc-total-pill');
    var out = document.getElementById('pi-svc-total');
    if (!pill || !out) return;
    var sum = serviceTotalSum();
    var any = sum > 0 || anyAttached();
    // Show pill whenever column is in use (even if prices still 0).
    pill.hidden = !anyAttached();
    out.textContent = (fmtNum(sum) || '0') + ' ' + currencyLabel();
  }

  function recomputeAllTotals() {
    // Reset per-row adjustment flags so we re-capture fresh engine bases.
    allRows().forEach(function (tr) {
      var td = totalCell(tr);
      if (td) {
        delete td.dataset.svcAdjusted;
        delete td.dataset.svcBaseTotal;
      }
      // Repaint UNIT SVC PRICE so conversion + (optional) margins show live.
      var inp = tr.querySelector('input.svc-price-input');
      if (inp && document.activeElement !== inp) paintSvcInput(inp);
    });
    liveRows().forEach(recomputeRowTotal);
    updateServiceTotalPill();
    // Ask pi_pricing to rebuild Subtotal / Total service / VAT / Grand.
    if (window.PIRecomputeGrandOnly) window.PIRecomputeGrandOnly();
  }

  /* ---- Selection / service tag on FTCO Description ---- */
  function selectableCellFor(target) {
    if (!target || !target.closest) return null;
    // Never treat Item No / ban / flag clicks as service selection.
    if (target.closest('.ic-flag, .ic-flag-ico, .ic-box, .unsup-btn, button, a')) return null;
    var td = target.closest('td[data-col-name]');
    if (!td) return null;
    var name = td.getAttribute('data-col-name') || '';
    for (var i = 0; i < SELECTABLE_COLS.length; i++) {
      if (SELECTABLE_COLS[i] === name) return td;
    }
    return null;
  }

  function setRowSelected(tr, on) {
    if (!tr) return;
    tr.classList.toggle('svc-row-selected', !!on);
    syncPickTag(tr, !!on);
    updatePickedCount();
  }

  function syncPickTag(tr, on) {
    var host = ftcoCell(tr);
    if (!host) return;
    var mark = host.querySelector(':scope > .svc-ftco-tag.svc-pick');
    if (on) {
      if (!mark) {
        mark = document.createElement('span');
        mark.className = 'svc-ftco-tag svc-pick';
        mark.textContent = 'SERVICE';
        host.appendChild(mark);
      }
    } else if (mark) {
      mark.remove();
    }
  }

  function syncAttachedTag(tr) {
    var host = ftcoCell(tr);
    if (!host) return;
    var mark = host.querySelector(':scope > .svc-ftco-tag.svc-attached');
    if (hasComment(tr)) {
      tr.classList.add('row-service');
      if (!mark) {
        mark = document.createElement('span');
        mark.className = 'svc-ftco-tag svc-attached';
        mark.textContent = 'SERVICE';
        host.appendChild(mark);
      }
    } else {
      tr.classList.remove('row-service');
      if (mark) mark.remove();
    }
  }

  function toggleRowSelected(tr) {
    if (!tr) return;
    setRowSelected(tr, !tr.classList.contains('svc-row-selected'));
  }

  function selectedRows() {
    return liveRows().filter(function (tr) {
      return tr.classList.contains('svc-row-selected');
    });
  }

  function setPicking(on) {
    state.picking = on;
    document.body.classList.toggle('svc-picking', on);
    if (!on) {
      liveRows().forEach(function (tr) { setRowSelected(tr, false); });
    }
    updatePickedCount();
  }

  function wireCellSelection() {
    var table = document.getElementById('virtual-scroll-table');
    if (!table || table.dataset.svcCellSelectionWired === '1') return;
    table.dataset.svcCellSelectionWired = '1';
    table.addEventListener('mousedown', function (e) {
      if (!state.picking) return;
      // Hard-exclude Item Code / ban affordances even if nested oddly.
      if (e.target && e.target.closest &&
          e.target.closest('td[data-col-name="Item Code"], td[data-col-name="#"], .ic-flag, .unsup-btn')) {
        return;
      }
      var td = selectableCellFor(e.target);
      if (!td) return;
      var tr = td.closest('tr');
      if (!tr || tr.getAttribute('data-deleted') === '1' || tr.getAttribute('data-unsuppliable') === '1') return;
      e.preventDefault();
      e.stopPropagation();
      toggleRowSelected(tr);
    }, true);
  }

  function updatePickedCount() {
    var el = document.getElementById('svc-picked-count');
    if (el) el.textContent = selectedRows().length + ' row(s) selected';
  }

  function buildCard() {
    if (document.getElementById('svc-card')) return;
    var anchor = document.getElementById('excel-table-container');
    if (!anchor) return;
    var card = document.createElement('div');
    card.id = 'svc-card';
    card.className = 'svc-card';
    card.hidden = true;
    card.innerHTML =
      '<div class="svc-left">' +
        '<div class="svc-title">Attach a service comment</div>' +
        '<div class="svc-row-picker">' +
          '<button type="button" class="svc-chip-btn" id="svc-select-all"><i class="fa-solid fa-check-double"></i><span>Select all rows</span></button>' +
          '<button type="button" class="svc-chip-btn svc-chip-btn-muted" id="svc-select-none"><i class="fa-solid fa-xmark"></i><span>Clear selection</span></button>' +
          '<span class="svc-picked-count" id="svc-picked-count">0 row(s) selected</span>' +
        '</div>' +
        '<textarea id="svc-comment-input" rows="2" placeholder="e.g. Galvanized coating…"></textarea>' +
        '<div id="svc-error" class="svc-error" hidden></div>' +
        '<div class="svc-actions">' +
          '<button type="button" class="btn btn-sm" id="svc-clear-selected">Clear</button>' +
          '<button type="button" class="btn btn-sm btn-primary" id="svc-confirm">Attach</button>' +
        '</div>' +
      '</div>' +
      '<div class="svc-right">' +
        '<div class="svc-title">Rows with a service comment</div>' +
        '<ul id="svc-list" class="svc-list"></ul>' +
      '</div>';
    anchor.parentNode.insertBefore(card, anchor);
  }

  function rebuildList() {
    var list = document.getElementById('svc-list');
    if (!list) return;
    list.innerHTML = '';
    var groups = [];
    var byText = {};
    liveRows().forEach(function (tr) {
      if (!hasComment(tr)) return;
      var text = cleanComment(tr.getAttribute('data-service-comment'));
      if (!text) return;
      var g = byText[text];
      if (!g) {
        g = { text: text, rows: [] };
        byText[text] = g;
        groups.push(g);
      }
      g.rows.push(tr);
    });
    if (!groups.length) {
      var empty = document.createElement('div');
      empty.className = 'svc-empty';
      empty.textContent = 'No rows have a service comment yet.';
      list.appendChild(empty);
      return;
    }
    groups.forEach(function (g) {
      var nums = g.rows.map(clientNoOf);
      nums.sort(function (a, b) {
        var na = parseFloat(a), nb = parseFloat(b);
        if (isFinite(na) && isFinite(nb)) return na - nb;
        return String(a).localeCompare(String(b));
      });
      var li = document.createElement('li');
      li.innerHTML =
        '<span class="svc-item">#' + nums.join(', ') + '</span>' +
        '<span class="svc-text"></span>' +
        '<button type="button" class="svc-row-clear" title="Clear this comment from all listed rows"><i class="fa-solid fa-xmark"></i></button>';
      li.querySelector('.svc-text').textContent = g.text;
      li.querySelector('.svc-row-clear').addEventListener('click', function () {
        g.rows.forEach(function (tr) { clearComment(tr); });
        syncColumnVisibility();
        recomputeAllTotals();
        rebuildList();
      });
      list.appendChild(li);
    });
  }

  function attachComment(tr, text) {
    var t = cleanComment(text);
    if (!t) {
      clearComment(tr);
      return;
    }
    tr.setAttribute('data-service-comment', t);
    syncRowLock(tr);
  }
  function clearComment(tr) {
    tr.removeAttribute('data-service-comment');
    tr.removeAttribute('data-service-price-raw');
    tr.removeAttribute('data-row-service-raw');
    syncRowLock(tr);
  }

  function showError(msg) {
    var el = document.getElementById('svc-error');
    if (!el) return;
    el.textContent = msg;
    el.hidden = false;
  }
  function clearError() {
    var el = document.getElementById('svc-error');
    if (el) el.hidden = true;
  }

  function wireCard() {
    var card = document.getElementById('svc-card');
    if (!card || card.dataset.svcWired === '1') return;
    card.dataset.svcWired = '1';

    var selAll = document.getElementById('svc-select-all');
    var selNone = document.getElementById('svc-select-none');
    var confirmBtn = document.getElementById('svc-confirm');
    var clearBtn = document.getElementById('svc-clear-selected');
    var input = document.getElementById('svc-comment-input');

    if (selAll) selAll.addEventListener('click', function () {
      liveRows().forEach(function (tr) { setRowSelected(tr, true); });
    });
    if (selNone) selNone.addEventListener('click', function () {
      liveRows().forEach(function (tr) { setRowSelected(tr, false); });
    });
    if (confirmBtn) confirmBtn.addEventListener('click', function () {
      var text = ((input && input.value) || '').trim();
      var picked = selectedRows();
      if (!picked.length) {
        showError('Select at least one row first.');
        return;
      }
      if (!text) {
        showError('Enter the service comment before confirming.');
        if (input) input.focus();
        return;
      }
      picked.forEach(function (tr) { attachComment(tr, text); });
      picked.forEach(function (tr) { setRowSelected(tr, false); });
      showSvcColumn();
      clearError();
      if (input) input.value = '';
      recomputeAllTotals();
      rebuildList();
      updatePickedCount();
      if (window.PIRefreshCalc) window.PIRefreshCalc();
      document.dispatchEvent(new CustomEvent('ft-rows-changed'));
    });
    if (clearBtn) clearBtn.addEventListener('click', function () {
      var picked = selectedRows();
      if (!picked.length) { showError('Select at least one row first.'); return; }
      picked.forEach(function (tr) { clearComment(tr); });
      picked.forEach(function (tr) { setRowSelected(tr, false); });
      syncColumnVisibility();
      clearError();
      recomputeAllTotals();
      rebuildList();
      updatePickedCount();
    });
    wireCellSelection();
  }

  function turnOn(silent) {
    state.on = true;
    buildCard();
    var card = document.getElementById('svc-card');
    if (card) card.hidden = false;
    wireCard();
    rebuildList();
    setPicking(true);
    syncColumnVisibility();
    // Margins now apply to UNIT SVC PRICE — always refresh displays + totals.
    recomputeAllTotals();
  }

  function injectToggle() {
    var host = document.getElementById('pi-panel-pricing');
    if (!host || document.getElementById('svc-toggle-row')) return;
    var row = document.createElement('div');
    row.id = 'svc-toggle-row';
    row.className = 'svc-toggle-row';
    row.innerHTML =
      '<label class="ft-switch"><input type="checkbox" id="svc-toggle-input">' +
        '<span class="ft-switch-slider"></span></label>' +
      '<span class="svc-toggle-label">Service Price</span>' +
      '<span class="svc-toggle-hint">Adds a priced, commented service line to selected rows.</span>';
    host.insertBefore(row, host.firstChild);
    var input = document.getElementById('svc-toggle-input');
    if (!input) return;
    input.addEventListener('change', function () {
      if (input.checked) turnOn(false);
      else turnOff();
    });
    // If saved service data exists, reflect the feature as available.
    if (state.columnVisible || shouldKeepColumn()) input.checked = true;
  }

  function boot() {
    if (KIND() !== 'PI') return;
    if (window.FT_TOOL_SAVE && window.FT_TOOL_SAVE.hidePricing) return;

    // Restore per-row service comment / raw price from server-rendered attrs
    // (set by views.dataframe_to_html_with_ids after a prior save).
    // Strip bogus "nan" comments left by older pandas NaN → str() saves.
    allRows().forEach(function (tr) {
      var c = cleanComment(tr.getAttribute('data-service-comment'));
      if (c) tr.setAttribute('data-service-comment', c);
      else {
        tr.removeAttribute('data-service-comment');
        tr.removeAttribute('data-service-price-raw');
        tr.removeAttribute('data-row-service-raw');
        syncAttachedTag(tr);
        return;
      }
      var raw = tr.getAttribute('data-service-price-raw') ||
                tr.getAttribute('data-row-service-raw') || '';
      if (raw) tr.setAttribute('data-service-price-raw', raw);
      // If the cell already has a saved SERVICE PRICE text, seed the raw.
      if (!raw) {
        var td = tr.querySelector('td[data-col-name="' + COL + '"]');
        if (td) {
          var n = toNum(td.getAttribute('data-calc-base') || td.textContent || '');
          if (n > 0) tr.setAttribute('data-service-price-raw', String(n));
        }
      }
    });

    var pre = anyAttached();
    if (pre) {
      state.on = true;
      buildCard();
      wireCard();
      rebuildList();
      showSvcColumn();
      allRows().forEach(syncRowLock);
      // Toggle ON ⇒ assignment card stays visible (do not require re-toggle).
      var card = document.getElementById('svc-card');
      if (card) card.hidden = false;
      setPicking(true);
    }
    var tries = 0;
    var iv = setInterval(function () {
      tries += 1;
      if (document.getElementById('pi-panel-pricing')) {
        injectToggle();
        clearInterval(iv);
      } else if (tries > 40) {
        clearInterval(iv);
      }
    }, 50);

    document.addEventListener('ft-calc-refreshed', recomputeAllTotals);
    if (eng() && eng().onRender) {
      eng().onRender(function (allR, start, end) {
        for (var i = start; i < end; i++) {
          var tr = allR[i];
          if (!tr) continue;
          if (state.columnVisible) {
            ensureServicePriceOnRow(tr);
            syncRowLock(tr);
          }
          if (tr.classList.contains('svc-row-selected')) syncPickTag(tr, true);
          if (hasComment(tr)) syncAttachedTag(tr);
        }
      });
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    setTimeout(boot, 0);
  });
})(window, document);
