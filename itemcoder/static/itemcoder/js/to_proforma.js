/* to_proforma.js — Technical Offer helpers (loaded only on the TO tool):
 *   1) Always: a "rows without an FT CODE" counter + filter checkbox.
 *   2) When the current Proforma has remarks: a READ-ONLY PROFORMA REMARK
 *      reference column (click a cell to copy), a counter, and a filter checkbox.
 *
 * Runs before virtual_scroll_engine.js so it can inject the reference column
 * into the template. The reference cell uses data-col-name="__proforma_remark__";
 * tool_save.js skips "__" columns, so it never becomes part of the saved TO.
 */
(function (window, document) {
  'use strict';

  var CODE_COL = 'کد';   // FT CODE
  var BRAND_COL = 'BRAND';

  function getMap() {
    var el = document.getElementById('ft-proforma-remarks');
    if (!el) return null;
    try { return JSON.parse(el.textContent || '{}'); } catch (e) { return {}; }
  }
  function cellText(td) {
    if (!td) return '';
    var f = td.querySelector('textarea,input,select');
    return (f ? f.value : td.textContent || '').trim();
  }
  function rows() {
    return (window.VirtualScrollEngine && window.VirtualScrollEngine.getRows)
      ? window.VirtualScrollEngine.getRows() : [];
  }
  function codeText(tr) { return cellText(tr.querySelector('td[data-col-name="' + CODE_COL + '"]')); }
  function brandText(tr) { return cellText(tr.querySelector('td[data-col-name="' + BRAND_COL + '"]')); }

  // The visible Item Code value (row number shown in the leftmost column).
  function itemCodeOf(tr) {
    var cell = tr.querySelector('td[data-col-name="Item Code"]');
    if (!cell) return '';
    var num = cell.querySelector('.ic-num');
    return (num ? num.textContent : cell.textContent || '').trim();
  }

  // Render count only (no "#3, #4" list — Details table shows totals).
  function setCountWithRows(countId, listId, nums) {
    var el = document.getElementById(countId);
    if (el) el.textContent = nums.length;
    var list = document.getElementById(listId);
    if (list) {
      list.textContent = '';
      list.style.display = 'none';
    }
  }

  function injectColumn(map) {
    var tpl = document.getElementById('raw-excel-template');
    if (!tpl || !tpl.content) return;
    tpl.content.querySelectorAll('tr.row').forEach(function (tr) {
      if (tr.querySelector('.proforma-remark-cell')) return;
      var key = cellText(tr.querySelector('td[data-col-name="Item Code"]'));
      var val = (key && map[key]) ? map[key] : '';
      var td = document.createElement('td');
      td.className = 'proforma-remark-cell';
      td.setAttribute('data-col-name', '__proforma_remark__');
      td.setAttribute('data-display-name', 'PROFORMA REMARK');
      if (val) { td.setAttribute('data-has-remark', '1'); td.title = 'Click to copy'; }
      td.textContent = val;
      var remarkCell = tr.querySelector('td[data-col-name="ریمارک"]');
      if (remarkCell) tr.insertBefore(td, remarkCell); else tr.appendChild(td);
    });
  }

  function copyText(txt, done) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(txt).then(done, done);
    } else {
      try {
        var ta = document.createElement('textarea');
        ta.value = txt; ta.style.position = 'fixed'; ta.style.opacity = '0';
        document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();
      } catch (_e) {}
      done();
    }
  }

  function countNoCode() {
    var nums = [];
    rows().forEach(function (tr) { if (!codeText(tr)) nums.push(itemCodeOf(tr)); });
    setCountWithRows('to-nocode-count', 'to-nocode-rows', nums);
    return nums.length;
  }

  function countNoBrand() {
    var nums = [];
    rows().forEach(function (tr) {
      if (tr.getAttribute('data-deleted') === '1') return;
      if (!brandText(tr)) nums.push(itemCodeOf(tr));
    });
    setCountWithRows('to-nobrand-count', 'to-nobrand-rows', nums);
    return nums.length;
  }

  function chipifyAlarms() {
    rows().forEach(function (tr) {
      var c = tr.querySelector('td[data-col-name="Alarm_Features"]');
      if (!c || c.dataset.chipped === '1') return;
      if (c.querySelector('.alarm-chip')) { c.dataset.chipped = '1'; return; } // already chipped (server)
      // Use plain text (handles both fresh chips and reloaded single-space text).
      var tmp = document.createElement('div');
      tmp.innerHTML = (c.innerHTML || '').replace(/<br\s*\/?>/gi, ' ');
      var text = (tmp.textContent || '').trim();
      if (!text) { c.dataset.chipped = '1'; return; }
      var tokens = text.split(/[\s,]+/).map(function (s) { return s.trim(); }).filter(Boolean);
      if (tokens.length) {
        c.innerHTML = tokens.map(function (t) { return '<span class="alarm-chip">' + t + '</span>'; }).join(' ');
      }
      c.dataset.chipped = '1';
    });
  }

  function wirePipeQtyTool() {
    var btn = document.getElementById('tool-pipe-qty');
    var filterChk = document.getElementById('tool-pipe-qty-filter');
    if (!btn || btn.dataset.ready === '1') return;
    btn.dataset.ready = '1';

    function setQty6Filter(on) {
      var eng = window.VirtualScrollEngine;
      if (!eng || !eng.addFilter) return;
      if (on) {
        eng.addFilter('pipeqty6', function (tr) {
          return tr.getAttribute('data-pipe-qty6') === '1';
        });
      } else {
        eng.removeFilter('pipeqty6');
      }
      if (filterChk) filterChk.checked = !!on;
    }

    function setFilterEnabled(enabled, count) {
      var badge = document.getElementById('pipe-qty-count');
      if (badge) badge.textContent = String(count || 0);
      if (!filterChk) return;
      if (!enabled) {
        setQty6Filter(false);
        filterChk.checked = false;
        filterChk.disabled = true;
      } else {
        filterChk.disabled = false;
      }
      var card = btn.closest('.to-qty6-card');
      if (card) card.classList.toggle('is-active', !!enabled);
    }

    btn.addEventListener('click', function () {
      var on = !btn.classList.contains('active');
      btn.classList.toggle('active', on);
      var count = 0;
      if (!on) {
        setFilterEnabled(false, 0);
        rows().forEach(function (tr) {
          if ((tr.getAttribute('data-group') || '').trim().toLowerCase() !== 'pipe') return;
          var cell = tr.querySelector('td[data-col-name="qty"]');
          if (!cell) return;
          if (cell.dataset.origQty !== undefined) {
            var field = cell.querySelector('input,textarea');
            if (field) field.value = cell.dataset.origQty; else cell.textContent = cell.dataset.origQty;
            delete cell.dataset.origQty;
          }
          tr.removeAttribute('data-pipe-qty6');
        });
      } else {
        rows().forEach(function (tr) {
          if ((tr.getAttribute('data-group') || '').trim().toLowerCase() !== 'pipe') return;
          var cell = tr.querySelector('td[data-col-name="qty"]');
          if (!cell) return;
          var field = cell.querySelector('input,textarea');
          var cur = (field ? field.value : cell.textContent).trim();
          if (cell.dataset.origQty === undefined) cell.dataset.origQty = cur;
          var q = parseFloat(cur.replace(/,/g, ''));
          if (isFinite(q) && q > 0) {
            var rounded = Math.ceil(q / 6) * 6;
            if (rounded !== q) {
              if (field) field.value = String(rounded); else cell.textContent = String(rounded);
              tr.setAttribute('data-pipe-qty6', '1');
              count++;
            }
          }
        });
        setFilterEnabled(count > 0, count);
      }
    });

    if (filterChk && filterChk.dataset.ready !== '1') {
      filterChk.dataset.ready = '1';
      filterChk.addEventListener('change', function () {
        if (filterChk.disabled) {
          filterChk.checked = false;
          return;
        }
        setQty6Filter(!!filterChk.checked);
      });
    }
  }

  function visibleRows() {
    if (window.VirtualScrollEngine && window.VirtualScrollEngine.getVisibleRows) {
      return window.VirtualScrollEngine.getVisibleRows() || [];
    }
    return rows();
  }

  function applyBrandBulk() {
    var inp = document.getElementById('to-brand-input');
    if (!inp) return;
    var v = String(inp.value || '').trim();
    var targets = visibleRows().filter(function (tr) {
      return tr && tr.getAttribute('data-deleted') !== '1';
    });
    if (!targets.length) return;
    if (window.FT_UNDO && window.FT_UNDO.capture) {
      try { window.FT_UNDO.capture('apply brand', [BRAND_COL], targets); } catch (_e) {}
    }
    targets.forEach(function (tr) {
      var cell = tr.querySelector('td[data-col-name="' + BRAND_COL + '"]');
      if (!cell) return;
      var field = cell.querySelector('textarea.cell-input, input.cell-input, textarea, input[type="text"]');
      if (field) {
        field.value = v;
        try { field.dispatchEvent(new Event('input', { bubbles: true })); } catch (_e2) {}
        try { field.dispatchEvent(new Event('change', { bubbles: true })); } catch (_e3) {}
      } else {
        cell.textContent = v;
      }
    });
    countNoBrand();
    document.dispatchEvent(new CustomEvent('ft-rows-changed'));
  }

  function wireBrandBulk() {
    var btn = document.getElementById('to-brand-apply');
    var inp = document.getElementById('to-brand-input');
    if (btn && btn.dataset.ready !== '1') {
      btn.dataset.ready = '1';
      btn.addEventListener('click', applyBrandBulk);
    }
    if (inp && inp.dataset.ready !== '1') {
      inp.dataset.ready = '1';
      inp.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') {
          e.preventDefault();
          applyBrandBulk();
        }
      });
    }
  }

  function wire(hasMap) {
    var table = document.getElementById('virtual-scroll-table');
    if (table && hasMap && table.dataset.proformaReady !== '1') {
      table.dataset.proformaReady = '1';
      table.addEventListener('click', function (e) {
        var td = e.target.closest ? e.target.closest('.proforma-remark-cell') : null;
        if (!td) return;
        var txt = cellText(td);
        if (!txt) return;
        copyText(txt, function () {
          td.classList.add('copied');
          setTimeout(function () { td.classList.remove('copied'); }, 700);
        });
      });
    }
    // NOTE: the PF REMARK filter checkbox (#pf-filter-chk) is wired by
    // proforma_remark_column.js against the real "proforma remark" column. This
    // file must NOT touch it (a stale handler here used to win the race and
    // block the real filter).
    var nc = document.getElementById('to-nocode-chk');
    if (nc && nc.dataset.ready !== '1') {
      nc.dataset.ready = '1';
      nc.addEventListener('change', function () {
        var eng = window.VirtualScrollEngine;
        if (!eng || !eng.addFilter) return;
        if (nc.checked) eng.addFilter('nocode', function (tr) { return !codeText(tr); });
        else eng.removeFilter('nocode');
      });
    }
    var nb = document.getElementById('to-nobrand-chk');
    if (nb && nb.dataset.ready !== '1') {
      nb.dataset.ready = '1';
      nb.addEventListener('change', function () {
        var eng = window.VirtualScrollEngine;
        if (!eng || !eng.addFilter) return;
        if (nb.checked) {
          eng.addFilter('nobrand', function (tr) {
            return tr.getAttribute('data-deleted') !== '1' && !brandText(tr);
          });
        } else {
          eng.removeFilter('nobrand');
        }
      });
    }
    var ic = document.getElementById('to-issue-chk');
    if (ic && ic.dataset.ready !== '1') {
      ic.dataset.ready = '1';
      ic.addEventListener('change', function () {
        var eng = window.VirtualScrollEngine;
        if (!eng || !eng.addFilter) return;
        if (ic.checked) eng.addFilter('issue', function (tr) { return tr.getAttribute('data-issue') === '1'; });
        else eng.removeFilter('issue');
      });
    }
    countNoCode();
    countNoBrand();
    countIssues();
    wirePipeQtyTool();
    wireBrandBulk();
    chipifyAlarms();
    setTimeout(chipifyAlarms, 120);
    setTimeout(chipifyAlarms, 400);
    document.addEventListener('ft-flags-changed', countIssues);
    // Re-count rows without an FT code / BRAND whenever a row changes.
    document.addEventListener('ft-rows-changed', function () {
      countNoCode();
      countNoBrand();
      setTimeout(chipifyAlarms, 0);
    });
    document.addEventListener('input', function (e) {
      var td = e.target && e.target.closest ? e.target.closest('td[data-col-name="BRAND"]') : null;
      if (td) countNoBrand();
    });
  }

  function countIssues() {
    var el = document.getElementById('to-issue-count');
    if (!el) return;
    var nums = [];
    rows().forEach(function (tr) { if (tr.getAttribute('data-issue') === '1') nums.push(itemCodeOf(tr)); });
    setCountWithRows('to-issue-count', 'to-issue-rows', nums);
  }

  document.addEventListener('DOMContentLoaded', function () {
    // NOTE: the read-only PROFORMA REMARK column + its card are now built by
    // proforma_remark_column.js (single column at the far right). This file only
    // keeps the no-code / issue counters, the pipe-qty tool and alarm chips.
    setTimeout(function () { wire(false); }, 0);
  });
})(window, document);
