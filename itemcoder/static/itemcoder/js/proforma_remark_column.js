/* proforma_remark_column.js — TO read-only "PF remark" column (single, at the
 * far right). Active only on the TO tool AND only when a current Proforma with
 * remarks exists (so it never appears on the first TO build). Clicking a cell
 * copies its text. The matching card (count + row numbers + on/off toggle) lives
 * in the Details metrics grid.
 */
(function (window, document) {
  'use strict';

  var COL = 'proforma remark';
  var DISPLAY = 'PF REMARK';            // short header title

  function isTO() { return ((window.FT_TOOL_SAVE && window.FT_TOOL_SAVE.kind) || '').toUpperCase() === 'TO'; }

  function readMap() {
    var el = document.getElementById('ft-proforma-remarks');
    if (!el) return null;
    try { return JSON.parse(el.textContent || '{}'); } catch (e) { return null; }
  }

  function itemCodeOf(tr) {
    var td = tr.querySelector('td[data-col-name="Item Code"]');
    if (!td) return '';
    var num = td.querySelector('.ic-num');
    return ((num ? num.textContent : td.textContent) || '').trim();
  }

  function decorate(tr, map) {
    if (!tr || tr.querySelector('td[data-col-name="' + COL + '"]')) return;
    var code = itemCodeOf(tr);
    var val = (map && map[code]) ? String(map[code]) : '';
    var td = document.createElement('td');
    td.setAttribute('data-col-name', COL);
    td.setAttribute('data-display-name', DISPLAY);
    td.className = 'pf-remark-cell' + (val ? ' has-pf' : '');
    if (val) { td.setAttribute('data-pf-has', '1'); td.title = 'Click to copy'; }
    td.textContent = val;
    tr.appendChild(td);   // far right
  }

  function decorateTemplate(map) {
    var tpl = document.getElementById('raw-excel-template');
    if (!tpl || !tpl.content) return;
    tpl.content.querySelectorAll('tr.row').forEach(function (tr) { decorate(tr, map); });
  }
  function rows() {
    var eng = window.VirtualScrollEngine;
    return (eng && eng.getRows) ? eng.getRows() : [];
  }
  function decorateAll(map) { rows().forEach(function (tr) { decorate(tr, map); }); }

  function wireCopy() {
    var table = document.getElementById('virtual-scroll-table');
    if (!table || table.dataset.pfReady === '1') return;
    table.dataset.pfReady = '1';
    table.addEventListener('click', function (e) {
      var td = e.target.closest ? e.target.closest('td[data-col-name="' + COL + '"]') : null;
      if (!td) return;
      var text = (td.textContent || '').trim();
      if (!text) return;
      function flash() { td.classList.add('pf-copied'); setTimeout(function () { td.classList.remove('pf-copied'); }, 700); }
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(flash, flash);
      else { var ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta); ta.select(); try { document.execCommand('copy'); } catch (err) {} document.body.removeChild(ta); flash(); }
    });
  }

  function updateCount() {
    var nums = [];
    rows().forEach(function (tr) {
      var td = tr.querySelector('td[data-col-name="' + COL + '"]');
      if (td && (td.textContent || '').trim() !== '') nums.push(itemCodeOf(tr));
    });
    var el = document.getElementById('pf-remark-count');
    if (el) el.textContent = nums.length;
    var list = document.getElementById('pf-remark-rows');
    if (list) { list.textContent = ''; list.style.display = 'none'; }
  }

  function wireFilter() {
    var chk = document.getElementById('pf-filter-chk');
    if (!chk || chk.dataset.ready === '1') return;
    chk.dataset.ready = '1';
    chk.addEventListener('change', function () {
      var eng = window.VirtualScrollEngine;
      if (!eng || !eng.addFilter) return;
      if (chk.checked) {
        eng.addFilter('pf-remark', function (tr) {
          var td = tr.querySelector('td[data-col-name="' + COL + '"]');
          return td && (td.textContent || '').trim() !== '';
        });
      } else { eng.removeFilter('pf-remark'); }
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    if (!isTO()) return;
    var map = readMap();
    if (!map || !Object.keys(map).length) return;   // no PI yet → no column

    decorateTemplate(map);
    setTimeout(function () {
      decorateAll(map);
      wireCopy();
      wireFilter();
      updateCount();
      var eng = window.VirtualScrollEngine;
      if (eng && eng.onRender) {
        eng.onRender(function (allRows, start, end) { for (var i = start; i < end; i++) decorate(allRows[i], map); });
        if (eng.refresh) setTimeout(function () { eng.refresh(); }, 0);
      }
    }, 0);
  });
})(window, document);
