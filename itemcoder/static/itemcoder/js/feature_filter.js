/* feature_filter.js — Group + feature filter panel for the TECHNICAL OFFER.
 *
 * Field labels come from the feature schema (#ft-group-features: material_type,
 * production_method, schedule, …). Values are resolved from data-vars /
 * Filled_Features using schema→data.json aliases (#ft-group-feature-aliases),
 * e.g. schedule → phisic_sch, production_method → material_type.
 *
 * No-op on PI (pi_pricing.js owns the filter there).
 */
(function (window, document) {
  'use strict';

  function KIND() { return ((window.FT_TOOL_SAVE && window.FT_TOOL_SAVE.kind) || '').toUpperCase(); }

  var GROUP_FEATS = (function () {
    var el = document.getElementById('ft-group-features');
    if (!el) return {};
    try { return JSON.parse(el.textContent || '{}'); } catch (e) { return {}; }
  })();
  var GROUP_FEAT_ALIASES = (function () {
    var el = document.getElementById('ft-group-feature-aliases');
    if (!el) return {};
    try { return JSON.parse(el.textContent || '{}'); } catch (e) { return {}; }
  })();
  var GROUP_FEAT_VALUES = (function () {
    var el = document.getElementById('ft-group-feature-values');
    if (!el) return {};
    try { return JSON.parse(el.textContent || '{}'); } catch (e) { return {}; }
  })();
  var GROUP_COMPOUND_MAP = (function () {
    var el = document.getElementById('ft-group-compound-map');
    if (!el) return {};
    try { return JSON.parse(el.textContent || '{}'); } catch (e) { return {}; }
  })();

  // Members (ordered, per asign_code.json) of the compound column `feature`
  // belongs to, or null when it isn't part of one. Reused for both which
  // features appear as ONE combined filter entry and how their combined
  // display value gets built, so those two things can never disagree.
  function compoundMembersFor(g, feature) {
    var map = GROUP_COMPOUND_MAP[String(g).toLowerCase()] || GROUP_COMPOUND_MAP[g] || {};
    return map[String(feature).toLowerCase()] || map[feature] || null;
  }

  /** resolve(), but for a compound representative ("material") this joins
   * every member's own resolved value in asign_code.json's declared order
   * ("ASTM A106" + "Gr.B" -> "ASTM A106 Gr.B") instead of returning only the
   * first member's bare value. Every filter call site that reads a
   * feature's value — building the dropdown options, cross-filtering other
   * combos, and matching rows against a selection — goes through this one
   * function, so a compound feature is resolved identically everywhere it's
   * used, not just where it happens to be displayed. */
  function resolveMaybeCompound(vars, key, grp, type, known) {
    var members = compoundMembersFor(grp, key);
    if (!members || members.length < 2) return resolve(vars, key, grp, type, known);
    var parts = [];
    members.forEach(function (m) {
      var v = resolve(vars, m, grp, type, known);
      if (isUsableValue(v)) parts.push(String(v));
    });
    return parts.join(' ');
  }

  function knownFeats(g) {
    return GROUP_FEATS[String(g).toLowerCase()] || GROUP_FEATS[g] || [];
  }
  function schemaAllowed(g, key) {
    var map = GROUP_FEAT_VALUES[String(g).toLowerCase()] || GROUP_FEAT_VALUES[g] || {};
    return map[key] || map[String(key)] || null;
  }
  function normVal(s) {
    return String(s || '').replace(/\s+/g, ' ').trim().toLowerCase();
  }
  function valueAllowed(g, key, value) {
    // A compound representative's value is a joined string ("ASTM A106
    // Gr.B") — the schema's allowed-values list for that key describes only
    // the individual member's own raw values, never the combined form, so
    // checking a joined value against it would reject everything. Trust the
    // row-derived value instead, same as when no schema list exists at all.
    if (compoundMembersFor(g, key) && compoundMembersFor(g, key).length >= 2) return true;
    var allowed = schemaAllowed(g, key);
    if (!allowed || !allowed.length) return true; // no schema list → keep any table value
    var nv = normVal(value);
    if (!nv) return false;
    function compact(s) { return String(s || '').replace(/[\s.\-_/]/g, '').toLowerCase(); }
    var nCompact = compact(nv);
    for (var i = 0; i < allowed.length; i++) {
      var a = normVal(allowed[i]);
      if (!a) continue;
      if (a === nv) return true;
      // Prefix / containment (API 5L ⊂ API 5L Gr.B PSL1, ASME B36.10 ⊂ ASME B36.10M)
      if (a.indexOf(nv) === 0 || nv.indexOf(a) === 0) return true;
      if (compact(a) === nCompact) return true;
    }
    return false;
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
  function knownFeatsExpanded(g, known) {
    var names = (known && known.length) ? known.slice() : knownFeats(g).slice();
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

  var state = { group: '', feats: {}, combos: {}, groupCombo: null };

  function rows() {
    var eng = window.VirtualScrollEngine;
    return (eng && eng.getRows) ? eng.getRows() : [];
  }
  // Deleted rows must never populate the group/feature dropdowns (a group or
  // value that only exists on a soft-deleted row is not a real option) — but
  // this is deliberately separate from rows()/visibleCount(), which the actual
  // show/hide filtering and the row counter still use unchanged, so a
  // deleted row that matches a chosen filter continues to interact correctly
  // with the existing "show deleted rows" toggle rather than being hidden by
  // two independent systems at once.
  function notDeleted(tr) { return tr.getAttribute('data-deleted') !== '1'; }
  function enumRows() { return rows().filter(notDeleted); }
  function visibleCount() {
    var eng = window.VirtualScrollEngine;
    var vis = (eng && eng.getVisibleRows) ? eng.getVisibleRows() : rows();
    return vis.length;
  }

  function parseFilled(tr) {
    var cell = tr.querySelector('td[data-col-name="Filled_Features"]');
    if (!cell) return {};
    var out = {};
    var html = cell.innerHTML || '';
    var parts = html.split(/<br\s*\/?>/i);
    if (parts.length > 1) {
      parts.forEach(function (part) {
        var tmp = document.createElement('div'); tmp.innerHTML = part;
        var text = (tmp.textContent || '').trim();
        var eq = text.indexOf('=');
        if (eq > 0) {
          var k = text.slice(0, eq).trim(), v = text.slice(eq + 1).trim();
          if (k && v) out[k] = v;
        }
      });
    }
    // Glued / no-<br> cell: "pipe_type = PIPEsize_pipe_pipe = 1/2\"material_group_…"
    // Split on keys that end with _<group>_<type> or <group>_type.
    if (Object.keys(out).length <= 1) {
      var full = (cell.textContent || '').trim();
      var group = (tr.getAttribute('data-group') || '').trim().toLowerCase();
      var type = (tr.getAttribute('data-type') || '').trim().toLowerCase();
      function esc(s) { return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }
      var matches = [];
      if (group) {
        var keyRe;
        if (type) {
          // ROOT CAUSE FIX: [a-z] (not [A-Za-z]) for the leading character,
          // and no 'i' flag. Every real feature key in this codebase is
          // lowercase-initial snake_case ("material_type_pipe_pipe"); values
          // are uppercased for display (colored_display upper-cases them:
          // "C.S", "SMLS"). When a saved cell has lost its <br> separators
          // (see tool_save.js's cellValue Filled_Features fix — this exists
          // for data saved before that fix too) and two entries end up
          // glued with zero separator ("...C.Smaterial_type_pipe_pipe..."),
          // the OLD case-insensitive [A-Za-z] let the regex backtrack into
          // starting a "key" match at that trailing uppercase "S" instead of
          // the real "m" of "material" — stealing the last letter off the
          // previous value ("C." instead of "C.S") and prefixing garbage
          // onto the next key (which then matched no known feature at all).
          // Restricting the start to lowercase forces the match to begin at
          // the real key every time, independent of whatever came before it.
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
        // Generic fallback: key_key_key = value until next similar key.
        // Same fix as above, same reason: exec had already collected zero
        // group-scoped matches here (`matches.length` is still 0), which is
        // exactly the case a glued cell with no recognizable group/type
        // suffix produces — the two problems compound rather than being
        // independent, so this pattern needs the identical restriction.
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
      // Some saved rows double-encode the JSON string.
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

  function resolveOne(vars, name, group, type, known) {
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
    var feats = (known || []).map(function (n) { return String(n).toLowerCase(); });
    function ownerOf(kl) {
      var best = '', bl = -1;
      for (var j = 0; j < feats.length; j++) {
        var nm = feats[j];
        if (kl === nm || kl.indexOf(nm + '_') === 0) {
          if (nm.length > bl) { bl = nm.length; best = nm; }
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
  function resolve(vars, name, group, type, known) {
    var tries = namesToTry(name, group);
    var expanded = knownFeatsExpanded(group, known);
    for (var i = 0; i < tries.length; i++) {
      var hit = resolveOne(vars, tries[i], group, type, expanded);
      if (hit != null && String(hit).trim() !== '' && String(hit).trim().toLowerCase() !== 'null') return hit;
    }
    return '';
  }

  function distinctGroups() {
    var seen = {}, out = [];
    enumRows().forEach(function (tr) {
      var g = (tr.getAttribute('data-group') || '').trim();
      if (g && !seen[g.toLowerCase()]) { seen[g.toLowerCase()] = 1; out.push(g); }
    });
    return out.sort();
  }

  /** Collapse compound-column siblings (material/grade_material/spec, ...)
   * into ONE representative entry — the first member encountered — so the
   * filter offers "Material" once with a combined value like
   * "ASTM A106 Gr.B", instead of three separate, individually-incomplete
   * combos. Order-preserving: only ever removes later siblings, never
   * reorders or drops a feature that isn't part of any compound column. */
  function collapseCompoundFeatNames(g, names) {
    var seen = {};   // canonical group key ("material|grade_material|spec") -> representative already kept
    var out = [];
    names.forEach(function (name) {
      var members = compoundMembersFor(g, name);
      if (!members || members.length < 2) { out.push(name); return; }
      var gkey = members.slice().sort().join('|');
      if (seen[gkey]) return;  // a sibling already represents this group
      seen[gkey] = true;
      out.push(name);
    });
    return out;
  }

  /** Schema field names for the group (fallback: derive from row keys). */
  function featNames(g) {
    var fromSchema = knownFeats(g);
    if (fromSchema && fromSchema.length) return collapseCompoundFeatNames(g, fromSchema.slice());
    var gl = String(g || '').toLowerCase();
    var seen = {}, out = [];
    enumRows().forEach(function (tr) {
      if ((tr.getAttribute('data-group') || '').trim().toLowerCase() !== gl) return;
      var vars = rowVars(tr);
      Object.keys(vars).forEach(function (k) {
        if (k.indexOf('__') === 0 || k.indexOf('display_') === 0) return;
        var base = k.replace(new RegExp('_' + gl + '(?:_[a-z0-9]+)?$', 'i'), '');
        if (base === k) base = k.replace(/_[a-z0-9]+_[a-z0-9]+$/i, '');
        base = base.toLowerCase();
        if (base && !seen[base]) { seen[base] = 1; out.push(base); }
      });
    });
    return collapseCompoundFeatNames(g, out);
  }

  function isUsableValue(v) {
    if (v == null) return false;
    var s = String(v).trim();
    return s !== '' && s.toLowerCase() !== 'null';
  }

  function valuesFor(g, key) {
    var gl = String(g || '').toLowerCase();
    var known = featNames(g);
    var others = Object.keys(state.feats).filter(function (k) { return k !== key; });
    var seen = {}, out = [];
    enumRows().forEach(function (tr) {
      if ((tr.getAttribute('data-group') || '').trim().toLowerCase() !== gl) return;
      var grp = tr.getAttribute('data-group') || '';
      var type = tr.getAttribute('data-type') || '';
      var vars = rowVars(tr);
      for (var i = 0; i < others.length; i++) {
        if (String(resolveMaybeCompound(vars, others[i], grp, type, known)) !== String(state.feats[others[i]])) return;
      }
      var v = resolveMaybeCompound(vars, key, grp, type, known);
      if (isUsableValue(v) && valueAllowed(g, key, v) && !seen[v]) {
        seen[v] = 1; out.push(String(v));
      }
    });
    return out.sort();
  }

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

  // Raw feature keys ("material_type") -> human labels ("Material Type").
  // Deliberately separate from any VALUE formatting (values keep their own
  // meaning-preserving rules elsewhere) — this only ever touches how a
  // feature's own name is displayed above its combo box.
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

  function makeCombo(label, placeholder, icon) {
    var wrap = document.createElement('div');
    wrap.className = 'ff-combo';
    var ic = icon ? ('<i class="fa-solid ' + icon + '"></i> ') : '';
    wrap.innerHTML = '<label>' + ic + label + '</label>' +
      '<div class="ff-combo-box"><input type="text" placeholder="' + (placeholder || '') + '" autocomplete="off">' +
      '<div class="ff-menu" hidden></div></div>';
    var input = wrap.querySelector('input'), menu = wrap.querySelector('.ff-menu');
    var getItems = function () { return []; }, onPickCb = function () {};
    function render(q) {
      q = (q || '').toLowerCase();
      var items = getItems().filter(function (it) { return it.label.toLowerCase().indexOf(q) >= 0; });
      menu.innerHTML = items.length
        ? items.map(function (it) { return '<div data-val="' + encodeURIComponent(it.value) + '">' + it.label + '</div>'; }).join('')
        : '<div class="ff-empty">No matches</div>';
    }
    input.addEventListener('focus', function () { render(input.value); menu.hidden = false; });
    input.addEventListener('input', function () { render(input.value); menu.hidden = false; });
    input.addEventListener('blur', function () { setTimeout(function () { menu.hidden = true; }, 150); });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Delete' || e.key === 'Backspace') {
        if (e.key === 'Delete' || !input.value || input.dataset.committed === '1') {
          e.preventDefault(); menu.hidden = true; input.value = ''; input.dataset.committed = ''; onPickCb('');
        }
      }
    });
    menu.addEventListener('mousedown', function (e) {
      var it = e.target.closest('[data-val]'); if (!it) return;
      input.value = it.textContent.trim(); input.dataset.committed = '1'; menu.hidden = true;
      onPickCb(decodeURIComponent(it.getAttribute('data-val')));
    });
    return {
      el: wrap, input: input,
      setItems: function (fn) { getItems = fn; },
      onPick: function (fn) { onPickCb = fn; },
      refresh: function () { if (!menu.hidden) render(input.value); }
    };
  }

  function refreshAllCombos() {
    Object.keys(state.combos).forEach(function (k) { if (state.combos[k]) state.combos[k].refresh(); });
  }

  function applyFilter() {
    var eng = window.VirtualScrollEngine;
    if (!eng || !eng.addFilter) return;
    var g = state.group, feats = state.feats;
    if (!g && !Object.keys(feats).length) { eng.removeFilter('to-feat'); updateCount(); return; }
    var known = featNames(g);
    eng.addFilter('to-feat', function (tr) {
      if (g && (tr.getAttribute('data-group') || '').trim().toLowerCase() !== String(g).toLowerCase()) return false;
      var keys = Object.keys(feats);
      if (keys.length) {
        var vars = rowVars(tr), grp = tr.getAttribute('data-group') || '', type = tr.getAttribute('data-type') || '';
        for (var i = 0; i < keys.length; i++) {
          if (String(resolveMaybeCompound(vars, keys[i], grp, type, known)) !== String(feats[keys[i]])) return false;
        }
      }
      return true;
    });
    updateCount();
  }

  function buildFeatRow() {
    var box = document.getElementById('to-filter-feats');
    if (!box) return;
    box.innerHTML = '';
    state.feats = {};
    state.combos = {};
    if (!state.group) return;
    var names = featNames(state.group);
    if (!names.length) { box.innerHTML = '<div class="ff-hint">This group has no features.</div>'; return; }
    names.forEach(function (key) {
      var combo = makeCombo(prettyLabel(key), 'Type or pick…', featIcon(key));
      combo.setItems(function () {
        return valuesFor(state.group, key).map(function (v) { return { value: v, label: v }; });
      });
      combo.onPick(function (val) {
        if (val === '' || val == null) delete state.feats[key]; else state.feats[key] = val;
        applyFilter(); refreshAllCombos();
      });
      state.combos[key] = combo;
      box.appendChild(combo.el);
    });
  }

  function updateCount() {
    var el = document.getElementById('ft-rowcount-num');
    if (el) el.textContent = visibleCount();
  }

  function mount() {
    if (KIND() !== 'TO') return;
    var anchor = document.getElementById('excel-table-container');
    if (!anchor || document.getElementById('to-filter-bar')) return;

    var bar = document.createElement('section');
    bar.id = 'to-filter-bar';
    bar.innerHTML =
      '<div class="to-filter-chips">' +
        '<button type="button" class="to-chip" id="to-filter-chip"><i class="fa-solid fa-filter"></i> Filter</button>' +
      '</div>' +
      '<div id="to-filter-panel" class="to-filter-panel" hidden>' +
        '<div class="to-filter-head">' +
          '<span class="to-filter-title"><i class="fa-solid fa-sliders"></i> Group &amp; feature filter</span>' +
          '<button type="button" id="to-filter-clear" class="to-chip-clear"><i class="fa-solid fa-xmark"></i> Clear all</button>' +
        '</div>' +
        '<div class="to-filter-row"><div id="to-filter-group" class="to-filter-group-col"></div>' +
          '<div id="to-filter-feats" class="to-feats"></div></div>' +
      '</div>';
    anchor.parentNode.insertBefore(bar, anchor);

    document.getElementById('to-filter-chip').addEventListener('click', function () {
      var p = document.getElementById('to-filter-panel');
      p.hidden = !p.hidden;
      this.classList.toggle('active', !p.hidden);
    });

    var groupCombo = makeCombo('Group', 'Choose a group…');
    groupCombo.setItems(function () {
      return distinctGroups().map(function (g) { return { value: g, label: g.toUpperCase() }; });
    });
    groupCombo.onPick(function (val) { state.group = val; buildFeatRow(); applyFilter(); });
    state.groupCombo = groupCombo;
    document.getElementById('to-filter-group').appendChild(groupCombo.el);

    document.getElementById('to-filter-clear').addEventListener('click', function () {
      state.group = ''; state.feats = {};
      if (state.groupCombo) { state.groupCombo.input.value = ''; state.groupCombo.input.dataset.committed = ''; }
      var eng = window.VirtualScrollEngine;
      if (eng && eng.removeFilter) eng.removeFilter('to-feat');
      buildFeatRow(); updateCount();
    });

    if (window.VirtualScrollEngine && window.VirtualScrollEngine.onRender) {
      window.VirtualScrollEngine.onRender(function () { updateCount(); });
    }
    setTimeout(updateCount, 200);
  }

  document.addEventListener('DOMContentLoaded', function () { setTimeout(mount, 30); });
})(window, document);
