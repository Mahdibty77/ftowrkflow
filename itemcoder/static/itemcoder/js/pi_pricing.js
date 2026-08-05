/* pi_pricing.js — Proforma (PI) pricing layer. Loaded only on the PI tool.
 *
 * What it does (no coding logic is touched — it only reads/writes the price
 * cells and the FTCO-code cell):
 *   - Builds a pricing bar: a searchable price-list picker, a cross-list
 *     comparison + cheapest-list suggestion, and a grand TOTAL of all rows.
 *   - On picking a list, fills every row's UNIT PRICE from that list's price for
 *     the row's FTCO code, then TOTAL PRICE = UNIT PRICE x QTY, then the grand
 *     total. Each priced cell is tagged with the list name (or "manual").
 *   - A UNIT PRICE the user types becomes "manual"; pressing Delete/Backspace in
 *     an empty edit reverts it to the active list's price (or empty).
 *   - Typing anything in a row's REMARK clears that row's FTCO code (and its
 *     prices); clearing REMARK restores the code and re-applies the list price.
 *
 * Column handles use the stable canonical data-col-name / data-calc-variable.
 */
(function (window, document) {
  'use strict';

  var CODE_COL = 'کد';                 // FTCO CODE (the price key)
  var QTY_COL = 'qty';
  var REMARK_COL = 'ریمارک';
  var BRAND_COL = 'BRAND';
  var TIME_COL = 'TIME';
  var SVC_COL = 'SERVICE PRICE';
  var UNIT_VAR = 'unit_price';
  var TOTAL_VAR = 'total_price';

  var state = { lists: [], activeListId: null, prices: {}, currency: 'rial',
                group: '', featFilters: {} };

  // group -> [main feature names] (the fields shown in the filter for a group).
  var GROUP_FEATS = {};
  var GROUP_FEAT_ALIASES = {};
  var GROUP_FEAT_VALUES = {};
  try {
    var _gfEl = document.getElementById('ft-group-features');
    if (_gfEl) GROUP_FEATS = JSON.parse(_gfEl.textContent || '{}');
  } catch (e) { GROUP_FEATS = {}; }
  try {
    var _gaEl = document.getElementById('ft-group-feature-aliases');
    if (_gaEl) GROUP_FEAT_ALIASES = JSON.parse(_gaEl.textContent || '{}');
  } catch (e) { GROUP_FEAT_ALIASES = {}; }
  try {
    var _gvEl = document.getElementById('ft-group-feature-values');
    if (_gvEl) GROUP_FEAT_VALUES = JSON.parse(_gvEl.textContent || '{}');
  } catch (e) { GROUP_FEAT_VALUES = {}; }
  var GROUP_COMPOUND_MAP = {};
  try {
    var _gcEl = document.getElementById('ft-group-compound-map');
    if (_gcEl) GROUP_COMPOUND_MAP = JSON.parse(_gcEl.textContent || '{}');
  } catch (e) { GROUP_COMPOUND_MAP = {}; }
  // Members (ordered, per asign_code.json) of the compound column `feature`
  // belongs to, or null when it isn't part of one — same helper and same
  // source data as feature_filter.js (TO), so both filters agree on what's
  // compound-grouped for a given group.
  function compoundMembersFor(g, feature) {
    var map = GROUP_COMPOUND_MAP[String(g).toLowerCase()] || GROUP_COMPOUND_MAP[g] || {};
    return map[String(feature).toLowerCase()] || map[feature] || null;
  }

  function cfg() { return window.FT_TOOL_SAVE || {}; }
  function isExternalCurrency() {
    return !!(cfg().externalCurrency);
  }
  function defaultCurrency() {
    return isExternalCurrency() ? 'usd' : 'rial';
  }
  state.currency = defaultCurrency();
  // TO-only offer → all Proforma pricing is locked (no list, no manual price).
  function pricingLocked() { return !!cfg().pricingLocked; }
  function getCookie(name) {
    var v = null;
    (document.cookie || '').split(';').forEach(function (c) {
      c = c.trim();
      if (c.indexOf(name + '=') === 0) v = decodeURIComponent(c.slice(name.length + 1));
    });
    return v;
  }
  function rows() {
    return (window.VirtualScrollEngine && window.VirtualScrollEngine.getRows)
      ? window.VirtualScrollEngine.getRows() : [];
  }
  // Same principle as the TO filter (feature_filter.js): deleted rows must
  // never populate the group/feature dropdowns. Kept separate from rows()/
  // visibleRows(), which pricing totals, counts and the existing "show
  // deleted rows" toggle still use unchanged.
  function notDeleted(tr) { return tr.getAttribute('data-deleted') !== '1'; }
  function enumRows() { return rows().filter(notDeleted); }
  function visibleRows() {
    var eng = window.VirtualScrollEngine;
    if (eng && eng.getVisibleRows) return eng.getVisibleRows();
    return rows();
  }
  function cellByName(tr, name) {
    return tr.querySelector('td[data-col-name="' + (window.CSS ? CSS.escape(name) : name) + '"]');
  }
  function cellByVar(tr, v) {
    return tr.querySelector('td[data-calc-variable="' + (window.CSS ? CSS.escape(v) : v) + '"]');
  }
  function cellText(td) {
    if (!td) return '';
    var f = td.querySelector('textarea,input,select');
    return (f ? f.value : td.textContent || '').trim();
  }
  function codeOf(tr) {
    return tr.getAttribute('data-saved-code') || cellText(cellByName(tr, CODE_COL));
  }
  function qtyOf(tr) {
    var n = parseFloat(cellText(cellByName(tr, QTY_COL)).replace(/,/g, ''));
    return isFinite(n) ? n : 0;
  }
  // Effective display currency now comes from the Unit-conversion field (via
  // calculation_controls). Fall back to the case's inherent currency.
  function curUnit() {
    var u = (window.CalcCurrentCurrency ? window.CalcCurrentCurrency() : (state.currency || defaultCurrency()));
    if (u === 'rial' && isExternalCurrency()) u = 'usd';
    return u;
  }
  function dec() {
    if (window.CalcCurrentDecimals) {
      try { return window.CalcCurrentDecimals(); } catch (e) {}
    }
    var u = curUnit();
    return (u === 'rial') ? 0 : 2;
  }
  function curLabel() {
    var unit = curUnit();
    if (window.CalcCurrencySymbol) {
      try {
        var sym = window.CalcCurrencySymbol(unit);
        if (sym) return sym;
      } catch (e) {}
    }
    if (unit === 'usd') return '$';
    if (unit === 'eur') return '€';
    if (unit === 'rial') return 'Rial';
    return String(unit || '').toUpperCase();
  }
  function fmt(n) {
    if (!isFinite(n)) return '';
    return n.toLocaleString('en-US', { minimumFractionDigits: dec(), maximumFractionDigits: dec() });
  }

  // -- writing the derived price cells (display only; save reads data-calc-base) --
  function setUnitCell(tr, value, source) {
    var td = cellByVar(tr, UNIT_VAR) || cellByName(tr, 'UNIT PRICE');
    if (!td) return;
    var f = td.querySelector('input,textarea');
    var num = (value === '' || value == null) ? NaN : parseFloat(String(value).replace(/,/g, ''));
    var hasPrice = isFinite(num) && num !== 0;
    if (value === '' || value == null) {
      if (f) { f.value = ''; f.dataset.raw = ''; } else td.textContent = '';
      td.removeAttribute('data-calc-base');
      td.removeAttribute('data-calc-raw');
    } else {
      // Single-box display: number + currency unit (e.g. "1,234 Rial"). The raw
      // numeric stays in data-calc-base / input.dataset.raw for calculations.
      if (f) {
        if (document.activeElement === f) { f.value = String(num); }
        else { f.value = fmt(value) + ' ' + curLabel(); }
        f.dataset.raw = String(num);
      } else {
        td.textContent = fmt(value) + ' ' + curLabel();
      }
      td.setAttribute('data-calc-base', String(value));
      td.setAttribute('data-calc-raw', String(value));
    }
    // Paint the source chip: "Manual" or the chosen price-list name (e.g. Aria).
    // An empty / zero price has no source, so the chip is removed.
    if (hasPrice) {
      td.setAttribute('data-price-source', source || '');
      paintSource(td, source || '');
    } else {
      td.removeAttribute('data-price-source');
      paintSource(td, '');
    }
  }
  function totalValue(tr) {
    var td = cellByVar(tr, TOTAL_VAR) || cellByName(tr, 'TOTAL PRICE');
    if (!td) return NaN;
    var n = parseFloat((td.getAttribute('data-calc-value') || td.getAttribute('data-calc-raw') ||
                        cellText(td)).replace(/,/g, ''));
    return isFinite(n) ? n : NaN;
  }
  function setTotalCellFallback(tr) {
    // Only used when the calculation engine is not present on the page.
    var td = cellByVar(tr, TOTAL_VAR) || cellByName(tr, 'TOTAL PRICE');
    if (!td) return;
    var u = unitValue(tr);
    var f = td.querySelector('input,textarea');
    var v = isFinite(u) ? fmt(u * qtyOf(tr)) : '';
    if (f) f.value = v; else td.textContent = v;
  }
  function unitValue(tr) {
    var td = cellByVar(tr, UNIT_VAR) || cellByName(tr, 'UNIT PRICE');
    if (!td) return NaN;
    // Prefer the live calculated value (conversion + margins) so Grand Total
    // stays in the same To-currency as the painted UNIT / TOTAL cells.
    var calc = td.getAttribute('data-calc-value');
    if (calc != null && calc !== '') {
      var cn = parseFloat(String(calc).replace(/[^0-9.\-]/g, ''));
      if (isFinite(cn)) return cn;
    }
    var base = td.getAttribute('data-calc-base') || td.getAttribute('data-calc-raw');
    var f = td.querySelector('input,textarea');
    if ((base == null || base === '') && f && f.dataset && f.dataset.raw) base = f.dataset.raw;
    var src = (base != null && base !== '') ? base : cellText(td);
    var n = parseFloat(String(src).replace(/[^0-9.\-]/g, ''));
    return isFinite(n) ? n : NaN;
  }
  function paintSource(td, source) {
    if (!td) return;
    var tag = td.querySelector(':scope > .price-src');
    var label = String(source || '').trim();
    // Show the friendly label: "Manual" for a manual entry, otherwise the price
    // list's own name (e.g. "Aria"). Empty source => no chip.
    if (!label) { if (tag) tag.remove(); return; }
    var shown = (label.toLowerCase() === 'manual') ? 'Manual' : label;
    if (!tag) {
      tag = document.createElement('span');
      tag.className = 'price-src';
      td.appendChild(tag);
    }
    tag.textContent = shown;
    tag.setAttribute('data-kind', label.toLowerCase() === 'manual' ? 'manual' : 'list');
  }
  // Let pi_columns repaint the source chip when it re-injects unit inputs on reload.
  window.PIPaintSource = paintSource;
  // Same-FTCO sync (pi_same_code_sync.js) writes peer UNIT PRICE via this API.
  window.PISetUnitCell = setUnitCell;
  window.PIRefreshCalc = function () { refreshCalc(); recomputeGrand(); };

  // Shared across recomputeGrand (outer) and the filter-card block (inner).
  // Must live here — defining it only inside the cards IIFE left recomputeGrand
  // throwing ReferenceError: isActive is not defined, so Subtotal/VAT/Grand stayed 0.
  function isActive(tr) {
    return !!(tr
      && tr.getAttribute('data-deleted') !== '1'
      && tr.getAttribute('data-unsuppliable') !== '1');
  }

  var hasCalc = function () { return !!(window.CalculationControls && window.CalculationControls.refreshAll); };
  function refreshCalc() {
    if (hasCalc()) {
      window.CalculationControls.refreshAll();   // recomputes TOTAL PRICE (+ margins) from the new base
    } else {
      rows().forEach(setTotalCellFallback);
      recomputeGrand();
    }
  }
  function recomputeGrand() {
    var sum = 0;
    rows().forEach(function (tr) {
      // Not Suppliable rows (and deleted ones) must never contribute to
      // Subtotal/VAT/Grand Total — isActive() already expresses exactly
      // this rule and is already used elsewhere in this file (missing-unit
      // counts, etc.); this loop was the one place still summing every row
      // unconditionally.
      if (!isActive(tr)) return;
      var t = totalValue(tr);
      if (isFinite(t)) { sum += t; return; }
      var u = unitValue(tr);
      if (isFinite(u)) sum += u * qtyOf(tr);
    });
    var strip = document.getElementById('pi-grand-strip');
    var vatPct = 10;
    if (strip) {
      var rawPct = parseFloat(strip.getAttribute('data-vat-percent') || '10');
      if (isFinite(rawPct)) vatPct = rawPct;
    }
    var vat = sum * (vatPct / 100);
    // Service total is tracked separately (service_price.js) so Subtotal/VAT are
    // unit×qty based; display order is Subtotal → Total service → VAT → Grand.
    // Grand = Subtotal + VAT + Total service price.
    var svcSum = 0;
    if (typeof window.PIServiceTotalSum === 'function') {
      try { svcSum = parseFloat(window.PIServiceTotalSum()) || 0; } catch (_e) { svcSum = 0; }
    }
    if (!isFinite(svcSum) || svcSum < 0) svcSum = 0;
    var grand = sum + vat + svcSum;
    var unit = curLabel();
    var subEl = document.getElementById('pi-subtotal');
    var vatEl = document.getElementById('pi-vat-total');
    var grandEl = document.getElementById('pi-grand-total');
    var vatLabel = document.getElementById('pi-vat-label');
    var svcPill = document.getElementById('pi-svc-total-pill');
    var svcEl = document.getElementById('pi-svc-total');
    if (subEl) subEl.textContent = fmt(sum) + ' ' + unit;
    if (vatEl) vatEl.textContent = fmt(vat) + ' ' + unit;
    if (svcPill && svcEl) {
      // Visibility is owned by service_price.js (any attached comment);
      // here we only keep the amount in sync when the pill is shown.
      if (!svcPill.hidden) svcEl.textContent = fmt(svcSum) + ' ' + unit;
    }
    if (grandEl) grandEl.textContent = fmt(grand) + ' ' + unit;
    if (vatLabel) {
      vatLabel.textContent = 'VAT (' + String(vatPct).replace(/\.0+$/, '') + '%)';
    }
    state._subtotal = sum;
    // Target-margin math uses line totals (pre-VAT); the strip shows VAT-inclusive grand.
    state._grand = sum;
    if (state._recomputeTarget) state._recomputeTarget();
    countMissingUnit();
    updateStatusBar();
    if (state._updateFilterCount) state._updateFilterCount();
    return grand;
  }
  // Used by service_price.js after it adjusts per-row service lines without
  // a full CalculationControls.refreshAll (avoids double work).
  window.PIRecomputeGrandOnly = recomputeGrand;

  function applyList(listId) {
    if (pricingLocked()) return;   // TO-only offer: no pricing allowed
    state.activeListId = listId;
    var list = state.lists.filter(function (l) { return String(l.id) === String(listId); })[0];
    state.currency = (list && list.currency) || defaultCurrency();
    if (isExternalCurrency() && state.currency === 'rial') state.currency = 'usd';
    // Snapshot UNIT PRICE on the rows we're about to reprice (for Ctrl+Z).
    if (window.FT_UNDO && window.FT_UNDO.capture) {
      window.FT_UNDO.capture('apply price list', ['UNIT PRICE'], visibleRows());
    }
    fetchPrices(listId).then(function (data) {
      state.prices = data.prices || {};
      // Price ONLY the currently filtered/visible rows (per requirement).
      visibleRows().forEach(function (tr) {
        if (tr.getAttribute('data-remark-cleared') === '1') return; // remark wins
        if (tr.getAttribute('data-unit-manual') === '1') return;    // keep manual price
        var p = state.prices[codeOf(tr)];
        if (p != null) setUnitCell(tr, p, list ? list.name : '');
        else setUnitCell(tr, '', '');
      });
      refreshCalc();
      recomputeGrand();
      renderComparison(data.comparison || [], data.suggestion || null);
    });
  }

  // Apply one manually-typed unit price to every filtered/visible row.
  function applyManualBulk(value) {
    if (pricingLocked()) return;   // TO-only offer: no pricing allowed
    var n = parseFloat(String(value).replace(/,/g, ''));
    if (!isFinite(n)) return;
    if (window.FT_UNDO && window.FT_UNDO.capture) {
      window.FT_UNDO.capture('apply manual price', ['UNIT PRICE'], visibleRows());
    }
    visibleRows().forEach(function (tr) {
      if (tr.getAttribute('data-remark-cleared') === '1') return;
      setUnitCell(tr, n, 'manual');
      tr.setAttribute('data-unit-manual', '1');
    });
    refreshCalc();
    recomputeGrand();
  }

  // Apply one plain-text value (Brand / Delivery time) to every filtered row.
  function applyTextBulk(colName, value) {
    var v = String(value == null ? '' : value).trim();
    if (window.FT_UNDO && window.FT_UNDO.capture) {
      window.FT_UNDO.capture('apply ' + (colName === 'BRAND' ? 'brand' : 'time'), [colName], visibleRows());
    }
    visibleRows().forEach(function (tr) {
      var cell = tr.querySelector('td[data-col-name="' + colName + '"]');
      if (!cell) return;
      var inp = cell.querySelector('input.cell-input, textarea.cell-input, input[type="text"]');
      if (inp) {
        inp.value = v;
        inp.dispatchEvent(new Event('input', { bubbles: true }));
      } else cell.textContent = v;
    });
  }

  function fetchPrices(listId) {
    var items = rows().map(function (tr) { return { code: codeOf(tr), qty: qtyOf(tr) }; })
      .filter(function (x) { return x.code; });
    var body = new URLSearchParams();
    body.set('items', JSON.stringify(items));
    if (listId) body.set('list_id', listId);
    if (isExternalCurrency()) body.set('external', '1');
    return fetch('/tool/prices/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRFToken': cfg().csrfToken || getCookie('csrftoken') || '' },
      body: body.toString()
    }).then(function (r) { return r.json(); }).catch(function () { return {}; });
  }

  // Fill in per-row feature values (data-vars) for rows that don't already have
  // them, by looking each FTCO code up in the code DB. This makes the feature
  // filter work for forms saved before feature values were persisted.
  function hydrateFeatures(forceGroup) {
    var need = [], items = [];
    var gl = forceGroup ? String(forceGroup).toLowerCase() : '';
    var names = gl ? knownFeats(forceGroup) : [];
    rows().forEach(function (tr) {
      if (gl && (tr.getAttribute('data-group') || '').trim().toLowerCase() !== gl) return;
      // Prefer resolving from existing data-vars / Filled_Features first.
      if (names.length) {
        var vars0 = rowVars(tr);
        var grp0 = tr.getAttribute('data-group') || '';
        var type0 = tr.getAttribute('data-type') || '';
        var hasAny = names.some(function (n) {
          return String(resolveFeatureValue(vars0, n, grp0, type0, names) || '').trim() !== '';
        });
        if (hasAny) return;
      } else {
        var vars = {};
        try { vars = JSON.parse(tr.getAttribute('data-vars') || '{}'); } catch (e) {}
        if (Object.keys(vars).length) return;
      }
      var code = cellText(cellByName(tr, CODE_COL));
      var grp = (tr.getAttribute('data-group') || '').trim();
      if (code && grp) { need.push(tr); items.push({ group: grp, code: code }); }
    });
    if (!items.length) return Promise.resolve(false);
    var body = new URLSearchParams();
    body.set('items', JSON.stringify(items));
    return fetch('/tool/features/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRFToken': cfg().csrfToken || getCookie('csrftoken') || '' },
      body: body.toString()
    }).then(function (r) { return r.json(); }).then(function (data) {
      var map = (data && data.features) || {};
      var changed = false;
      need.forEach(function (tr) {
        var code = cellText(cellByName(tr, CODE_COL));
        if (map[code]) {
          var existing = {};
          try { existing = JSON.parse(tr.getAttribute('data-vars') || '{}'); } catch (e) { existing = {}; }
          var merged = Object.assign({}, existing, map[code]);
          tr.setAttribute('data-vars', JSON.stringify(merged));
          changed = true;
        }
      });
      return changed;
    }).catch(function () { return false; });
  }

  // ---------------------------- UI ----------------------------
  function localDisplay(v) {
    var s = String(v == null ? '' : v).trim();
    var m = s.match(/^\((?:no|بدون)\)\s*(.+)$/i) || s.match(/^\$(.+)\$$/);
    if (m) {
      var inner = String(m[1] || "").replace(/^no\s*/i, "").trim() || String(m[1] || "");
      return ("NO " + inner).toUpperCase();
    }
    return s.toUpperCase();
  }

  // Icon + label formatting for the Group & Feature filter combos, ported
  // verbatim from feature_filter.js (TO) so both filters look and read
  // identically — previously PI had neither: makeCombo took no icon at all,
  // and labels went through localDisplay (built for VALUES — "(no)coating"
  // -> "NO COATING" — which uppercases everything), so a feature name like
  // "material_type" rendered as "MATERIAL_TYPE" instead of "Material Type".
  function featIcon(name) {
    var n = String(name).toLowerCase();
    if (n.indexOf('material') === 0) return 'fa-cube';
    if (n.indexOf('size') === 0) return 'fa-ruler';
    if (n.indexOf('production') === 0 || n.indexOf('method') >= 0) return 'fa-gears';
    if (n.indexOf('phisic') === 0 || n.indexOf('sch') >= 0 || n.indexOf('schedule') >= 0) return 'fa-layer-group';
    if (n.indexOf('coating') >= 0) return 'fa-paint-roller';
    if (n.indexOf('standard') >= 0 || n.indexOf('spec') === 0) return 'fa-clipboard-check';
    if (n.indexOf('grade') >= 0) return 'fa-medal';
    if (n.indexOf('type') >= 0) return 'fa-shapes';
    return 'fa-tag';
  }
  function prettyLabel(key) {
    return String(key || '')
      .replace(/^or\d+_/i, '')
      .split(/[_\s]+/)
      .filter(Boolean)
      .map(function (w) {
        return /^[a-z]/i.test(w) ? w.charAt(0).toUpperCase() + w.slice(1).toLowerCase() : w;
      })
      .join(' ');
  }

  // A small reusable searchable dropdown.
  function makeCombo(label, placeholder, icon) {
    var wrap = document.createElement('div');
    wrap.className = 'pi-combo';
    var ic = icon ? ('<i class="fa-solid ' + icon + '"></i> ') : '';
    wrap.innerHTML = '<label>' + ic + label + '</label>' +
      '<input type="text" placeholder="' + placeholder + '" autocomplete="off">' +
      '<div class="pi-menu" hidden></div>';
    var input = wrap.querySelector('input');
    var menu = wrap.querySelector('.pi-menu');
    var getItems = function () { return []; };
    var onPick = function () {};
    function render(q) {
      q = (q || '').toLowerCase();
      var items = getItems().filter(function (it) { return it.label.toLowerCase().indexOf(q) >= 0; });
      menu.innerHTML = items.length
        ? items.map(function (it) { return '<div data-val="' + encodeURIComponent(it.value) + '">' + it.label + (it.hint ? ' <em>' + it.hint + '</em>' : '') + '</div>'; }).join('')
        : '<div class="pi-empty">No matches</div>';
    }
    input.addEventListener('focus', function () { render(input.value); menu.hidden = false; });
    input.addEventListener('input', function () { render(input.value); menu.hidden = false; });
    input.addEventListener('blur', function () { setTimeout(function () { menu.hidden = true; }, 150); });
    // Delete / Backspace clears the current selection and removes this field's
    // filter (like deselecting "all" in the archive filters). Works whether the
    // field is empty OR currently shows a picked value.
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Delete' || e.key === 'Backspace') {
        // If the user is mid-typing a multi-char query, let Backspace edit it;
        // only treat it as "clear" when the field shows a committed value
        // (selectionStart===selectionEnd at end and value matches a known item)
        // — simplest robust rule: clear on Delete always, and on Backspace when
        // the caret would empty the field or a value is already committed.
        if (e.key === 'Delete' || !input.value || input.dataset.committed === '1') {
          e.preventDefault();
          menu.hidden = true;
          input.value = '';
          input.dataset.committed = '';
          onPick('');
        }
      }
    });
    menu.addEventListener('mousedown', function (e) {
      var it = e.target.closest('[data-val]'); if (!it) return;
      var val = decodeURIComponent(it.getAttribute('data-val'));
      input.value = it.textContent.trim(); menu.hidden = true;
      input.dataset.committed = '1';   // a value is selected → Delete clears it
      onPick(val);
    });
    return {
      el: wrap, input: input,
      setItems: function (fn) { getItems = fn; },
      onPick: function (fn) { onPick = fn; },
      clear: function () { input.value = ''; onPick(''); },
      refreshItems: function () { if (!menu.hidden) render(input.value); }
    };
  }

  function distinctGroups() {
    var seen = {}, out = [];
    enumRows().forEach(function (tr) {
      var g = (tr.getAttribute('data-group') || '').trim();
      // Case-insensitive de-dupe (matches feature_filter.js / TO): without
      // this, the same group saved with inconsistent casing across rows
      // (e.g. "Pipe" on one row, "PIPE" on another) showed up as two
      // separate, redundant entries in the PI dropdown instead of one.
      if (g && !seen[g.toLowerCase()]) { seen[g.toLowerCase()] = 1; out.push(g); }
    });
    return out.sort();
  }
  // Feature_Variables keys are suffixed by group/type (e.g. material_type_pipe_pipe),
  // while filter fields use the base name (material_type). Resolve either form,
  // mirroring the backend candidate order in code_assigner._resolve_feature_value.
  // Each row's feature values, merged from data-vars AND the Filled_Features
  // cell (which renders "key = value" pairs like material_type_pipe_pipe = c.s).
  // Parsing the cell makes the filter work even when data-vars is empty.
  function parseFilled(tr) {
    var cell = tr.querySelector('td[data-col-name="Filled_Features"]');
    if (!cell) return {};
    var out = {};
    var html = cell.innerHTML || '';
    var parts = html.split(/<br\s*\/?>/i);
    if (parts.length > 1) {
      parts.forEach(function (part) {
        var tmp = document.createElement('div');
        tmp.innerHTML = part;
        var text = (tmp.textContent || '').trim();
        var eq = text.indexOf('=');
        if (eq > 0) {
          var k = text.slice(0, eq).trim();
          var v = text.slice(eq + 1).trim();
          if (k && v) out[k] = v;
        }
      });
    }
    // Glued / no-<br> cell: split on keys ending with _<group>_<type> or <group>_type.
    if (Object.keys(out).length <= 1) {
      var full = (cell.textContent || '').trim();
      var group = (tr.getAttribute('data-group') || '').trim().toLowerCase();
      var type = (tr.getAttribute('data-type') || '').trim().toLowerCase();
      function esc(s) { return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
      var matches = [];
      if (group) {
        var keyRe;
        if (type) {
          // ROOT CAUSE FIX — see the matching comment in feature_filter.js's
          // parseFilled for the full explanation: [a-z] (not [A-Za-z]) for
          // the leading character, no 'i' flag. Prevents an uppercased
          // value's trailing letter ("C.S" -> the "S") from being
          // misinterpreted as the start of the next key when a saved cell
          // has lost its <br> separators.
          keyRe = new RegExp(
            '([a-z][A-Za-z0-9_]*_' + esc(group) + '_' + esc(type) + '|' + esc(group) + '_type)\\s*=\\s*',
            'g'
          );
        } else {
          keyRe = new RegExp('([a-z][A-Za-z0-9_]*_' + esc(group) + '(?:_[A-Za-z0-9]+)?)\\s*=\\s*', 'g');
        }
        var m;
        while ((m = keyRe.exec(full)) !== null) {
          matches.push({ key: m[1], valueStart: m.index + m[0].length, start: m.index });
        }
      }
      if (!matches.length) {
        var re2 = /([a-z][A-Za-z0-9_]{2,})\s*=\s*/g, m2;
        while ((m2 = re2.exec(full)) !== null) {
          matches.push({ key: m2[1], valueStart: m2.index + m2[0].length, start: m2.index });
        }
      }
      for (var i = 0; i < matches.length; i++) {
        var end = (i + 1 < matches.length) ? matches[i + 1].start : full.length;
        var vv = full.slice(matches[i].valueStart, end).trim();
        if (matches[i].key && vv) out[matches[i].key] = vv;
      }
    }
    return out;
  }
  function rowVars(tr) {
    var vars = {};
    try {
      vars = JSON.parse(tr.getAttribute('data-vars') || '{}');
      if (typeof vars === 'string') {
        try { vars = JSON.parse(vars); } catch (e2) { vars = {}; }
      }
    } catch (e) { vars = {}; }
    if (!vars || typeof vars !== 'object' || Array.isArray(vars)) vars = {};
    var filled = parseFilled(tr);
    Object.keys(filled).forEach(function (k) {
      if (vars[k] == null || String(vars[k]).trim() === '') vars[k] = filled[k];
    });
    return vars;
  }

  function resolveFeatureValue(vars, name, group, type, knownFeatures) {
    if (!vars) return '';
    var tries = namesToTry(name, group);
    var known = knownFeatsExpanded(group, knownFeatures);
    for (var t = 0; t < tries.length; t++) {
      var hit = resolveFeatureValueOne(vars, tries[t], group, type, known);
      if (hit != null && String(hit).trim() !== '') return hit;
    }
    return '';
  }

  /** resolveFeatureValue(), but for a compound representative ("material")
   * joins every member's own resolved value in asign_code.json's declared
   * order ("ASTM A106" + "Gr.B" -> "ASTM A106 Gr.B") — same behavior and
   * same reasoning as feature_filter.js's identical helper (TO), so both
   * filters resolve a compound feature identically. */
  function resolveMaybeCompound(vars, key, grp, type, known) {
    var members = compoundMembersFor(grp, key);
    if (!members || members.length < 2) return resolveFeatureValue(vars, key, grp, type, known);
    var parts = [];
    members.forEach(function (m) {
      var v = resolveFeatureValue(vars, m, grp, type, known);
      if (v != null && String(v).trim() !== '' && String(v).trim().toLowerCase() !== 'null') parts.push(String(v));
    });
    return parts.join(' ');
  }

  function resolveFeatureValueOne(vars, name, group, type, knownFeatures) {
    if (!vars) return '';
    var f = String(name).toLowerCase();
    var g = String(group || '').toLowerCase();
    var t = String(type || '').toLowerCase();
    var cands = [f + '_' + g + '_' + t, f + '_' + t + '_' + g, f + '_' + g, f + '_' + t, f];
    var i, v;
    function ok(val) {
      if (val == null) return false;
      var s = String(val).trim();
      return s !== '' && s.toLowerCase() !== 'null';
    }
    for (i = 0; i < cands.length; i++) {
      v = vars[cands[i]];
      if (ok(v)) return v;
    }
    var lk = {};
    Object.keys(vars).forEach(function (k) { lk[String(k).toLowerCase()] = k; });
    for (i = 0; i < cands.length; i++) {
      var rk = lk[cands[i]];
      if (rk != null && ok(vars[rk])) return vars[rk];
    }
    var feats = (knownFeatures || []).map(function (n) { return String(n).toLowerCase(); });
    function ownerOf(kl) {
      var best = '', bestLen = -1;
      for (var j = 0; j < feats.length; j++) {
        var nm = feats[j];
        if (kl === nm || kl.indexOf(nm + '_') === 0) {
          if (nm.length > bestLen) { bestLen = nm.length; best = nm; }
        }
      }
      return best;
    }
    var keys = Object.keys(vars);
    for (i = 0; i < keys.length; i++) {
      var kl = String(keys[i]).toLowerCase();
      if (String(keys[i]).indexOf('__') === 0) continue;
      if (kl !== f && kl.indexOf(f + '_') !== 0) continue;
      var owner = ownerOf(kl);
      if (feats.length && owner && owner !== f) continue;
      if (ok(vars[keys[i]])) return vars[keys[i]];
    }
    return '';
  }

  // Schema MAIN feature names for a group (#ft-group-features).
  function knownFeats(g) {
    return GROUP_FEATS[String(g).toLowerCase()] || GROUP_FEATS[g] || [];
  }
  function namesToTry(name, group) {
    var out = [String(name || '')];
    var gl = String(group || '').toLowerCase();
    var map = GROUP_FEAT_ALIASES[gl] || GROUP_FEAT_ALIASES[group] || {};
    var als = map[name] || map[String(name)] || [];
    for (var i = 0; i < als.length; i++) {
      if (als[i] && out.indexOf(als[i]) < 0) out.push(als[i]);
    }
    var soft = String(name || '').trim().toLowerCase().replace(/\s+/g, '_');
    if (soft && out.indexOf(soft) < 0) out.push(soft);
    return out;
  }
  function knownFeatsExpanded(g, knownFeatures) {
    var names = (knownFeatures && knownFeatures.length) ? knownFeatures.slice() : knownFeats(g).slice();
    var gl = String(g || '').toLowerCase();
    var map = GROUP_FEAT_ALIASES[gl] || GROUP_FEAT_ALIASES[g] || {};
    names.slice().forEach(function (n) {
      var als = map[n] || [];
      for (var i = 0; i < als.length; i++) {
        if (als[i] && names.indexOf(als[i]) < 0) names.push(als[i]);
      }
    });
    return names;
  }
  /** Collapse compound-column siblings into ONE representative entry — same
   * function and same reasoning as feature_filter.js's identical helper. */
  function collapseCompoundFeatNames(g, names) {
    var seen = {}, out = [];
    names.forEach(function (name) {
      var members = compoundMembersFor(g, name);
      if (!members || members.length < 2) { out.push(name); return; }
      var gkey = members.slice().sort().join('|');
      if (seen[gkey]) return;
      seen[gkey] = true;
      out.push(name);
    });
    return out;
  }

  function featNamesForGroup(g) {
    var names = knownFeats(g);
    if (names && names.length) return collapseCompoundFeatNames(g, names.slice());
    // Fallback only when schema list is missing.
    var gl = String(g || '').toLowerCase();
    var seen = {}, out = [];
    enumRows().forEach(function (tr) {
      if ((tr.getAttribute('data-group') || '').trim().toLowerCase() !== gl) return;
      var vars = rowVars(tr);
      Object.keys(vars).forEach(function (k) {
        if (k.indexOf('__') === 0 || k.indexOf('display_') === 0) return;
        var base = k.replace(new RegExp('_' + gl + '(?:_[a-z0-9]+)?$', 'i'), '');
        if (base === k) base = k.replace(/_[a-z0-9]+_[a-z0-9]+$/i, '');
        base = String(base || '').toLowerCase();
        if (base && !seen[base]) { seen[base] = 1; out.push(base); }
      });
    });
    return collapseCompoundFeatNames(g, out);
  }
  function schemaAllowed(g, key) {
    var map = GROUP_FEAT_VALUES[String(g).toLowerCase()] || GROUP_FEAT_VALUES[g] || {};
    return map[key] || map[String(key)] || null;
  }
  function normFeatVal(s) {
    return String(s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  }
  function valueAllowed(g, key, value) {
    // Same reasoning as feature_filter.js: a compound representative's value
    // is a joined string ("ASTM A106 Gr.B") — the schema's allowed-values
    // list for that key only ever describes the individual member's own raw
    // values, so checking a joined value against it would reject everything.
    if (compoundMembersFor(g, key) && compoundMembersFor(g, key).length >= 2) return true;
    var allowed = schemaAllowed(g, key);
    if (!allowed || !allowed.length) return true;
    var nv = normFeatVal(value);
    if (!nv) return false;
    function compact(s) { return String(s || '').replace(/[\s.\-_/]/g, '').toLowerCase(); }
    var nCompact = compact(nv);
    for (var i = 0; i < allowed.length; i++) {
      var a = normFeatVal(allowed[i]);
      if (!a) continue;
      if (a === nv) return true;
      if (a.indexOf(nv) === 0 || nv.indexOf(a) === 0) return true;
      if (compact(a) === nCompact) return true;
    }
    return false;
  }

  function featuresForGroup(g) {
    var names = featNamesForGroup(g);
    var gl = String(g || '').toLowerCase();
    var acc = {};
    names.forEach(function (n) { acc[n] = {}; });
    rows().forEach(function (tr) {
      if ((tr.getAttribute('data-group') || '').trim().toLowerCase() !== gl) return;
      var type = tr.getAttribute('data-type') || '';
      var grp = tr.getAttribute('data-group') || '';
      var vars = rowVars(tr);
      names.forEach(function (n) {
        var v = resolveMaybeCompound(vars, n, grp, type, names);
        if (v !== '' && v != null && String(v).trim().toLowerCase() !== 'null') acc[n][String(v)] = 1;
      });
    });
    var out = {};
    names.forEach(function (n) { out[n] = Object.keys(acc[n]); });
    return out;
  }
  function updateFilterCount() {
    var el = document.getElementById('ft-rowcount-num');
    if (!el) return;
    var eng = window.VirtualScrollEngine;
    var vis = (eng && eng.getVisibleRows) ? eng.getVisibleRows() : rows();
    el.textContent = vis.length;
  }
  state._updateFilterCount = updateFilterCount;

  function applyFilter() {
    var eng = window.VirtualScrollEngine;
    if (!eng || !eng.addFilter) return;
    var g = state.group, feats = state.featFilters;
    if (!g && !Object.keys(feats).length) { eng.removeFilter('feat'); recomputeGrand(); updateFilterCount(); return; }
    var kf = featNamesForGroup(g);
    eng.addFilter('feat', function (tr) {
      if (g && (tr.getAttribute('data-group') || '').trim().toLowerCase() !== String(g).toLowerCase()) return false;
      var keys = Object.keys(feats);
      if (keys.length) {
        var vars = rowVars(tr);
        var grp = tr.getAttribute('data-group') || '';
        var type = tr.getAttribute('data-type') || '';
        for (var i = 0; i < keys.length; i++) {
          var rowVal = resolveMaybeCompound(vars, keys[i], grp, type, kf);
          if (String(rowVal) !== String(feats[keys[i]])) return false;
        }
      }
      return true;
    });
    updateFilterCount();
  }

  // The visible Item Code value (row number) for a PI row.
  function piItemCode(tr) {
    var cell = tr.querySelector('td[data-col-name="Item Code"]');
    if (!cell) return '';
    var num = cell.querySelector('.ic-num');
    return (num ? num.textContent : cell.textContent || '').trim();
  }

  function countMissingUnit() {
    // Count ALL rows where UNIT PRICE is zero or empty, regardless of reason.
    // Only the COUNT is shown here (no per-row list — that would be a huge line);
    // the "not suppliable" card is the one that lists row numbers.
    var n = 0;
    rows().forEach(function (tr) {
      var v = unitValue(tr);
      if (!isFinite(v) || v === 0) n++;
    });
    var el = document.getElementById('pi-nounit-count');
    if (el) el.textContent = n;
    var list = document.getElementById('pi-nounit-rows');
    if (list) { list.textContent = ''; list.style.display = 'none'; }
    return n;
  }

  // Distinct values of ONE feature, taken only from rows that already pass the
  // group + every OTHER active feature filter. This makes the combos cascade:
  // choosing one value narrows the choices left in the others.
  function valuesForField(g, key) {
    var names = featNamesForGroup(g);
    if (names.indexOf(key) < 0) names = names.concat(key);
    var gl = String(g || '').toLowerCase();
    var others = Object.keys(state.featFilters).filter(function (k) { return k !== key; });
    var seen = {};
    var out = [];
    enumRows().forEach(function (tr) {
      if ((tr.getAttribute('data-group') || '').trim().toLowerCase() !== gl) return;
      var grp = tr.getAttribute('data-group') || '';
      var type = tr.getAttribute('data-type') || '';
      var vars = rowVars(tr);
      for (var i = 0; i < others.length; i++) {
        if (String(resolveMaybeCompound(vars, others[i], grp, type, names)) !== String(state.featFilters[others[i]])) return;
      }
      var v = resolveMaybeCompound(vars, key, grp, type, names);
      if (v !== '' && v != null && String(v).trim().toLowerCase() !== 'null'
          && valueAllowed(g, key, v) && !seen[v]) {
        seen[v] = 1; out.push(String(v));
      }
    });
    return out.sort();
  }

  function refreshFilterCombos() {
    if (state._featCombos) {
      Object.keys(state._featCombos).forEach(function (k) {
        var combo = state._featCombos[k];
        if (combo && combo.refreshItems) combo.refreshItems();
      });
    }
  }

  function buildFilterRow() {
    var box = document.getElementById('pi-filter-feats');
    box.innerHTML = '';
    state.featFilters = {};
    if (!state.group) return;
    // Fields = data.json bases (+ any extra bases present on rows, e.g. phisic_sch).
    var names = featNamesForGroup(state.group);
    if (!names.length) {
      names = Object.keys(featuresForGroup(state.group));
    }
    if (!names.length) {
      box.innerHTML = '<div class="pi-feat-hint">This group has no main features.</div>';
      return;
    }
    state._featCombos = {};
    names.forEach(function (key) {
      var combo = makeCombo(prettyLabel(key), 'Type or pick…', featIcon(key));
      combo.setItems(function () {
        return valuesForField(state.group, key).map(function (v) { return { value: v, label: localDisplay(v) }; });
      });
      combo.onPick(function (val) {
        if (val === '' || val == null) delete state.featFilters[key]; else state.featFilters[key] = val;
        applyFilter();
        refreshFilterCombos();
      });
      if (combo.input) {
        combo.input.addEventListener('keydown', function (e) {
          if ((e.key === 'Delete' || e.key === 'Backspace') && !combo.input.value) {
            delete state.featFilters[key];
            applyFilter();
            refreshFilterCombos();
          }
        });
      }
      state._featCombos[key] = combo;
      box.appendChild(combo.el);
    });
  }

  function buildBar() {
    if (document.getElementById('pi-pricing-bar')) return;
    // Place the toolbar above the grand-total strip so the order is:
    // chips/panels  ->  grand total  ->  table  (grand total always sits right
    // on top of the table).
    var anchor = document.getElementById('pi-grand-strip') || document.getElementById('excel-table-container');
    if (!anchor) return;
    var bar = document.createElement('section');
    bar.id = 'pi-pricing-bar';
    bar.innerHTML =
      '<div id="pi-status-bar" class="pi-status-bar" hidden></div>' +
      '<div class="pi-chips">' +
        '<button type="button" class="pi-chip" data-target="pi-panel-filter"><i class="fa-solid fa-filter"></i> Filter</button>' +
        '<button type="button" class="pi-chip" data-target="pi-panel-pricing"><i class="fa-solid fa-tags"></i> Pricing &amp; fields</button>' +
        '<button type="button" class="pi-chip" data-target="calculation-control-card"><i class="fa-solid fa-calculator"></i> Calculation &amp; margin</button>' +
      '</div>' +
      '<div id="pi-panel-filter" class="pi-panel" hidden>' +
        '<div class="pi-filter-head">' +
          '<span class="pi-filter-title"><i class="fa-solid fa-sliders"></i> Group &amp; feature filter</span>' +
          '<button type="button" id="pi-filter-clear" class="pi-chip-clear"><i class="fa-solid fa-xmark"></i> Clear all</button>' +
        '</div>' +
        '<div class="pi-filter-row"><div id="pi-filter-group" class="pi-filter-group-col"></div><div id="pi-filter-feats" class="pi-feats"></div></div>' +
        '<div id="pi-filter-cards" class="pi-fcards"></div>' +
      '</div>' +
      '<div id="pi-panel-pricing" class="pi-panel" hidden>' +
        '<div class="pi-row pi-fields-grid">' +
          '<div id="pi-list-host"></div>' +
          '<div class="pi-field"><label><i class="fa-solid fa-pen"></i> Manual unit price</label>' +
            '<div class="pi-field-row"><input id="pi-manual-input" type="text" inputmode="decimal" placeholder="2,500"><button type="button" id="pi-manual-apply" class="pi-btn">Apply</button></div></div>' +
          '<div class="pi-field"><label><i class="fa-solid fa-copyright"></i> Brand</label>' +
            '<div class="pi-field-row"><input id="pi-brand-input" type="text" placeholder="Brand"><button type="button" id="pi-brand-apply" class="pi-btn">Apply</button></div></div>' +
          '<div class="pi-field"><label><i class="fa-solid fa-clock"></i> Delivery time</label>' +
            '<div class="pi-field-row"><input id="pi-time-input" type="text" placeholder="8 weeks"><button type="button" id="pi-time-apply" class="pi-btn">Apply</button></div></div>' +
        '</div>' +
        '<div id="pi-suggestion" class="pi-suggestion" hidden></div>' +
        '<div id="pi-comparison" class="pi-comparison"></div>' +
      '</div>';
    anchor.parentNode.insertBefore(bar, anchor);

    // Fold the calc card into the collapsible system (hidden until its chip is
    // clicked) and move it next to the other panels so nothing stacks tall.
    var calcCardEl = document.getElementById('calculation-control-card');
    if (calcCardEl) {
      calcCardEl.classList.add('pi-panel');
      calcCardEl.hidden = true;
      bar.appendChild(calcCardEl);
    }

    // Each chip independently shows/hides its own panel (several may be open).
    bar.querySelectorAll('.pi-chip').forEach(function (chip) {
      chip.addEventListener('click', function () {
        var panel = document.getElementById(chip.getAttribute('data-target'));
        if (!panel) return;
        panel.hidden = !panel.hidden;
        chip.classList.toggle('active', !panel.hidden);
      });
    });

    // Move the "Target total -> required margin %" tool INTO the calc card so the
    // pricing bar stays compact (requirement #6). Prefer the dedicated left host.
    var calcCard = document.getElementById('calculation-control-card');
    if (calcCard && !document.getElementById('pi-target-input')) {
      var tgt = document.createElement('div');
      tgt.className = 'calc-section-inner';
      tgt.innerHTML =
        '<div class="calc-section-title"><i class="fa-solid fa-bullseye"></i> Target total → margin %</div>' +
        '<div class="pi-field-row"><input id="pi-target-input" type="text" inputmode="decimal" placeholder="e.g. 360,000,000">' +
        '<span id="pi-target-out" class="pi-target-out" title="Click to copy exact %">—</span></div>' +
        '<div class="calc-hint">How much the grand total must change to reach this target.</div>';
      var host = document.getElementById('calc-target-host');
      if (host) {
        host.innerHTML = '';
        host.appendChild(tgt.firstChild);
        while (tgt.firstChild) host.appendChild(tgt.firstChild);
      } else {
        var grid = calcCard.querySelector('.calc-grid') || calcCard;
        var wrap = document.createElement('div');
        wrap.className = 'calc-section calc-section-target';
        wrap.innerHTML = tgt.innerHTML;
        grid.insertBefore(wrap, grid.firstChild);
      }
    }

    // group combo — only real groups; press Delete to clear back to "all rows".
    var groupCombo = makeCombo('Group', 'Choose a group…');
    groupCombo.setItems(function () {
      return distinctGroups().map(function (g) { return { value: g, label: g.toUpperCase() }; });
    });
    groupCombo.onPick(function (val) {
      state.group = val;
      buildFilterRow();
      applyFilter();
      // Rebuild combo value lists after rows are resolved (Filled_Features / data-vars).
      setTimeout(refreshFilterCombos, 30);
    });
    document.getElementById('pi-filter-group').appendChild(groupCombo.el);
    state._groupCombo = groupCombo;

    // Wire the "Clear all" button in the filter panel.
    var clearBtn = document.getElementById('pi-filter-clear');
    if (clearBtn) {
      clearBtn.addEventListener('click', function () {
        state.group = '';
        state.featFilters = {};
        if (state._groupCombo) { state._groupCombo.input.value = ''; }
        if (state._featCombos) {
          Object.keys(state._featCombos).forEach(function (k) {
            var c = state._featCombos[k];
            if (c && c.input) c.input.value = '';
          });
        }
        var eng = window.VirtualScrollEngine;
        if (eng && eng.removeFilter) eng.removeFilter('feat');
        buildFilterRow();
        recomputeGrand();
      });
    }

    // list combo
    var listCombo = makeCombo('Price list', 'Search a price list…');
    listCombo.setItems(function () {
      return state.lists.map(function (l) { return { value: String(l.id), label: l.name, hint: '(' + (l.currency || '').toUpperCase() + ')' }; });
    });
    listCombo.onPick(function (val) { applyList(val); });
    document.getElementById('pi-list-host').appendChild(listCombo.el);
    state._listCombo = listCombo;

    document.getElementById('pi-manual-apply').addEventListener('click', function () {
      applyManualBulk(document.getElementById('pi-manual-input').value);
    });
    document.getElementById('pi-brand-apply').addEventListener('click', function () {
      applyTextBulk('BRAND', document.getElementById('pi-brand-input').value);
    });
    document.getElementById('pi-time-apply').addEventListener('click', function () {
      applyTextBulk('TIME', document.getElementById('pi-time-input').value);
    });

    // TENDER proformas cannot flag "not suppliable", so that card is omitted;
    // a TO-only offer carries no price, so the "no unit price" card is omitted.
    var isTender = String(cfg().docKind || '').toUpperCase() === 'TENDER';
    function fcard(id, icon, label, accent) {
      return '<div class="pi-fcard pi-fcard--' + accent + '"><span class="pi-fcard-ico"><i class="fa-solid ' + icon + '"></i></span>' +
        '<span class="pi-fcard-body"><span class="pi-fcard-num" id="' + id + '-count">0</span>' +
        '<span class="pi-fcard-label">' + label + '</span></span>' +
        '<label class="ft-switch" title="Show only these rows"><input type="checkbox" id="' + id + '-chk"><span class="ft-switch-slider"></span></label></div>';
    }
    var cardsHost = document.getElementById('pi-filter-cards');
    if (cardsHost) {
      cardsHost.innerHTML =
        fcard('pi-del', 'fa-circle-minus', 'deleted', 'del') +
        fcard('pi-add', 'fa-circle-plus', 'added', 'add') +
        (pricingLocked() ? '' : fcard('pi-nounit', 'fa-money-bill-wave', 'no unit price', 'nounit')) +
        fcard('pi-nobrand', 'fa-copyright', 'no brand', 'nobrand') +
        fcard('pi-notime', 'fa-clock', 'no time', 'notime') +
        (isTender ? '' : fcard('pi-unsup', 'fa-ban', 'not suppliable', 'unsup')) +
        fcard('pi-remark', 'fa-comment-dots', 'with remark', 'remark') +
        fcard('pi-similar', 'fa-link', 'similar codes', 'similar');
    }

    function txtEmpty(tr, col) { return cellText(cellByName(tr, col)).trim() === ''; }
    // isActive is defined once at module scope (used by recomputeGrand too).
    function setCount(id, n) { var el = document.getElementById(id); if (el) el.textContent = n; }
    function countAttr(attr) { var n = 0; rows().forEach(function (tr) { if (tr.getAttribute(attr) === '1') n++; }); return n; }
    function countRemark() {
      var n = 0;
      rows().forEach(function (tr) {
        var rTd = cellByName(tr, REMARK_COL);
        if (rTd && cellText(rTd).trim() !== '') n++;
      });
      setCount('pi-remark-count', n);
    }
    function countSimilar() {
      var api = window.PISameCode;
      var n = api && api.countSimilar ? api.countSimilar() : 0;
      setCount('pi-similar-count', n);
    }
    function countUnsup() { if (!isTender) setCount('pi-unsup-count', countAttr('data-unsuppliable')); }
    function countMissingText(col, id) {
      var n = 0;
      rows().forEach(function (tr) { if (isActive(tr) && txtEmpty(tr, col)) n++; });
      setCount(id, n);
    }
    function refreshCards() {
      setCount('pi-del-count', countAttr('data-deleted'));
      setCount('pi-add-count', countAttr('data-added'));
      countMissingUnit();
      countMissingText(BRAND_COL, 'pi-nobrand-count');
      countMissingText(TIME_COL, 'pi-notime-count');
      countUnsup();
      countRemark();
      countSimilar();
      if (window.PISameCode && window.PISameCode.refreshBadges) {
        window.PISameCode.refreshBadges();
      }
      updateStatusBar();
    }
    state._countRemark = countRemark;
    state._countUnsup = countUnsup;
    state._refreshCards = refreshCards;

    function wireToggle(id, key, pred) {
      var chk = document.getElementById(id);
      if (!chk) return;
      chk.addEventListener('change', function () {
        var eng = window.VirtualScrollEngine;
        if (!eng || !eng.addFilter) return;
        if (chk.checked) eng.addFilter(key, pred); else eng.removeFilter(key);
      });
    }
    wireToggle('pi-del-chk', 'fdel', function (tr) { return tr.getAttribute('data-deleted') === '1'; });
    wireToggle('pi-add-chk', 'fadd', function (tr) { return tr.getAttribute('data-added') === '1'; });
    wireToggle('pi-nounit-chk', 'nounit', function (tr) { var v = unitValue(tr); return !isFinite(v) || v === 0; });
    wireToggle('pi-nobrand-chk', 'fnobrand', function (tr) { return isActive(tr) && txtEmpty(tr, BRAND_COL); });
    wireToggle('pi-notime-chk', 'fnotime', function (tr) { return isActive(tr) && txtEmpty(tr, TIME_COL); });
    wireToggle('pi-unsup-chk', 'unsup', function (tr) { return tr.getAttribute('data-unsuppliable') === '1'; });
    wireToggle('pi-remark-chk', 'remark', function (tr) {
      var rTd = cellByName(tr, REMARK_COL); return rTd && cellText(rTd).trim() !== '';
    });

    // Similar FTCO codes: filter + per-group highlight colors (toggle off clears both).
    (function wireSimilarToggle() {
      var chk = document.getElementById('pi-similar-chk');
      if (!chk || chk.dataset.ready === '1') return;
      chk.dataset.ready = '1';
      chk.addEventListener('change', function () {
        var eng = window.VirtualScrollEngine;
        var api = window.PISameCode;
        if (!eng || !eng.addFilter) return;
        if (chk.checked) {
          eng.addFilter('fsimilar', function (tr) {
            return !!(api && api.isSimilarRow && api.isSimilarRow(tr));
          });
          if (api && api.paintGroupColors) api.paintGroupColors();
        } else {
          eng.removeFilter('fsimilar');
          if (api && api.clearGroupColors) api.clearGroupColors();
        }
      });
    })();
    document.addEventListener('ft-rows-changed', refreshCards);
    document.addEventListener('ft-flags-changed', function () {
      applyUnsuppliableLocks();
      refreshCards();
      // NOT SUPPLIABLE toggle must immediately drop/restore row prices in totals.
      recomputeGrand();
    });
    document.addEventListener('input', function (e) {
      var td = e.target.closest ? e.target.closest('td') : null;
      if (!td) return;
      var cn = td.getAttribute('data-col-name');
      if (cn === REMARK_COL || cn === BRAND_COL || cn === TIME_COL) refreshCards();
    });
    setTimeout(refreshCards, 120);

    function format3 (inp) {
      var p = inp.value.replace(/[^\d.]/g, '').split('.');
      var intp = p[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
      inp.value = (p.length > 1) ? (intp + '.' + p.slice(1).join('')) : intp;
    }
    var manualInput = document.getElementById('pi-manual-input');
    if (manualInput) manualInput.addEventListener('input', function () { format3(manualInput); });

    // Target total -> required margin % (live, green/red, click to copy exact %)
    var targetInput = document.getElementById('pi-target-input');
    var targetOut = document.getElementById('pi-target-out');
    function copyTextToClipboard(text) {
      text = String(text == null ? '' : text);
      if (!text) return Promise.reject(new Error('empty'));
      if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
        return navigator.clipboard.writeText(text).catch(function () {
          return fallbackCopy(text);
        });
      }
      return fallbackCopy(text);
    }
    function fallbackCopy(text) {
      return new Promise(function (resolve, reject) {
        try {
          var ta = document.createElement('textarea');
          ta.value = text;
          ta.setAttribute('readonly', '');
          ta.style.cssText = 'position:fixed;left:-9999px;top:0;opacity:0;';
          document.body.appendChild(ta);
          ta.focus();
          ta.select();
          ta.setSelectionRange(0, ta.value.length);
          var ok = document.execCommand('copy');
          document.body.removeChild(ta);
          if (ok) resolve();
          else reject(new Error('copy failed'));
        } catch (err) {
          reject(err);
        }
      });
    }
    function recomputeTarget() {
      if (!targetInput || !targetOut) return;
      format3(targetInput);
      var target = parseFloat(targetInput.value.replace(/,/g, ''));
      var grand = state._grand || 0;
      if (!isFinite(target) || !grand) {
        targetOut.textContent = '—'; targetOut.className = 'pi-target-out';
        targetOut.dataset.copy = ''; return;
      }
      var p = (target / grand - 1) * 100;
      var shown = (p >= 0 ? '+' : '') + p.toFixed(2).replace(/\.00$/, '') + '%';
      targetOut.textContent = shown;
      targetOut.className = 'pi-target-out ' + (p >= 0 ? 'pos' : 'neg');
      // Exact percentage for pasting into a margin % field (no trailing % sign).
      targetOut.dataset.copy = (p >= 0 ? '' : '-') + Math.abs(p).toString();
    }
    if (targetInput) targetInput.addEventListener('input', recomputeTarget);
    if (targetOut) {
      targetOut.style.cursor = 'pointer';
      targetOut.setAttribute('role', 'button');
      targetOut.setAttribute('tabindex', '0');
      targetOut.title = 'Click to copy exact %';
      function doCopyPercent() {
        var v = targetOut.dataset.copy || '';
        if (!v) return;
        copyTextToClipboard(v).then(function () {
          var old = targetOut.textContent;
          targetOut.textContent = 'copied';
          setTimeout(function () {
            targetOut.textContent = old;
            // Clear the target value so the user can enter a new one cleanly.
            if (targetInput) { targetInput.value = ''; recomputeTarget(); }
          }, 700);
        }).catch(function () { /* ignore clipboard denials */ });
      }
      targetOut.addEventListener('click', doCopyPercent);
      targetOut.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          doCopyPercent();
        }
      });
    }
    state._recomputeTarget = recomputeTarget;
  }
  function renderComparison(comp, suggestion) {
    var box = document.getElementById('pi-comparison');
    if (box) {
      box.innerHTML = comp.length
        ? '<table class="pi-cmp"><thead><tr><th>List</th><th>Priced items</th><th>Total of common items</th></tr></thead><tbody>' +
          comp.map(function (c) {
            var ct = (c.common_total != null ? c.common_total : c.total);
            return '<tr><td>' + c.name + '</td><td>' + c.covered + '</td><td>' +
              ct.toLocaleString('en-US') + ' ' + (c.currency || '').toUpperCase() + '</td></tr>';
          }).join('') + '</tbody></table>'
        : '';
    }
    var sg = document.getElementById('pi-suggestion');
    if (sg) {
      if (suggestion) {
        sg.hidden = false;
        sg.innerHTML = 'Cheapest for the <b>' + suggestion.count + '</b> items priced by every list: ' +
          '<b>' + suggestion.name + '</b> &mdash; total <b>' + suggestion.common_total.toLocaleString('en-US') + ' ' +
          (suggestion.currency || '').toUpperCase() + '</b> ' +
          '<button type="button" id="pi-apply-suggestion" data-id="' + suggestion.id + '">Use this list</button>';
        var btn = document.getElementById('pi-apply-suggestion');
        if (btn) btn.addEventListener('click', function () {
          var name = (state.lists.filter(function (l) { return String(l.id) === String(suggestion.id); })[0] || {}).name || '';
          if (state._listCombo) state._listCombo.input.value = name;
          applyList(suggestion.id);
        });
      } else { sg.hidden = true; sg.innerHTML = ''; }
    }
  }

  // ---------------- manual override + remark-clears-code ----------------
  function onEditableInput(e) {
    // Per-row margin inputs live inside the UNIT PRICE cell — never treat them
    // as price edits (that was corrupting data-calc-base).
    if (e.target && (e.target.classList.contains('row-margin-percent')
        || (e.target.closest && e.target.closest('.row-margin-panel')))) {
      return;
    }
    var td = e.target.closest ? e.target.closest('td') : null;
    if (!td) return;
    var tr = td.closest('tr');
    if (td.getAttribute('data-col-name') === QTY_COL) {
      // Unit-conversion / manual qty change: keep the list unit price (the
      // calc base), just recompute the line total and grand total from it.
      refreshCalc(); recomputeGrand();
      return;
    }
    var isUnit = (td.getAttribute('data-calc-variable') === UNIT_VAR) ||
                 (td.getAttribute('data-col-name') === 'UNIT PRICE');
    if (isUnit) {
      // Only the always-on unit-price field may rewrite the base.
      if (!e.target.classList.contains('pi-unit-input') && e.target.tagName !== 'TEXTAREA') return;
      // TO-only offer: the price column is locked — never accept a value.
      if (pricingLocked()) {
        setUnitCell(tr, '', '');
        var pf = td.querySelector('input.pi-unit-input');
        if (pf) { pf.value = ''; if (pf.dataset) pf.dataset.raw = ''; }
        return;
      }
      // Row whose remark is non-empty is LOCKED: force the price back to empty
      // and ignore the input entirely (belt-and-suspenders with the disabled
      // attribute, in case a value was injected programmatically).
      if (tr.getAttribute('data-remark-cleared') === '1') {
        setUnitCell(tr, '', '');
        var lf = td.querySelector('input.pi-unit-input');
        if (lf) { lf.value = ''; if (lf.dataset) lf.dataset.raw = ''; }
        return;
      }
      var f = e.target;
      var start = (typeof f.selectionStart === 'number') ? f.selectionStart : null;
      var oldVal = String(f.value || '');
      var digitsBefore = start == null ? 0 : oldVal.slice(0, start).replace(/[^\d]/g, '').length;
      var raw = oldVal.replace(/[^0-9.\-]/g, '');
      // Live thousand-separators while typing (groups of 3 from the right).
      var parts = raw.split('.');
      var intPart = (parts[0] || '').replace(/-/g, '');
      var sign = raw.indexOf('-') === 0 ? '-' : '';
      var intFmt = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
      var formatted = sign + ((parts.length > 1)
        ? (intFmt + '.' + parts.slice(1).join('').replace(/[^\d]/g, ''))
        : intFmt);
      if (f.value !== formatted) {
        f.value = formatted;
        if (start != null) {
          var pos = 0, seen = 0;
          for (; pos < formatted.length && seen < digitsBefore; pos++) {
            if (/\d/.test(formatted.charAt(pos))) seen++;
          }
          try { f.setSelectionRange(pos, pos); } catch (err) {}
        }
      }
      td.setAttribute('data-calc-base', raw);
      td.setAttribute('data-calc-raw', raw);
      if (f && f.dataset) f.dataset.raw = raw;
      var num = parseFloat(raw);
      if (isFinite(num) && num !== 0) {
        tr.setAttribute('data-unit-manual', '1');
        td.setAttribute('data-price-source', 'manual');
        paintSource(td, 'manual');
      } else {
        tr.removeAttribute('data-unit-manual');
        td.removeAttribute('data-price-source');
        paintSource(td, '');
      }
      if (td.dataset.editing === '1') return; // mid-typing in beginBasicCellEdit: skip refresh
      // Refresh this row synchronously so Grand Total updates in real time.
      if (window.CalculationControls && window.CalculationControls.refreshRow) {
        window.CalculationControls.refreshRow(tr);
      }
      refreshCalc();
      recomputeGrand();
    }
  }
  function onEditableKey(e) {
    if (e.key !== 'Delete' && e.key !== 'Backspace') return;
    if (e.target && (e.target.classList.contains('row-margin-percent')
        || (e.target.closest && e.target.closest('.row-margin-panel')))) {
      return;
    }
    var td = e.target.closest ? e.target.closest('td') : null;
    if (!td) return;
    var isUnit = (td.getAttribute('data-calc-variable') === UNIT_VAR) ||
                 (td.getAttribute('data-col-name') === 'UNIT PRICE');
    if (!isUnit) return;
    if (!e.target.classList.contains('pi-unit-input')) return;
    if (cellText(td) !== '') return;   // only revert when the field is already empty
    var tr = td.closest('tr');
    tr.removeAttribute('data-unit-manual');
    var p = state.prices[codeOf(tr)];
    var list = state.lists.filter(function (l) { return String(l.id) === String(state.activeListId); })[0];
    if (p != null) setUnitCell(tr, p, list ? list.name : '');
    else setUnitCell(tr, '', '');
    refreshCalc();
    recomputeGrand();
  }
  // Enable / disable a row's UNIT PRICE input (locked while remark has content).
  function setUnitLocked(tr, locked) {
    var td = cellByVar(tr, UNIT_VAR) || cellByName(tr, 'UNIT PRICE');
    if (!td) return;
    var f = td.querySelector('input,textarea');
    if (f) {
      f.disabled = !!locked;
      f.readOnly = !!locked;
    }
    if (locked) td.setAttribute('data-locked', '1');
    else td.removeAttribute('data-locked');
  }

  // UNIT SVC PRICE — clear + lock with remark/brand (same as unit price / time).
  function svcRawOf(tr) {
    var raw = tr.getAttribute('data-service-price-raw') || '';
    if (raw) return raw;
    var td = cellByName(tr, SVC_COL);
    if (!td) return '';
    var inp = td.querySelector('input');
    if (inp && inp.dataset && inp.dataset.raw) return String(inp.dataset.raw);
    return cellText(td);
  }

  function clearAndLockServicePrice(tr) {
    var td = cellByName(tr, SVC_COL);
    if (!td) return;
    if (!tr.hasAttribute('data-saved-svc')) {
      tr.setAttribute('data-saved-svc', svcRawOf(tr));
    }
    var inp = td.querySelector('input');
    if (inp) {
      inp.value = '';
      if (inp.dataset) inp.dataset.raw = '';
      inp.disabled = true;
      inp.readOnly = true;
    } else {
      td.textContent = '';
    }
    tr.removeAttribute('data-service-price-raw');
    td.setAttribute('data-locked', '1');
    td.classList.add('svc-locked');
  }

  function unlockServicePrice(tr) {
    var td = cellByName(tr, SVC_COL);
    if (!td) return;
    var saved = tr.getAttribute('data-saved-svc');
    tr.removeAttribute('data-saved-svc');
    var inp = td.querySelector('input');
    if (inp) {
      inp.disabled = false;
      inp.readOnly = false;
      if (saved != null && saved !== '') {
        inp.dataset.raw = saved;
        tr.setAttribute('data-service-price-raw', saved);
        inp.value = saved;
      }
    }
    td.removeAttribute('data-locked');
    td.classList.remove('svc-locked');
  }

  function onRemarkInput(e) {
    var td = e.target.closest ? e.target.closest('td') : null;
    if (!td || td.getAttribute('data-col-name') !== REMARK_COL) return;
    var tr = td.closest('tr');
    var val = cellText(td);
    var codeCell = cellByName(tr, CODE_COL);
    // Typing in New (or any remark) re-locks commercial cells after handoff /
    // after an absorbed Technical remark round.
    if (val !== '') {
      tr.setAttribute('data-remark-new-edited', '1');
    }
    if (val !== '') {
      // Remark has ANY content → this row's price + code are cleared and the
      // unit-price / TIME boxes are LOCKED until the remark is emptied again.
      // BRAND stays editable (remark must not lock brand).
      if (tr.getAttribute('data-remark-cleared') !== '1') {
        // Remember the code AND the current unit price so both can come back.
        // Only save if brand-clear hasn't already stashed them.
        if (tr.getAttribute('data-brand-cleared') !== '1') {
          tr.setAttribute('data-saved-code', cellText(codeCell));
          var u = unitValue(tr);
          tr.setAttribute('data-saved-unit', isFinite(u) ? String(u) : '');
          var unitTd = cellByVar(tr, UNIT_VAR) || cellByName(tr, 'UNIT PRICE');
          tr.setAttribute('data-saved-src', (unitTd && unitTd.getAttribute('data-price-source')) || '');
        }
        tr.setAttribute('data-remark-cleared', '1');
        if (codeCell) codeCell.textContent = '';
        tr.removeAttribute('data-unit-manual');
        setUnitCell(tr, '', '');
        clearAndLockServicePrice(tr);
        refreshCalc(); recomputeGrand();
      }
      setUnitLocked(tr, true);   // keep it locked on every keystroke
      setCellDisabled(tr, TIME_COL, true);
      clearAndLockServicePrice(tr);
      // Do NOT lock BRAND when remark is filled.
    } else if (tr.getAttribute('data-remark-cleared') === '1') {
      // Remark cleared → unlock TIME; restore code + price only if brand is
      // not also holding a clear (brand change still needs lock).
      tr.removeAttribute('data-remark-cleared');
      tr.removeAttribute('data-remark-new-edited');
      if (tr.getAttribute('data-brand-cleared') !== '1'
          && tr.getAttribute('data-unsuppliable') !== '1') {
        setUnitLocked(tr, false);
        setCellDisabled(tr, TIME_COL, false);
        unlockServicePrice(tr);
        var saved = tr.getAttribute('data-saved-code') || '';
        if (codeCell) codeCell.textContent = saved;
        var savedUnit = parseFloat(tr.getAttribute('data-saved-unit') || '');
        var savedSrc = tr.getAttribute('data-saved-src') || '';
        tr.removeAttribute('data-saved-code');
        tr.removeAttribute('data-saved-unit');
        tr.removeAttribute('data-saved-src');
        if (isFinite(savedUnit) && savedUnit !== 0) {
          setUnitCell(tr, savedUnit, savedSrc);
          if (savedSrc && savedSrc.toLowerCase() === 'manual') tr.setAttribute('data-unit-manual', '1');
        } else {
          var p = state.prices[saved];
          var list = state.lists.filter(function (l) { return String(l.id) === String(state.activeListId); })[0];
          if (p != null) setUnitCell(tr, p, list ? list.name : '');
        }
        refreshCalc(); recomputeGrand();
      } else if (tr.getAttribute('data-brand-cleared') === '1') {
        // Brand still changed — keep unit/TIME/svc locked; leave saved-* for brand restore.
        setUnitLocked(tr, true);
        setCellDisabled(tr, TIME_COL, true);
        clearAndLockServicePrice(tr);
      } else {
        setUnitLocked(tr, false);
        if (tr.getAttribute('data-unsuppliable') !== '1') {
          setCellDisabled(tr, TIME_COL, false);
          unlockServicePrice(tr);
        }
      }
    }
  }

  function brandNs(s) {
    return String(s || '').replace(/\s+/g, '');
  }

  function brandBaselineOf(tr) {
    if (tr.hasAttribute('data-brand-baseline')) {
      return tr.getAttribute('data-brand-baseline') || '';
    }
    return cellText(cellByName(tr, BRAND_COL));
  }

  // PI Old/New brand round: lock commercial cells only when Supply's New
  // differs from Technical's baseline (a real Supply brand change).
  // Prev≠New alone is NOT enough — after absorbing Technical's brand,
  // New equals baseline while Prev still holds Supply's prior brand; locking
  // then would clear TIME/price on the next calm Save→Edit.
  // After a handoff (unlockCommercial), wait until the user edits New.
  function brandSplitLocks(tr) {
    if (!tr || tr.getAttribute('data-brand-split') !== '1') return false;
    if (cfg().unlockCommercial && tr.getAttribute('data-brand-new-edited') !== '1') {
      return false;
    }
    var cur = cellText(cellByName(tr, BRAND_COL));
    // Absorbed Technical brand (New == TO baseline) → keep pricing open.
    if (brandNs(cur) === brandNs(brandBaselineOf(tr))) return false;
    var prev = tr.getAttribute('data-prev-brand');
    if (prev == null) prev = '';
    return brandNs(prev) !== brandNs(cur);
  }

  function brandShouldLock(tr) {
    if (cfg().unlockCommercial && tr.getAttribute('data-brand-new-edited') !== '1') {
      return false;
    }
    if (brandSplitLocks(tr)) return true;
    var bTd = cellByName(tr, BRAND_COL);
    if (!bTd) return false;
    return brandNs(cellText(bTd)) !== brandNs(brandBaselineOf(tr));
  }

  // Remark locks TIME/price when Supply has an active proforma remark that
  // still needs Technical review. After Technical answers and PI absorbs that
  // round (data-remark-ack), empty New stays unlocked for pricing; any typed
  // New remark starts a fresh Supply round and must lock (including calm
  // Save→Edit — do not require a transient data-remark-new-edited flag).
  function remarkShouldLock(tr) {
    var rTd = cellByName(tr, REMARK_COL);
    if (!rTd) return false;
    var val = cellText(rTd).trim();
    if (cfg().unlockCommercial && tr.getAttribute('data-remark-new-edited') !== '1'
        && val === '') {
      return false;
    }
    var ack = tr.getAttribute('data-remark-ack');
    if (ack != null && String(ack).trim() !== '') {
      // Absorbed: empty New → open for price; non-empty New → lock.
      return val !== '';
    }
    if (val === '') return false;
    if (tr.getAttribute('data-remark-split') === '1') {
      var prev = tr.getAttribute('data-prev-remark');
      if (prev != null && brandNs(prev) === brandNs(val)) {
        // Prev==New with no absorb ack — not a fresh remark round.
        return false;
      }
    }
    return true;
  }

  function onBrandInput(e) {
    var td = e.target.closest ? e.target.closest('td') : null;
    if (!td || td.getAttribute('data-col-name') !== BRAND_COL) return;
    var tr = td.closest('tr');
    if (tr.getAttribute('data-unsuppliable') === '1') return;
    if (cfg().unlockCommercial) tr.setAttribute('data-brand-new-edited', '1');
    var changed = brandShouldLock(tr);
    var codeCell = cellByName(tr, CODE_COL);
    if (changed) {
      if (tr.getAttribute('data-brand-cleared') !== '1') {
        if (tr.getAttribute('data-remark-cleared') !== '1') {
          tr.setAttribute('data-saved-code', cellText(codeCell));
          var u = unitValue(tr);
          tr.setAttribute('data-saved-unit', isFinite(u) ? String(u) : '');
          var unitTd = cellByVar(tr, UNIT_VAR) || cellByName(tr, 'UNIT PRICE');
          tr.setAttribute('data-saved-src', (unitTd && unitTd.getAttribute('data-price-source')) || '');
        }
        tr.setAttribute('data-brand-cleared', '1');
        if (codeCell) codeCell.textContent = '';
        tr.removeAttribute('data-unit-manual');
        setUnitCell(tr, '', '');
        clearAndLockServicePrice(tr);
        refreshCalc(); recomputeGrand();
      }
      setUnitLocked(tr, true);
      setCellDisabled(tr, TIME_COL, true);
      clearAndLockServicePrice(tr);
      // BRAND and REMARK stay editable.
    } else if (tr.getAttribute('data-brand-cleared') === '1') {
      // Reverted to baseline — restore like remark clear, unless remark still holds lock.
      tr.removeAttribute('data-brand-cleared');
      if (tr.getAttribute('data-remark-cleared') === '1') {
        setUnitLocked(tr, true);
        setCellDisabled(tr, TIME_COL, true);
        clearAndLockServicePrice(tr);
        return;
      }
      setUnitLocked(tr, false);
      if (tr.getAttribute('data-unsuppliable') !== '1') {
        setCellDisabled(tr, TIME_COL, false);
        unlockServicePrice(tr);
      }
      var saved = tr.getAttribute('data-saved-code') || '';
      if (codeCell) codeCell.textContent = saved;
      var savedUnit = parseFloat(tr.getAttribute('data-saved-unit') || '');
      var savedSrc = tr.getAttribute('data-saved-src') || '';
      tr.removeAttribute('data-saved-code');
      tr.removeAttribute('data-saved-unit');
      tr.removeAttribute('data-saved-src');
      if (isFinite(savedUnit) && savedUnit !== 0) {
        setUnitCell(tr, savedUnit, savedSrc);
        if (savedSrc && savedSrc.toLowerCase() === 'manual') tr.setAttribute('data-unit-manual', '1');
      } else {
        var p2 = state.prices[saved];
        var list2 = state.lists.filter(function (l) { return String(l.id) === String(state.activeListId); })[0];
        if (p2 != null) setUnitCell(tr, p2, list2 ? list2.name : '');
      }
      refreshCalc(); recomputeGrand();
    }
  }

  // UNIT PRICE lock is driven by remark / brand-vs-TO / not-suppliable /
  // TO-only pricing — not by an empty FTCO code. Rows matching Technical's
  // BRAND stay editable so Supply can enter TIME and UNIT PRICE immediately.
  function lockUncodedRows() {
    rows().forEach(function (tr) {
      if (tr.getAttribute('data-nocode-locked') !== '1') return;
      tr.removeAttribute('data-nocode-locked');
      if (tr.getAttribute('data-remark-cleared') === '1') return;
      if (tr.getAttribute('data-brand-cleared') === '1') return;
      if (tr.getAttribute('data-unsuppliable') === '1') return;
      if (pricingLocked()) return;
      setUnitLocked(tr, false);
    });
  }
  window.PILockUncoded = lockUncodedRows;

  // Enable / disable ANY cell's editor (keeps the displayed value; only fixes it).
  function setCellDisabled(tr, colName, disabled) {
    var td = cellByName(tr, colName);
    if (!td) return;
    var f = td.querySelector('input,textarea,select');
    if (f) { f.disabled = !!disabled; f.readOnly = !!disabled; }
    if (disabled) td.setAttribute('data-locked', '1'); else td.removeAttribute('data-locked');
  }

  // A NOT-SUPPLIABLE row fixes its four commercial columns (code / unit price /
  // brand / time): the values stay but can no longer be edited. Clearing the
  // flag re-enables them (subject to the remark / brand / no-code price locks).
  function applyUnsuppliableLocks() {
    rows().forEach(function (tr) {
      var uns = tr.getAttribute('data-unsuppliable') === '1';
      if (uns) {
        setCellDisabled(tr, BRAND_COL, true);
        setCellDisabled(tr, TIME_COL, true);
        setCellDisabled(tr, CODE_COL, true);
        setUnitLocked(tr, true);
        tr.setAttribute('data-unsup-locked', '1');
      } else if (tr.getAttribute('data-unsup-locked') === '1') {
        tr.removeAttribute('data-unsup-locked');
        // Keep TIME/price locked when a remark or brand-clear is still active.
        // BRAND stays unlocked unless NOT SUPPLIABLE (remark must not lock brand).
        setCellDisabled(tr, BRAND_COL, false);
        if (tr.getAttribute('data-remark-cleared') !== '1'
            && tr.getAttribute('data-brand-cleared') !== '1') {
          setCellDisabled(tr, TIME_COL, false);
          if (!pricingLocked()) setUnitLocked(tr, false);
        }
        setCellDisabled(tr, CODE_COL, false);
        lockUncodedRows();
      }
    });
  }
  window.PIApplyUnsup = applyUnsuppliableLocks;

  // Live "ready to submit" status shown frozen at the top of the PI builder.
  // Summarises how many active, suppliable rows still miss a required field
  // (FTCO code, brand, time, plus unit price for TO & PI). When nothing is
  // missing it turns green with a clear "ready" message.
  function updateStatusBar() {
    var barEl = document.getElementById('pi-status-bar');
    if (!barEl) return;
    var needPrice = !pricingLocked();
    var needCode = !!(cfg().requireFtcoCode);
    var m = { code: 0, brand: 0, time: 0, price: 0 };
    rows().forEach(function (tr) {
      if (tr.getAttribute('data-deleted') === '1') return;
      if (tr.getAttribute('data-unsuppliable') === '1') return;
      if (needCode && !cellText(cellByName(tr, CODE_COL)).trim()) m.code++;
      if (!cellText(cellByName(tr, BRAND_COL)).trim()) m.brand++;
      if (!cellText(cellByName(tr, TIME_COL)).trim()) m.time++;
      if (needPrice) { var v = unitValue(tr); if (!isFinite(v) || v === 0) m.price++; }
    });
    var parts = [];
    if (m.code) parts.push({ t: 'FTCO code', n: m.code, i: 'fa-hashtag' });
    if (needPrice && m.price) parts.push({ t: 'unit price', n: m.price, i: 'fa-money-bill-wave' });
    if (m.brand) parts.push({ t: 'brand', n: m.brand, i: 'fa-copyright' });
    if (m.time) parts.push({ t: 'delivery time', n: m.time, i: 'fa-clock' });
    barEl.hidden = false;
    if (!parts.length) {
      barEl.className = 'pi-status-bar ok';
      barEl.innerHTML = '<span class="pi-status-ico"><i class="fa-solid fa-circle-check"></i></span>' +
        '<span>All items complete — ready to submit to Commercial.</span>';
      return;
    }
    barEl.className = 'pi-status-bar warn';
    barEl.innerHTML = '<span class="pi-status-ico"><i class="fa-solid fa-circle-exclamation"></i></span>' +
      '<span class="pi-status-lead">Not ready to submit —</span>' +
      parts.map(function (p) {
        return '<span class="pi-status-chip"><i class="fa-solid ' + p.i + '"></i> ' + p.t + ': <b>' + p.n + '</b></span>';
      }).join('');
  }
  window.PIUpdateStatus = updateStatusBar;

  function boot() {
    if (!document.getElementById('virtual-scroll-table')) return;
    buildBar();

    // TO-only offer: the Pricing panel stays OPEN and Brand / Delivery-time bulk
    // apply remain fully usable. Only the price-list picker and the manual unit
    // price are locked (those set the actual price, which a TO-only case can't
    // carry). A short inline note explains the lock.
    if (pricingLocked()) {
      var listHost = document.getElementById('pi-list-host');
      if (listHost) {
        listHost.querySelectorAll('input, button, select, textarea').forEach(function (el) {
          el.disabled = true;
        });
        listHost.classList.add('pi-field-locked');
      }
      var manualInput = document.getElementById('pi-manual-input');
      var manualApply = document.getElementById('pi-manual-apply');
      if (manualInput) manualInput.disabled = true;
      if (manualApply) manualApply.disabled = true;
      var manualField = manualInput ? manualInput.closest('.pi-field') : null;
      if (manualField) manualField.classList.add('pi-field-locked');
      // Brief note at the top of the pricing panel.
      var pricingPanelEl = document.getElementById('pi-panel-pricing');
      if (pricingPanelEl && !document.getElementById('pi-price-lock-note')) {
        var pnote = document.createElement('div');
        pnote.id = 'pi-price-lock-note';
        pnote.className = 'pi-inline-lock-note';
        pnote.innerHTML = '<i class="fa-solid fa-lock"></i> <b>TO only</b> — unit price is locked. ' +
          'You can still set <b>Brand</b> and <b>Delivery time</b> in bulk. ' +
          'Pricing unlocks when the offer becomes <b>TO &amp; PI</b>.';
        pricingPanelEl.insertBefore(pnote, pricingPanelEl.firstChild);
      }
    }

    fetchPrices(null).then(function (data) {
      state.lists = data.lists || [];
      renderComparison(data.comparison || [], data.suggestion || null);
    });
    var container = document.getElementById('excel-table-container');
    if (container) {
      container.addEventListener('input', function (e) { onEditableInput(e); onRemarkInput(e); onBrandInput(e); });
      container.addEventListener('keydown', onEditableKey, true);
      // When a manual unit-price edit finishes, store the committed raw value and
      // repaint the single box as "number + unit". No visible source badge.
      container.addEventListener('focusout', function (e) {
        var input = e.target && e.target.closest ? e.target.closest('input') : null;
        if (!input) return;
        var td = input.closest('td');
        if (!td) return;
        var isUnit = (td.getAttribute('data-calc-variable') === UNIT_VAR) ||
                     (td.getAttribute('data-col-name') === 'UNIT PRICE');
        if (!isUnit) return;
        setTimeout(function () {
          var raw = String(td.dataset.calcBase != null ? td.dataset.calcBase : cellText(td)).replace(/[^0-9.\-]/g, '');
          var num = parseFloat(raw);
          if (isFinite(num) && num !== 0) {
            // Keep the existing source if there is one (e.g. a list name); only
            // default to "manual" when the user typed a value with no source yet.
            var src = td.getAttribute('data-price-source') || 'manual';
            td.setAttribute('data-price-source', src);
            paintSource(td, src);
          } else {
            var tr2 = td.closest('tr');
            if (tr2) tr2.removeAttribute('data-unit-manual');
            td.removeAttribute('data-price-source');
            paintSource(td, '');
          }
          refreshCalc();
          recomputeGrand();
        }, 0);
      });
    }
    // Keep Grand Total currency in sync with the Unit-conversion field, and
    // reformat every unit-price box so its unit label updates too.
    ['calc-convert-from', 'calc-convert-to', 'calc-convert-rate'].forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.addEventListener('change', function () {
        recomputeGrand();
        if (window.PIReformatUnits) window.PIReformatUnits();
      });
    });

    document.addEventListener('ft-rows-changed', function () { refreshCalc(); recomputeGrand(); });
    document.addEventListener('ft-calc-refreshed', recomputeGrand);
    setTimeout(recomputeGrand, 60);   // initial grand total + missing-price count
    setTimeout(function () {
      // Feature values for the filter come from data-vars / Filled_Features
      // (data.json naming). Do not hydrate from the code-table CSV columns —
      // those names (schema/Browse) do not match extractor keys.
      if (state.group) buildFilterRow();
    }, 80);

    // Initial pass: a saved Proforma may load with remark text already present in
    // some rows. Lock those rows (clear price + code, disable unit/TIME) exactly
    // as if the user had just typed the remark. BRAND stays editable.
    // Brand vs Technical TO baseline (data-brand-baseline): if Supply changed
    // BRAND, lock TIME + UNIT PRICE; if still equal to Technical's brand, keep
    // TIME + UNIT PRICE open (even when FTCO code is empty).
    // After a workflow handoff (unlockCommercial): leave commercial cells open
    // until the user types in New again — do not re-apply prior-session locks.
    setTimeout(function () {
      var unlockHandoff = !!cfg().unlockCommercial;
      rows().forEach(function (tr) {
        // Ensure baseline exists for change detection (fallback = current brand).
        if (!tr.hasAttribute('data-brand-baseline')) {
          tr.setAttribute('data-brand-baseline', cellText(cellByName(tr, BRAND_COL)));
        }
        if (unlockHandoff) {
          // Keep Technical baseline from the server when present. Only seed from
          // the visible cell when missing — never replace a real TO baseline
          // with a stale PI brand (that caused Save→Edit locks).
          if (!tr.hasAttribute('data-brand-baseline')) {
            var bTd0 = cellByName(tr, BRAND_COL);
            if (bTd0) tr.setAttribute('data-brand-baseline', cellText(bTd0));
          }
          tr.removeAttribute('data-brand-new-edited');
          tr.removeAttribute('data-remark-new-edited');
          tr.removeAttribute('data-remark-cleared');
          tr.removeAttribute('data-brand-cleared');
          if (tr.getAttribute('data-unsuppliable') !== '1') {
            setCellDisabled(tr, TIME_COL, false);
            if (!pricingLocked()) setUnitLocked(tr, false);
            unlockServicePrice(tr);
          }
          return;
        }
        var rTd = cellByName(tr, REMARK_COL);
        if (remarkShouldLock(tr)) {
          var codeCell = cellByName(tr, CODE_COL);
          if (tr.getAttribute('data-remark-cleared') !== '1') {
            if (tr.getAttribute('data-brand-cleared') !== '1') {
              tr.setAttribute('data-saved-code', cellText(codeCell));
              var u = unitValue(tr);
              tr.setAttribute('data-saved-unit', isFinite(u) ? String(u) : '');
              var unitTd = cellByVar(tr, UNIT_VAR) || cellByName(tr, 'UNIT PRICE');
              tr.setAttribute('data-saved-src', (unitTd && unitTd.getAttribute('data-price-source')) || '');
            }
            tr.setAttribute('data-remark-cleared', '1');
            if (codeCell) codeCell.textContent = '';
            tr.removeAttribute('data-unit-manual');
            setUnitCell(tr, '', '');
            clearAndLockServicePrice(tr);
          }
          setUnitLocked(tr, true);
          setCellDisabled(tr, TIME_COL, true);
          clearAndLockServicePrice(tr);
          // Do NOT lock BRAND when remark is filled.
        } else if (rTd && tr.getAttribute('data-remark-cleared') === '1'
                   && !remarkShouldLock(tr)) {
          // Absorbed / calm restore — drop stale remark-lock from a prior session.
          tr.removeAttribute('data-remark-cleared');
        }
        // Brand differs from Technical baseline, OR Supply changed New away
        // from that baseline in a Prev/New round — keep locks.
        var bTd = cellByName(tr, BRAND_COL);
        var brandChanged = brandShouldLock(tr);
        if (brandChanged) {
          var codeCell2 = cellByName(tr, CODE_COL);
          if (tr.getAttribute('data-brand-cleared') !== '1') {
            if (tr.getAttribute('data-remark-cleared') !== '1') {
              tr.setAttribute('data-saved-code', cellText(codeCell2));
              var u2 = unitValue(tr);
              tr.setAttribute('data-saved-unit', isFinite(u2) ? String(u2) : '');
              var unitTd2 = cellByVar(tr, UNIT_VAR) || cellByName(tr, 'UNIT PRICE');
              tr.setAttribute('data-saved-src', (unitTd2 && unitTd2.getAttribute('data-price-source')) || '');
            }
            tr.setAttribute('data-brand-cleared', '1');
            if (codeCell2) codeCell2.textContent = '';
            tr.removeAttribute('data-unit-manual');
            setUnitCell(tr, '', '');
            clearAndLockServicePrice(tr);
          }
          setUnitLocked(tr, true);
          setCellDisabled(tr, TIME_COL, true);
          clearAndLockServicePrice(tr);
        } else {
          // Matches Technical BRAND — TIME + UNIT PRICE editable (unless remark /
          // not-suppliable / TO-only pricing).
          tr.removeAttribute('data-brand-cleared');
          if (tr.getAttribute('data-remark-cleared') !== '1'
              && tr.getAttribute('data-unsuppliable') !== '1') {
            setCellDisabled(tr, TIME_COL, false);
            if (!pricingLocked()) setUnitLocked(tr, false);
            unlockServicePrice(tr);
          }
        }
      });
      // Clear any legacy no-code price locks; brand/remark rules own the lock.
      lockUncodedRows();
      // NOT-SUPPLIABLE rows fix their four commercial columns on load, too.
      applyUnsuppliableLocks();
      refreshCalc(); recomputeGrand();
      if (state._refreshCards) state._refreshCards();
    }, 120);
  }

  document.addEventListener('DOMContentLoaded', function () { setTimeout(boot, 0); });
})(window, document);
