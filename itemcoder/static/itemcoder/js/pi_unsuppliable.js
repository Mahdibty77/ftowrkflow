/* pi_unsuppliable.js — Proforma only. Replaces the old per-row delete (×) with
 * a "mark as NOT SUPPLIABLE" toggle. Clicking the dot on a row tints the row
 * and stamps a NOT SUPPLIABLE label; clicking again clears it. The state is
 * carried on the row (data-unsuppliable) and persisted by tool_save, so it
 * survives Save / reload. The toggle cell uses data-col-name="__unsup__" and is
 * skipped on save (only the row flag is stored).
 */
(function (window, document) {
  'use strict';

  function injectToggle() {
    var tpl = document.getElementById('raw-excel-template');
    if (!tpl || !tpl.content) return;
    tpl.content.querySelectorAll('tr.row').forEach(function (tr) {
      if (tr.querySelector('.unsup-cell')) return;
      var td = document.createElement('td');
      td.className = 'unsup-cell';
      td.setAttribute('data-col-name', '__unsup__');   // skipped on save
      td.innerHTML = '<button type="button" class="unsup-btn" aria-label="Toggle not suppliable" title="Mark as NOT SUPPLIABLE">\u25CF</button>';
      tr.insertBefore(td, tr.firstChild);              // leftmost column
      if (tr.getAttribute('data-unsuppliable') === '1') tr.classList.add('row-unsuppliable');
    });
  }

  function wire() {
    var table = document.getElementById('virtual-scroll-table');
    if (!table || table.dataset.unsupReady === '1') return;
    table.dataset.unsupReady = '1';
    table.addEventListener('click', function (e) {
      var btn = e.target.closest ? e.target.closest('.unsup-btn') : null;
      if (!btn) return;
      var tr = btn.closest('tr');
      if (!tr) return;
      var on = tr.classList.toggle('row-unsuppliable');
      tr.setAttribute('data-unsuppliable', on ? '1' : '0');
      // Subtotal/VAT/Grand Total (and Service Price's own total adjustment)
      // must reflect this immediately, not wait for some unrelated cell edit
      // to happen to trigger a recompute. Reuses the exact event
      // calculation_controls.js already dispatches for the same purpose —
      // pi_pricing.js's recomputeGrand and service_price.js's
      // recomputeAllTotals both already listen for it.
      document.dispatchEvent(new CustomEvent('ft-calc-refreshed'));
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    injectToggle();          // before the engine reads the template
    setTimeout(wire, 0);     // after the engine builds the grid
  });
})(window, document);
