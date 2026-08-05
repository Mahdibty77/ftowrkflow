/* pi_same_code_sync.js — PI only.
 *
 * When several rows share the same FTCO code, editing UNIT PRICE / TIME on one
 * row copies that value to every other row with the same code, and marks those
 * cells with a small "same code" badge.
 *
 * BRAND and REMARK are intentionally NOT synced: they drive per-row Technical
 * Confirm/Reject rounds. Copying them (with lock side-effects) locked sibling
 * rows' TIME/price after Save→Edit even when those rows were never edited.
 */
(function (window, document) {
  'use strict';

  var KIND = (window.FT_TOOL_SAVE && window.FT_TOOL_SAVE.kind) || '';
  if (KIND !== 'PI') return;

  var CODE_COL = 'کد';
  var REMARK_COL = 'ریمارک';
  var SYNC_COLS = {
    'UNIT PRICE': true,
    'TIME': true
  };

  // No BRAND/REMARK side-effect sync (see file header).
  var SIDE_EFFECT_COLS = {};

  // Similarity badge lives only on the FTCO CODE cell (top-right).
  var syncing = false;

  var GROUP_COLORS = [
    'rgba(14, 165, 233, 0.20)',
    'rgba(168, 85, 247, 0.18)',
    'rgba(234, 179, 8, 0.22)',
    'rgba(34, 197, 138, 0.18)',
    'rgba(249, 115, 22, 0.18)',
    'rgba(236, 72, 153, 0.16)',
    'rgba(99, 102, 241, 0.18)',
    'rgba(20, 184, 166, 0.18)',
    'rgba(244, 63, 94, 0.16)',
    'rgba(132, 204, 22, 0.18)'
  ];

  function eng() { return window.VirtualScrollEngine || null; }
  function allRows() {
    var e = eng();
    return (e && e.getRows) ? e.getRows() : Array.prototype.slice.call(
      document.querySelectorAll('#virtual-scroll-tbody tr.row, #raw-excel-template tr.row')
    );
  }
  function cellByName(tr, name) {
    return tr.querySelector('td[data-col-name="' + (window.CSS ? CSS.escape(name) : name) + '"]')
      || (name === 'UNIT PRICE'
        ? tr.querySelector('td[data-calc-variable="unit_price"]')
        : null);
  }
  function cellText(td) {
    if (!td) return '';
    var f = td.querySelector('textarea,input,select');
    return (f ? f.value : td.textContent || '').trim();
  }

  function syncCodeOf(tr) {
    if (!tr) return '';
    var visible = cellText(cellByName(tr, CODE_COL)).trim();
    if (visible) return visible;
    return String(tr.getAttribute('data-saved-code') || '').trim();
  }

  function rowOk(tr) {
    if (!tr) return false;
    if (tr.getAttribute('data-deleted') === '1') return false;
    if (tr.classList && tr.classList.contains('row-soft-deleted')) return false;
    if (tr.getAttribute('data-unsuppliable') === '1') return false;
    return true;
  }

  function peersFor(tr, code) {
    if (!code) return [];
    var out = [];
    allRows().forEach(function (r) {
      if (!rowOk(r)) return;
      if (syncCodeOf(r) !== code) return;
      out.push(r);
    });
    return out;
  }

  function buildGroups() {
    var byCode = {};
    allRows().forEach(function (tr) {
      if (!rowOk(tr)) return;
      var c = syncCodeOf(tr);
      if (!c) return;
      (byCode[c] || (byCode[c] = [])).push(tr);
    });
    return byCode;
  }

  function paintSameBadge(td, on) {
    if (!td) return;
    td.style.position = td.style.position || 'relative';
    var tag = td.querySelector(':scope > .same-code-badge');
    if (!on) {
      if (tag) tag.remove();
      td.removeAttribute('data-same-code');
      return;
    }
    if (!tag) {
      tag = document.createElement('span');
      tag.className = 'same-code-badge';
      tag.setAttribute('title', 'Same FTCO code — values stay in sync');
      tag.innerHTML = '<i class="fa-solid fa-link" aria-hidden="true"></i>';
      td.appendChild(tag);
    }
    td.setAttribute('data-same-code', '1');
  }

  function paintCodeBadge(tr, on) {
    paintSameBadge(cellByName(tr, CODE_COL), on);
  }

  function markGroupCodeBadges(peerRows, on) {
    peerRows.forEach(function (r) { paintCodeBadge(r, on); });
  }

  /** Remove leftover badges from older versions that painted commercial cells. */
  function clearLegacyCommercialBadges(tr) {
    ['UNIT PRICE', 'BRAND', 'TIME', REMARK_COL].forEach(function (col) {
      paintSameBadge(cellByName(tr, col), false);
    });
  }

  function setSyncedText(tr, colName, value, applySideEffects) {
    var td = cellByName(tr, colName);
    if (!td) return;
    if (colName === 'TIME' && td.getAttribute('data-locked') === '1') return;
    var inp = td.querySelector('textarea, input');
    if (inp) {
      if (colName === 'TIME' && (inp.disabled || inp.readOnly)) return;
      inp.value = value == null ? '' : value;
      try {
        inp.dispatchEvent(new Event('input', { bubbles: !!applySideEffects }));
      } catch (e) {}
    } else {
      td.textContent = value == null ? '' : value;
    }
    paintCodeBadge(tr, true);
  }

  function setPeerUnit(tr, raw, source) {
    if (window.FT_TOOL_SAVE && window.FT_TOOL_SAVE.pricingLocked) return;
    if (tr.getAttribute('data-remark-cleared') === '1') return;
    if (tr.getAttribute('data-brand-cleared') === '1') return;
    if (tr.getAttribute('data-nocode-locked') === '1') return;
    var td = cellByName(tr, 'UNIT PRICE');
    if (td && td.getAttribute('data-locked') === '1') return;
    var num = parseFloat(String(raw == null ? '' : raw).replace(/[^0-9.\-]/g, ''));
    var has = isFinite(num) && num !== 0;
    var src = has ? (source || 'manual') : '';
    if (window.PISetUnitCell) {
      window.PISetUnitCell(tr, has ? num : '', src);
    } else if (td) {
      var f = td.querySelector('input.pi-unit-input, input, textarea');
      if (f) {
        f.value = has ? String(num) : '';
        if (f.dataset) f.dataset.raw = has ? String(num) : '';
      }
      if (has) {
        td.setAttribute('data-calc-base', String(num));
        td.setAttribute('data-calc-raw', String(num));
        td.setAttribute('data-price-source', src);
      } else {
        td.removeAttribute('data-calc-base');
        td.removeAttribute('data-calc-raw');
        td.removeAttribute('data-price-source');
      }
      if (window.PIPaintSource) window.PIPaintSource(td, src);
    }
    if (has && String(src).toLowerCase() === 'manual') {
      tr.setAttribute('data-unit-manual', '1');
    } else if (!has) {
      tr.removeAttribute('data-unit-manual');
    }
    paintCodeBadge(tr, true);
    if (window.CalculationControls && window.CalculationControls.refreshRow) {
      window.CalculationControls.refreshRow(tr);
    }
  }

  function syncColumn(sourceTr, colName) {
    if (syncing) return;
    if (!sourceTr || !SYNC_COLS[colName]) return;
    if (!rowOk(sourceTr)) return;

    var code = syncCodeOf(sourceTr);
    if (!code) {
      paintCodeBadge(sourceTr, false);
      return;
    }

    var peers = peersFor(sourceTr, code);
    if (peers.length < 2) {
      paintCodeBadge(sourceTr, false);
      return;
    }

    var srcTd = cellByName(sourceTr, colName);
    var value;
    var priceSrc = '';
    if (colName === 'UNIT PRICE') {
      value = (srcTd && (srcTd.getAttribute('data-calc-base') || srcTd.getAttribute('data-calc-raw'))) || '';
      if (!value) {
        var f = srcTd && srcTd.querySelector('input,textarea');
        value = (f && f.dataset && f.dataset.raw) || cellText(srcTd);
      }
      value = String(value || '').replace(/[^0-9.\-]/g, '');
      priceSrc = (srcTd && srcTd.getAttribute('data-price-source')) || 'manual';
    } else {
      value = cellText(srcTd);
    }

    var withSideEffects = !!SIDE_EFFECT_COLS[colName];

    syncing = true;
    try {
      peers.forEach(function (r) {
        if (r === sourceTr) {
          paintCodeBadge(r, true);
          return;
        }
        if (colName === 'UNIT PRICE') setPeerUnit(r, value, priceSrc);
        else setSyncedText(r, colName, value, withSideEffects);
      });
      if (colName === 'UNIT PRICE' && window.PIRefreshCalc) {
        window.PIRefreshCalc();
      }
    } finally {
      syncing = false;
    }
  }

  function colFromTd(td) {
    if (!td) return '';
    var n = td.getAttribute('data-col-name') || '';
    if (SYNC_COLS[n]) return n;
    if (td.getAttribute('data-calc-variable') === 'unit_price') return 'UNIT PRICE';
    return '';
  }

  function onInput(e) {
    if (syncing) return;
    if (e.target && (e.target.classList.contains('row-margin-percent')
        || (e.target.closest && e.target.closest('.row-margin-panel')))) {
      return;
    }
    var td = e.target.closest ? e.target.closest('td') : null;
    var col = colFromTd(td);
    if (!col) return;
    if (col === 'UNIT PRICE' && e.target && !e.target.classList.contains('pi-unit-input')
        && e.target.tagName !== 'TEXTAREA' && e.target.tagName !== 'INPUT') {
      return;
    }
    var tr = td.closest('tr');
    setTimeout(function () { syncColumn(tr, col); }, 0);
  }

  /** Badge only the FTCO CODE cell for every duplicate-code group. */
  function initialBadges() {
    var byCode = buildGroups();
    allRows().forEach(clearLegacyCommercialBadges);
    Object.keys(byCode).forEach(function (code) {
      var group = byCode[code];
      markGroupCodeBadges(group, group.length >= 2);
    });
  }

  function isSimilarRow(tr) {
    if (!rowOk(tr)) return false;
    var c = syncCodeOf(tr);
    if (!c) return false;
    var n = 0;
    allRows().forEach(function (r) {
      if (!rowOk(r)) return;
      if (syncCodeOf(r) === c) n++;
    });
    return n >= 2;
  }

  function countSimilarRows() {
    var n = 0;
    allRows().forEach(function (tr) { if (isSimilarRow(tr)) n++; });
    return n;
  }

  function clearGroupColors() {
    allRows().forEach(function (tr) {
      tr.classList.remove('same-code-hilite');
      tr.style.removeProperty('--same-code-bg');
      tr.removeAttribute('data-same-code-group');
    });
  }

  function paintGroupColors() {
    clearGroupColors();
    var byCode = buildGroups();
    var codes = Object.keys(byCode).filter(function (c) { return byCode[c].length >= 2; }).sort();
    codes.forEach(function (code, idx) {
      var color = GROUP_COLORS[idx % GROUP_COLORS.length];
      byCode[code].forEach(function (tr) {
        tr.classList.add('same-code-hilite');
        tr.style.setProperty('--same-code-bg', color);
        tr.setAttribute('data-same-code-group', String(idx + 1));
      });
    });
  }

  function boot() {
    var container = document.getElementById('excel-table-container');
    if (!container) return;
    container.addEventListener('input', onInput);
    container.addEventListener('focusout', function (e) {
      var td = e.target && e.target.closest ? e.target.closest('td') : null;
      var col = colFromTd(td);
      if (col !== 'UNIT PRICE' && col !== 'TIME') return;
      var tr = td && td.closest('tr');
      setTimeout(function () { syncColumn(tr, col); }, 0);
    });
    initialBadges();
    setTimeout(initialBadges, 80);
    setTimeout(initialBadges, 250);
    setTimeout(initialBadges, 700);
    document.addEventListener('ft-rows-changed', function () {
      setTimeout(initialBadges, 0);
    });
  }

  window.PISameCode = {
    refreshBadges: initialBadges,
    isSimilarRow: isSimilarRow,
    countSimilar: countSimilarRows,
    paintGroupColors: paintGroupColors,
    clearGroupColors: clearGroupColors,
    syncCodeOf: syncCodeOf
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { setTimeout(boot, 50); });
  } else {
    setTimeout(boot, 50);
  }
})(window, document);
