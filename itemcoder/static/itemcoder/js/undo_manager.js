/* undo_manager.js — Ctrl+Z (undo) for bulk edits.
 *
 * Bulk operations that touch many rows at once — pasting an Excel column into
 * REMARK / REVISION (TO) or BRAND / TIME / UNIT PRICE (PI), and the "Apply"
 * buttons that push one value to every filtered row — push a snapshot onto an
 * undo stack BEFORE they run. Pressing Ctrl+Z (or Cmd+Z) pops the most recent
 * snapshot and restores every captured cell to its previous value, then fires
 * the right events so totals / counters / row state recompute.
 *
 * The browser's native per-field undo still works for ordinary typing; this
 * only intercepts Ctrl+Z when the last action was one of our bulk operations.
 */
(function (window, document) {
  'use strict';

  var stack = [];          // each entry: { label, cells:[{tr,col,kind,value}] }
  var MAX = 50;

  function rowsAll() {
    var eng = window.VirtualScrollEngine;
    return (eng && eng.getRows) ? eng.getRows() : [];
  }

  // Read the current value of a (row, column) cell. kind tells us where the
  // value lives so we can both snapshot and restore it identically.
  function readCell(tr, col) {
    var td = tr.querySelector('td[data-col-name="' + col + '"]') ||
             (col === 'UNIT PRICE'
               ? tr.querySelector('td[data-calc-variable="unit_price"]')
               : null);
    if (!td) return null;
    // Prefer an inner field (input/textarea); else use the cell text.
    var field = td.querySelector('input, textarea');
    if (field) {
      return {
        tr: tr, col: col, kind: 'field',
        value: field.value,
        // For UNIT PRICE also remember the raw + source so the chip restores.
        raw: td.getAttribute('data-calc-base') || td.getAttribute('data-calc-raw') || '',
        src: td.getAttribute('data-price-source') || '',
        manual: tr.getAttribute('data-unit-manual') || ''
      };
    }
    return {
      tr: tr, col: col, kind: 'text',
      value: (td.textContent || '')
    };
  }

  // Public: capture a snapshot of the given rows × columns BEFORE a bulk op.
  // rows defaults to all rows; columns is an array of data-col-name values.
  function capture(label, columns, rows) {
    rows = rows || rowsAll();
    var cells = [];
    rows.forEach(function (tr) {
      columns.forEach(function (col) {
        var snap = readCell(tr, col);
        if (snap) cells.push(snap);
      });
    });
    if (!cells.length) return;
    stack.push({ label: label || 'bulk edit', cells: cells });
    if (stack.length > MAX) stack.shift();
    flashHint(label);
  }

  function restoreCell(snap) {
    var tr = snap.tr;
    var col = snap.col;
    var td = tr.querySelector('td[data-col-name="' + col + '"]') ||
             (col === 'UNIT PRICE'
               ? tr.querySelector('td[data-calc-variable="unit_price"]')
               : null);
    if (!td) return;
    if (snap.kind === 'field') {
      var field = td.querySelector('input, textarea');
      if (field) {
        field.value = snap.value;
        // Restore UNIT PRICE raw/source/manual state.
        if (snap.raw != null) {
          if (snap.raw === '') { td.removeAttribute('data-calc-base'); td.removeAttribute('data-calc-raw'); }
          else { td.setAttribute('data-calc-base', snap.raw); td.setAttribute('data-calc-raw', snap.raw); }
          if (field.dataset) field.dataset.raw = snap.raw;
        }
        if (snap.src != null) {
          if (snap.src === '') td.removeAttribute('data-price-source');
          else td.setAttribute('data-price-source', snap.src);
          // Repaint or clear the source chip.
          if (window.PIPaintSource && snap.src) window.PIPaintSource(td, snap.src);
          else { var chip = td.querySelector('.price-src'); if (chip) chip.remove(); }
        }
        if (snap.manual != null) {
          if (snap.manual === '1') tr.setAttribute('data-unit-manual', '1');
          else tr.removeAttribute('data-unit-manual');
        }
        // Fire input so autosize / pricing / counters react.
        field.dispatchEvent(new Event('input', { bubbles: true }));
        field.dispatchEvent(new Event('blur', { bubbles: true }));
      }
    } else {
      td.textContent = snap.value;
    }
  }

  function undo() {
    var entry = stack.pop();
    if (!entry) return false;
    entry.cells.forEach(restoreCell);
    // Recompute everything that depends on these cells.
    document.dispatchEvent(new CustomEvent('ft-rows-changed'));
    if (window.VirtualScrollEngine && window.VirtualScrollEngine.refresh) {
      window.VirtualScrollEngine.refresh();
    }
    flashHint('Undid ' + entry.label, true);
    return true;
  }

  // Small transient hint — DISABLED per request (no on-screen paste/undo toast).
  function flashHint(text, isUndo) { /* intentionally silent */ }

  // Intercept Ctrl+Z / Cmd+Z. Whenever we have a bulk snapshot (paste / Apply),
  // OUR undo takes priority — a bulk edit is the most recent action, so restoring
  // it is what the user expects, even while a cell field is focused.
  document.addEventListener('keydown', function (e) {
    var z = (e.key === 'z' || e.key === 'Z');
    if (!z || !(e.ctrlKey || e.metaKey) || e.shiftKey || e.altKey) return;
    if (!stack.length) return;  // nothing of ours to undo → let native handle
    e.preventDefault();
    e.stopPropagation();
    undo();
  }, true);

  window.FT_UNDO = { capture: capture, undo: undo };
})(window, document);
