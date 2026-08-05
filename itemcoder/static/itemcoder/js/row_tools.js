/* Per-row delete (×) for the TO/PI tool grids.
 *
 * Loaded BEFORE virtual_scroll_engine.js so it can inject a delete cell into
 * every template row before the engine reads the template (the engine builds
 * its header from the first row, so the delete column appears automatically).
 * Removing a row splices it out of the engine's in-memory row list and asks the
 * engine to re-render. The delete cell carries no data-col-name, so the save
 * collector and the column layout ignore it. No coding logic is involved.
 */
(function (window, document) {
  'use strict';

  // In plain EDIT mode the row set is fixed (it follows the inquiry): the user
  // may change cell values but NOT add or delete rows. Row deletion is only
  // offered while building a form or making a NEW version. So we skip injecting
  // the delete column entirely when the tool opened in edit mode.
  var MODE = (window.FT_TOOL_SAVE && window.FT_TOOL_SAVE.mode) || 'build';
  var ALLOW_DELETE = (MODE !== 'edit');

  function injectDeleteCells() {
    if (!ALLOW_DELETE) return;
    var tpl = document.getElementById('raw-excel-template');
    if (!tpl || !tpl.content) return;
    tpl.content.querySelectorAll('tr.row').forEach(function (tr) {
      if (tr.querySelector('.row-del-cell')) return;
      var td = document.createElement('td');
      td.className = 'row-del-cell';
      td.setAttribute('data-col-name', '__del__');   // skipped on save; plain header
      td.innerHTML = '<button type="button" class="row-del" aria-label="Remove row" title="Remove row">\u00d7</button>';
      tr.insertBefore(td, tr.firstChild);            // leftmost column
    });
  }

  function wireDelete() {
    if (!ALLOW_DELETE) return;
    var table = document.getElementById('virtual-scroll-table');
    if (!table || table.dataset.rowDelReady === '1') return;
    table.dataset.rowDelReady = '1';
    table.addEventListener('click', function (e) {
      var btn = e.target.closest ? e.target.closest('.row-del') : null;
      if (!btn) return;
      var tr = btn.closest('tr');
      var eng = window.VirtualScrollEngine;
      if (!tr || !eng || !eng.getRows) return;
      var arr = eng.getRows();
      var i = arr.indexOf(tr);
      if (i >= 0) {
        arr.splice(i, 1);
        if (eng.refresh) eng.refresh();
        document.dispatchEvent(new CustomEvent('ft-rows-changed'));
      }
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    injectDeleteCells();   // runs before the engine's own DOMContentLoaded init
    wireDelete();
  });
})(window, document);
