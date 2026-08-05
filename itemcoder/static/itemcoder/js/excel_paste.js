/* excel_paste.js — Excel-style column paste into REMARK / REVISION (and the PI
 * proforma-remark, which is the REMARK column on a Proforma).
 *
 * Copy a column from Excel (or anywhere), click into a remark/revision cell and
 * paste: the clipboard's lines are distributed DOWN the currently filtered rows,
 * one value per row, starting at the row you pasted into. Each filled row is
 * processed + saved exactly as if typed, even if it is scrolled out of view.
 */
(function (window, document) {
  'use strict';

  // Columns that accept an Excel-style multi-row paste: REMARK / REVISION
  // everywhere, plus BRAND / TIME / UNIT PRICE on a Proforma.
  var PASTE_COLS = { 'ریمارک': 1, 'اصلاحیه': 1, 'BRAND': 1, 'TIME': 1, 'UNIT PRICE': 1 };

  function viewRows() {
    var eng = window.VirtualScrollEngine;
    if (eng && eng.getVisibleRows) return eng.getVisibleRows();
    if (eng && eng.getRows) return eng.getRows();
    return [];
  }

  function splitClipboard(text) {
    // Normalise newlines; Excel single-column copy = one value per line.
    var t = String(text == null ? '' : text).replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    // Drop a single trailing newline (Excel adds one) but keep internal blanks.
    if (t.charAt(t.length - 1) === '\n') t = t.slice(0, -1);
    return t.split('\n');
  }

  function onPaste(e) {
    var cell = e.target && e.target.closest ? e.target.closest('td') : null;
    if (!cell) return;
    var col = cell.dataset.colName || '';
    if (!PASTE_COLS[col]) return;
    // Locked cells (remark-cleared brand/time/price, or pending PF resolve) refuse paste.
    if (cell.getAttribute('data-locked') === '1') return;
    var tr0 = cell.closest('tr');
    if (tr0 && tr0.getAttribute('data-pf-pending') === '1' &&
        (col === 'ریمارک' || col === 'اصلاحیه')) return;

    var cd = e.clipboardData || window.clipboardData;
    if (!cd) return;
    var text = cd.getData('text/plain') || cd.getData('Text') || '';
    var lines = splitClipboard(text);

    // Only hijack when it's a multi-row paste; a single value pastes normally.
    if (lines.length <= 1) return;

    e.preventDefault();

    var rows = viewRows();
    var startTr = cell.closest('tr');
    var start = rows.indexOf(startTr);
    if (start < 0) start = 0;

    // Snapshot the rows we're about to overwrite so Ctrl+Z can restore them.
    if (window.FT_UNDO && window.FT_UNDO.capture) {
      var affected = [];
      for (var s = 0; s < lines.length && (start + s) < rows.length; s++) affected.push(rows[start + s]);
      window.FT_UNDO.capture('paste into ' + (col === 'اصلاحیه' ? 'REVISION' : 'REMARK'), [col], affected);
    }

    var api = window.FT_TABLE_UI;
    var filled = 0;
    for (var i = 0; i < lines.length && (start + i) < rows.length; i++) {
      var tr = rows[start + i];
      if (!tr) break;
      var val = lines[i];
      var td = tr.querySelector('td[data-col-name="' + col + '"]') ||
               (col === 'UNIT PRICE' ? tr.querySelector('td[data-calc-variable="unit_price"]') : null);
      var field = td ? td.querySelector('input, textarea') : null;
      if (field) {
        field.value = val;
        field.dispatchEvent(new Event('input', { bubbles: true }));
        field.dispatchEvent(new Event('blur', { bubbles: true }));
      } else if (api && api.setCellValue) {
        api.setCellValue(tr, col, val);
        if (api.submitRow) api.submitRow(tr);
      } else if (td) {
        td.textContent = val;
      }
      filled++;
    }

    if (window.VirtualScrollEngine && window.VirtualScrollEngine.refresh) {
      window.VirtualScrollEngine.refresh();
    }
    // (No on-screen paste/undo message — kept silent per request.)
  }

  document.addEventListener('DOMContentLoaded', function () {
    var table = document.getElementById('virtual-scroll-table');
    if (!table) return;
    table.addEventListener('paste', onPaste, true);
  });
})(window, document);
