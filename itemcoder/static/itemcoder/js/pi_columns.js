/* pi_columns.js — PI form only.
 *
 * 1. Injects BRAND and TIME columns into the row template (before the virtual
 *    scroll engine processes it).
 * 2. After the engine has created all row elements, injects an always-on
 *    <input class="pi-unit-input"> into every UNIT PRICE cell so the user can
 *    type prices directly without needing to click first.
 * 3. Wires Enter key → move down the same column for BRAND, TIME, UNIT PRICE.
 * 4. Wires a capture-phase click on those td cells to focus the inner input
 *    immediately (so clicking anywhere in the cell activates the field).
 */
(function (window, document) {
  'use strict';

  /* ── helpers ── */
  function eng() { return window.VirtualScrollEngine || null; }
  function allRows() { var e = eng(); return (e && e.getRows) ? e.getRows() : []; }
  function visRows() { var e = eng(); return (e && e.getVisibleRows) ? e.getVisibleRows() : allRows(); }
  // When the case offer type is TO-only, the Proforma price columns are locked.
  function pricingLocked() { return !!(window.FT_TOOL_SAVE && window.FT_TOOL_SAVE.pricingLocked); }

  function fmtNum(n) {
    if (!isFinite(n) || n === 0) return '';
    return n.toLocaleString('en-US', { maximumFractionDigits: 2 });
  }

  /* ── Step 1: inject BRAND and TIME into the HTML template (runs at DCL, before VSE) ── */
  function mkTextAreaCell(colName) {
    var td = document.createElement('td');
    td.className = 'col-' + colName.replace(/ /g, '-');
    td.setAttribute('data-col-name', colName);
    td.setAttribute('data-display-name', colName);
    var ta = document.createElement('textarea');
    ta.className = 'cell-input pi-text-area';
    ta.rows = 1;
    ta.style.resize = 'none';
    ta.setAttribute('autocomplete', 'off');
    td.appendChild(ta);
    return td;
  }

  function injectBrandTime() {
    var tpl = document.getElementById('raw-excel-template');
    if (!tpl || !tpl.content) return;
    tpl.content.querySelectorAll('tr.row').forEach(ensureBrandTimeOnRow);
  }

  /** BRAND and TIME must both exist on every PI row (TO-only and TO&PI).
   *  Older bug: if BRAND was already present (seeded from TO), the injector
   *  returned early and never added TIME — so TIME was missing in PI. */
  function ensureBrandTimeOnRow(tr) {
    if (!tr) return;
    var anchor = tr.querySelector('td[data-col-name="UNIT PRICE"]') ||
                 tr.querySelector('td[data-calc-variable="unit_price"]');
    var brandTd = tr.querySelector('td[data-col-name="BRAND"]');
    var timeTd = tr.querySelector('td[data-col-name="TIME"]');

    if (!brandTd) {
      brandTd = mkTextAreaCell('BRAND');
      if (anchor) tr.insertBefore(brandTd, anchor);
      else tr.appendChild(brandTd);
    }
    if (!timeTd) {
      timeTd = mkTextAreaCell('TIME');
      if (anchor) tr.insertBefore(timeTd, anchor);
      else if (brandTd && brandTd.nextSibling) tr.insertBefore(timeTd, brandTd.nextSibling);
      else tr.appendChild(timeTd);
    }

    // Hydrate empty editors from any pre-rendered text left in the cell.
    [brandTd, timeTd].forEach(function (td) {
      if (!td) return;
      var ta = td.querySelector('textarea.pi-text-area, textarea.cell-input');
      if (!ta) return;
      if (String(ta.value || '').trim()) return;
      var leftover = '';
      Array.prototype.forEach.call(td.childNodes, function (n) {
        if (n.nodeType === 3) leftover += n.textContent || '';
      });
      leftover = leftover.trim();
      if (leftover) ta.value = leftover;
    });

    reorderPiCommercialOnRow(tr);
  }

  /** LTR commercial block: REMARK → BRAND → TIME → UNIT PRICE. */
  function reorderPiCommercialOnRow(tr) {
    if (!tr) return;
    var unit = tr.querySelector('td[data-col-name="UNIT PRICE"]') ||
               tr.querySelector('td[data-calc-variable="unit_price"]');
    if (!unit) return;
    var rem = tr.querySelector('td[data-col-name="ریمارک"]');
    var brand = tr.querySelector('td[data-col-name="BRAND"]');
    var time = tr.querySelector('td[data-col-name="TIME"]');
    if (rem) tr.insertBefore(rem, unit);
    if (brand) tr.insertBefore(brand, unit);
    if (time) tr.insertBefore(time, unit);
  }

  function ensureBrandTimeOnAllRows() {
    allRows().forEach(ensureBrandTimeOnRow);
  }

  /** Keep the sticky header th order aligned with the first data row. */
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

  /* ── Step 2: inject ONE always-on input per UNIT PRICE cell ──
     The input is the ONLY element in the cell. When blurred it shows
     "1,234 Rial" (value + currency unit); when focused it shows the raw
     editable number. No separate display span, no source badge under it. */
  function curUnitLabel() {
    var ext = !!(window.FT_TOOL_SAVE && window.FT_TOOL_SAVE.externalCurrency);
    var u = (window.CalcCurrentCurrency ? window.CalcCurrentCurrency() : (ext ? 'usd' : 'rial'));
    if (ext && u === 'rial') u = 'usd';
    if (window.CalcCurrencySymbol) {
      var sym = window.CalcCurrencySymbol(u);
      if (sym) return sym;
    }
    if (u === 'usd') return '$';
    if (u === 'eur') return '€';
    if (u === 'rial') return 'Rial';
    return String(u || '').toUpperCase();
  }

  function showFormatted(inp) {
    // Display uses the calculated value (margins / conversion) when present;
    // dataset.raw always stays the editable BASE from data-calc-base.
    if (!inp) return;
    var td = inp.closest ? inp.closest('td') : null;
    var baseAttr = td && (td.getAttribute('data-calc-base') || td.getAttribute('data-calc-raw'));
    var baseN = parseFloat(String(baseAttr != null && baseAttr !== '' ? baseAttr : (inp.dataset.raw || '')).replace(/[^0-9.\-]/g, ''));
    var displaySrc = (td && td.dataset.calcValue != null && td.dataset.calcValue !== '')
      ? td.dataset.calcValue
      : (isFinite(baseN) ? String(baseN) : String(inp.value || ''));
    var n = parseFloat(String(displaySrc).replace(/[^0-9.\-]/g, ''));
    if (isFinite(n) && n !== 0) {
      inp.value = fmtNum(n) + ' ' + curUnitLabel();
    } else {
      inp.value = '';
    }
    inp.dataset.raw = (isFinite(baseN) && baseN !== 0) ? String(baseN) : '';
  }

  function showRaw(inp) {
    // Edit the BASE price (pre-margin), with live thousand-separators.
    if (!inp) return;
    var td = inp.closest ? inp.closest('td') : null;
    var base = td && (td.getAttribute('data-calc-base') || td.getAttribute('data-calc-raw'));
    var raw = String(base != null && base !== '' ? base : (inp.dataset.raw || inp.value || '')).replace(/[^0-9.\-]/g, '');
    if (!raw) { inp.value = ''; return; }
    var p = raw.split('.');
    var intp = p[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    inp.value = (p.length > 1) ? (intp + '.' + p.slice(1).join('')) : intp;
  }

  function injectUnitInputs() {
    allRows().forEach(function (tr) {
      var td = tr.querySelector('td[data-col-name="UNIT PRICE"]') ||
               tr.querySelector('td[data-calc-variable="unit_price"]');
      if (!td || td.querySelector('input.pi-unit-input')) return;

      // Determine the saved value: prefer the row's persisted raw unit price,
      // then data-calc-base, else parse the cell's existing text.
      var rowRaw = (tr.getAttribute('data-row-unit-raw') || '');
      var rawAttr = rowRaw || (td.getAttribute('data-calc-base') || td.getAttribute('data-calc-raw') || '');
      var base = parseFloat(String(rawAttr).replace(/,/g, ''));
      if (!isFinite(base)) {
        var txt = (td.textContent || '').replace(/[^0-9.\-]/g, '');
        base = parseFloat(txt);
      }
      // Source label persisted on the row (Manual / list name), else on the cell.
      var existingSrc = tr.getAttribute('data-row-price-source') ||
                        td.getAttribute('data-price-source') || '';

      var inp = document.createElement('input');
      inp.type = 'text';
      inp.className = 'cell-input pi-unit-input';
      inp.setAttribute('autocomplete', 'off');
      inp.setAttribute('inputmode', 'decimal');

      // TO-only offer → the price column is read-only: disable the input and mark
      // the cell locked so no manual entry is possible.
      if (pricingLocked()) {
        inp.disabled = true;
        inp.readOnly = true;
        td.setAttribute('data-locked', '1');
        td.setAttribute('data-price-locked', '1');
      }

      // IMPORTANT: wipe the cell completely (text nodes + spans) so the saved
      // value isn't left sitting under the new input as a duplicate.
      td.textContent = '';

      td.insertBefore(inp, td.firstChild);

      // Initial formatted display (value + unit) when not focused.
      if (isFinite(base) && base !== 0) {
        inp.value = String(base);
        inp.dataset.raw = String(base);
        td.setAttribute('data-calc-base', String(base));
        td.setAttribute('data-calc-raw', String(base));
        showFormatted(inp);
        // Repaint the Manual/list chip if this row had a source.
        if (existingSrc) {
          td.setAttribute('data-price-source', existingSrc);
          if (window.PIPaintSource) window.PIPaintSource(td, existingSrc);
        }
      }

      // Focus -> raw number; blur -> formatted with unit.
      inp.addEventListener('focus', function () { showRaw(inp); });
      inp.addEventListener('blur', function () { showFormatted(inp); });
    });
  }

  // Re-apply the formatted "value + unit" display to all unit inputs (e.g. when
  // the currency changes). Skips the one currently being edited.
  function reformatAllUnits() {
    allRows().forEach(function (tr) {
      var inp = tr.querySelector('input.pi-unit-input');
      if (inp && document.activeElement !== inp) showFormatted(inp);
    });
  }
  window.PIReformatUnits = reformatAllUnits;

  /* ── Auto-resize BRAND/TIME textareas so the row grows to fit wrapped text ── */
  function sizeTextarea(ta) {
    if (!ta) return;
    ta.style.height = 'auto';           // reset so scrollHeight reflects content
    // scrollHeight is the content height; with border-box we add the 2px border
    // so the text never clips at the bottom edge.
    var h = ta.scrollHeight + 2;
    ta.style.height = h + 'px';
  }
  function autoSize(ta) {
    sizeTextarea(ta);
    // Tell the virtual-scroll engine this row may have changed height so it can
    // fix the scrollbar length / window (keeps scrolling reaching the true end).
    var tr = ta.closest ? ta.closest('tr') : null;
    if (tr && window.VirtualScrollEngine && window.VirtualScrollEngine.remeasureRow) {
      window.VirtualScrollEngine.remeasureRow(tr);
    }
  }
  function autoSizeAll() {
    allRows().forEach(function (tr) {
      tr.querySelectorAll('.pi-text-area').forEach(sizeTextarea);
    });
    // One refresh after sizing every textarea (cheaper than per-row).
    if (window.VirtualScrollEngine && window.VirtualScrollEngine.refresh) {
      window.VirtualScrollEngine.refresh();
    }
  }

  /* ── Step 3 + 4: Enter-key navigation and click-to-focus ── */
  // Columns that support Enter→move-down in PI. Skips locked / disabled cells.
  var PI_COLS = ['BRAND', 'TIME', 'UNIT PRICE', 'SERVICE PRICE', 'ریمارک'];

  function fieldIsWritable(fld) {
    if (!fld) return false;
    if (fld.disabled || fld.readOnly) return false;
    var td = fld.closest ? fld.closest('td') : null;
    if (td && (td.getAttribute('data-locked') === '1' || td.classList.contains('svc-locked'))) return false;
    var tr = fld.closest ? fld.closest('tr') : null;
    if (tr && (tr.getAttribute('data-deleted') === '1' || tr.getAttribute('data-unsuppliable') === '1')) return false;
    return true;
  }

  function focusColInput(tr, colName) {
    if (!tr) return false;
    var cell = tr.querySelector('td[data-col-name="' + colName + '"]');
    if (!cell) return false;
    var fld = cell.querySelector('textarea.cell-input') ||
              cell.querySelector('input.svc-price-input') ||
              cell.querySelector('input.pi-unit-input') ||
              cell.querySelector('input.cell-input') ||
              cell.querySelector('textarea.remark-revision-textarea') ||
              cell.querySelector('textarea, input');
    if (!fieldIsWritable(fld)) return false;
    try { tr.scrollIntoView({ block: 'nearest' }); } catch (_) {}
    setTimeout(function () { fld.focus(); if (fld.select) fld.select(); }, 0);
    return true;
  }

  function focusNextWritableInCol(rows, fromIdx, colName) {
    for (var i = fromIdx + 1; i < rows.length; i++) {
      if (focusColInput(rows[i], colName)) return true;
    }
    return false;
  }

  function wireEvents() {
    var table = document.getElementById('virtual-scroll-table');
    if (!table) return;

    /* Enter → move down the same column (Excel-style).
       For BRAND/TIME textareas, Enter navigates rather than inserting a newline.
       Locked SERVICE/UNIT PRICE cells are skipped until the next writable one. */
    if (!table.dataset.piEnterNav) {
      table.dataset.piEnterNav = '1';
      table.addEventListener('keydown', function (e) {
        if (e.key !== 'Enter') return;
        var fld = e.target;
        if (!fld || (fld.tagName !== 'INPUT' && fld.tagName !== 'TEXTAREA')) return;
        if (!fld.classList.contains('cell-input') && !fld.classList.contains('pi-unit-input') &&
            !fld.classList.contains('svc-price-input') &&
            !fld.classList.contains('pi-text-area') && !fld.classList.contains('remark-revision-textarea')) return;
        var td = fld.closest ? fld.closest('td') : null;
        var col = td ? td.getAttribute('data-col-name') : '';
        if (PI_COLS.indexOf(col) < 0) return;
        e.preventDefault();
        var tr = td ? td.closest('tr') : null;
        if (!tr) return;
        var rows = visRows();
        var idx = rows.indexOf(tr);
        if (idx < 0) { fld.blur(); return; }
        if (!focusNextWritableInCol(rows, idx, col)) {
          var vp = document.getElementById('virtual-scroll-viewport');
          if (vp) vp.scrollTop += 34;
          setTimeout(function () {
            var fresh = visRows();
            var freshIdx = fresh.indexOf(tr);
            if (freshIdx < 0) freshIdx = idx;
            if (!focusNextWritableInCol(fresh, freshIdx, col)) fld.blur();
          }, 30);
        }
      });
    }

    /* Click on td → immediately focus the inner field (capture phase fires first).
       BUT never hijack clicks that land inside the per-row margin panel (those
       inputs/buttons live inside the UNIT PRICE cell and must work normally). */
    if (!table.dataset.piCellFocus) {
      table.dataset.piCellFocus = '1';
      table.addEventListener('click', function (e) {
        if (e.target.closest && e.target.closest('.row-margin-panel')) return;
        var td = e.target.closest ? e.target.closest('td') : null;
        if (!td) return;
        var col = td.getAttribute('data-col-name') || '';
        if (PI_COLS.indexOf(col) < 0) return;
        var directField = (e.target.classList &&
          (e.target.classList.contains('pi-unit-input') ||
           e.target.classList.contains('svc-price-input') ||
           e.target.classList.contains('cell-input') ||
           e.target.classList.contains('pi-text-area')));
        var onBareCell = (e.target === td);
        if (!directField && !onBareCell) return;
        var fld = td.querySelector('textarea.cell-input, input.svc-price-input, input.cell-input, input.pi-unit-input');
        if (fld && fieldIsWritable(fld) && document.activeElement !== fld) {
          e.stopPropagation();
          fld.focus();
          if (fld.select) fld.select();
        }
      }, true /* capture */);
    }

    /* Auto-grow BRAND/TIME textareas as the user types (row height follows). */
    if (!table.dataset.piAutoSize) {
      table.dataset.piAutoSize = '1';
      table.addEventListener('input', function (e) {
        var ta = e.target;
        if (ta && ta.classList && ta.classList.contains('pi-text-area')) autoSize(ta);
      });
    }
  }

  /* ── Boot ── */
  document.addEventListener('DOMContentLoaded', function () {
    // Step 1 must run BEFORE the virtual scroll engine processes the template.
    injectBrandTime();

    // Steps 2-4 need the VSE rows to already exist.
    setTimeout(function () {
      // Re-ensure on live rows (covers BRAND-from-TO without TIME, and any
      // rows cloned after the template pass).
      ensureBrandTimeOnAllRows();
      syncHeaderOrderFromFirstRow();
      injectUnitInputs();
      wireEvents();

      // Auto-size BRAND/TIME textareas for every row as it scrolls into view
      // (off-screen rows can't be measured, so we size them lazily on render).
      if (window.VirtualScrollEngine && window.VirtualScrollEngine.onRender) {
        window.VirtualScrollEngine.onRender(function (rows, start, end) {
          for (var i = start; i < end; i++) {
            var tr = rows[i];
            if (!tr) continue;
            ensureBrandTimeOnRow(tr);
            tr.querySelectorAll('.pi-text-area').forEach(function (ta) {
              if (ta === document.activeElement) return; // don't disturb typing
              sizeTextarea(ta);
            });
          }
        });
      }
      autoSizeAll();
      // Column widths were computed before TIME existed — refresh so TIME shows.
      if (window.enableResizableColumns) {
        var table = document.getElementById('virtual-scroll-table');
        if (table) window.enableResizableColumns(table);
      }
    }, 0);
  });

  // Re-inject if rows change (defensive; shouldn't normally occur after boot).
  document.addEventListener('ft-rows-changed', function () {
    setTimeout(function () {
      ensureBrandTimeOnAllRows();
      injectUnitInputs();
    }, 0);
  });

})(window, document);
