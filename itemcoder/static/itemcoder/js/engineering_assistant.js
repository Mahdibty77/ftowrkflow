/*
 * Engineering Assistant (EA) — TO-only floating helper for Remark / Revision.
 * Additive only: uses FT_TABLE_UI + process-row; never alters coding logic.
 */
(function (window, document) {
  'use strict';

  var KIND = (window.FT_TOOL_SAVE && window.FT_TOOL_SAVE.kind) || '';
  if (KIND !== 'TO') return;

  var LS_KEY = 'ft-ea-on';
  var CTX_URL = '/ajax/ea-context/';
  var COL_REV = 'اصلاحیه';
  var COL_RMK = 'ریمارک';
  var TARGET_COLS = new Set([COL_REV, COL_RMK]);
  var PANEL_MIN_H = 148;

  var enabled = false;
  try { enabled = localStorage.getItem(LS_KEY) === '1'; } catch (_e) {}

  var state = {
    row: null,
    targetCol: COL_REV,
    ctx: null,
    loading: false,
    abort: null
  };

  var fab, panel, bodyEl, targetBadge;

  var EA_MARK =
    '<span class="ea-fab-mark" aria-hidden="true">' +
      '<span class="ea-fab-ring"></span>' +
      '<span class="ea-fab-letters"><b>E</b><b>A</b></span>' +
    '</span>';

  function getCookie(name) {
    var cookieValue = null;
    if (document.cookie && document.cookie !== '') {
      var cookies = document.cookie.split(';');
      for (var i = 0; i < cookies.length; i++) {
        var cookie = cookies[i].trim();
        if (cookie.substring(0, name.length + 1) === (name + '=')) {
          cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
          break;
        }
      }
    }
    return cookieValue;
  }

  function getCell(row, col) {
    return row ? row.querySelector('td[data-col-name="' + CSS.escape(col) + '"]') : null;
  }

  function cellText(row, col) {
    var c = getCell(row, col);
    if (!c) return '';
    var ta = c.querySelector('textarea');
    return ta ? String(ta.value || '').trim() : String(c.textContent || '').trim();
  }

  function groupType(row) {
    // After Revision Confirm, the locked group wins until Revision is cleared —
    // otherwise EA keeps re-processing under the description's old group.
    var lockedG = (row && row.dataset && row.dataset.revGroupLocked) || '';
    var lockedT = (row && row.dataset && row.dataset.revTypeLocked) || '';
    var g = lockedG || (row && row.dataset && row.dataset.group) || '';
    var t = lockedT || (row && row.dataset && row.dataset.type) || '';
    if (!g) {
      var gc = getCell(row, 'Group');
      if (gc) g = String(gc.textContent || '').trim();
    }
    if (!t) {
      var tc = getCell(row, 'Type');
      if (tc) t = String(tc.textContent || '').trim();
    }
    return { group: g, type: t };
  }

  function appendRowLockParams(body, row) {
    if (!body || !row || !row.dataset) return;
    var lg = String(row.dataset.revGroupLocked || '').trim();
    var lt = String(row.dataset.revTypeLocked || '').trim();
    if (lg) body.set('locked_group', lg);
    if (lt) body.set('locked_type', lt);
  }

  function normTok(s) {
    return String(s || '').trim().toLowerCase().replace(/\s+/g, ' ');
  }

  function splitTokens(text) {
    return String(text || '')
      .split(/\s*,\s*/)
      .map(function (p) { return p.trim(); })
      .filter(Boolean);
  }

  function tokenMatchesReplace(tok, replaceList) {
    var nt = normTok(tok);
    if (!nt) return false;
    for (var i = 0; i < replaceList.length; i++) {
      var nr = normTok(replaceList[i]);
      if (!nr) continue;
      if (nt === nr) return true;
      // Compound material: dropping "ASTM A106" when replacing with full compound
      // that supersedes it, or dropping grade fragment.
      if (nt.indexOf(nr) === 0 || nr.indexOf(nt) === 0) return true;
      if (nt.indexOf(nr) !== -1 && nr.length >= 4) return true;
      if (nr.indexOf(nt) !== -1 && nt.length >= 4) return true;
    }
    return false;
  }

  function removeTokens(text, replaceList, alsoExact) {
    var parts = splitTokens(text);
    var out = [];
    var seen = {};
    for (var i = 0; i < parts.length; i++) {
      var p = parts[i];
      var n = normTok(p);
      if (alsoExact && normTok(alsoExact) === n) continue;
      if (replaceList && replaceList.length && tokenMatchesReplace(p, replaceList)) continue;
      if (seen[n]) continue;
      seen[n] = true;
      out.push(p);
    }
    return out.join(',');
  }

  function appendToken(current, token) {
    var cur = String(current || '').trim();
    var tok = String(token || '').trim();
    if (!tok) return cur;
    if (!cur) return tok;
    var parts = splitTokens(cur);
    var low = normTok(tok);
    for (var i = 0; i < parts.length; i++) {
      if (normTok(parts[i]) === low) return cur;
    }
    return cur + ',' + tok;
  }

  function normalizeApplyToken(token, meta) {
    var tok = String(token || '').trim();
    if (!tok) return tok;
    var kind = (meta && meta.apply_kind) || '';
    if (kind === 'sch' || /^sch\d/i.test(tok) || /^sch[a-z]/i.test(tok)) {
      // Keep the full schedule spelling (``sch10 × 10``), only normalize the
      // Sch prefix — never truncate at the first number.
      return tok.replace(/^sch\s*/i, 'sch');
    }
    return tok;
  }

  function applyValue(token, meta) {
    if (!state.row || !token) return;
    meta = meta || {};
    var col = state.targetCol || COL_REV;
    var cell = getCell(state.row, col);
    if (!cell) return;
    var ta = cell.querySelector('textarea');
    var prev = ta ? ta.value : String(cell.textContent || '');
    var applyTok = normalizeApplyToken(token, meta);
    var replaceList = Array.isArray(meta.replace_values) ? meta.replace_values.slice() : [];
    // Always drop exact duplicate of the new token.
    var next = removeTokens(prev, replaceList, applyTok);
    next = appendToken(next, applyTok);

    if (window.FT_TABLE_UI && window.FT_TABLE_UI.setCellValue) {
      window.FT_TABLE_UI.setCellValue(state.row, col, next);
    } else if (ta) {
      ta.value = next;
    }
    if (window.FT_TABLE_UI && window.FT_TABLE_UI.submitRow) {
      window.FT_TABLE_UI.submitRow(state.row);
    }
  }

  function mountChrome() {
    if (document.getElementById('ea-fab')) return;

    fab = document.createElement('button');
    fab.type = 'button';
    fab.id = 'ea-fab';
    fab.title = 'Engineering Assistant';
    fab.setAttribute('aria-label', 'Engineering Assistant');
    fab.innerHTML = EA_MARK;
    if (enabled) fab.classList.add('ea-on');
    document.body.appendChild(fab);

    panel = document.createElement('div');
    panel.id = 'ea-panel';
    panel.innerHTML =
      '<div class="ea-head">' +
        '<div class="ea-head-mark"><span>E</span><span>A</span></div>' +
        '<div class="ea-head-title">Engineering Assistant</div>' +
        '<span class="ea-head-target" id="ea-target-badge">REVISION</span>' +
        '<span class="ea-head-sub" id="ea-head-sub"></span>' +
        '<button type="button" class="ea-close" id="ea-close" aria-label="Close">×</button>' +
      '</div>' +
      '<div class="ea-body" id="ea-body"><div class="ea-idle">Select a Revision or Remark cell…</div></div>';
    document.body.appendChild(panel);
    bodyEl = panel.querySelector('#ea-body');
    targetBadge = panel.querySelector('#ea-target-badge');

    fab.addEventListener('click', function () {
      enabled = !enabled;
      try { localStorage.setItem(LS_KEY, enabled ? '1' : '0'); } catch (_e) {}
      fab.classList.toggle('ea-on', enabled);
      if (!enabled) {
        closePanel();
        closeAllMenus();
      }
    });
    panel.querySelector('#ea-close').addEventListener('click', function () {
      closePanel();
      closeAllMenus();
    });

    window.addEventListener('scroll', reposition, true);
    window.addEventListener('resize', reposition);
    if (window.VirtualScrollEngine && window.VirtualScrollEngine.onRender) {
      window.VirtualScrollEngine.onRender(function () {
        if (panel.classList.contains('ea-open')) {
          if (!state.row || !document.body.contains(state.row) || !state.row.offsetParent) {
            if (state.row && !isRowVisible(state.row)) {
              panel.classList.remove('ea-open');
              return;
            }
          }
          reposition();
        }
      });
    }
  }

  function isRowVisible(tr) {
    if (!tr || !tr.getBoundingClientRect) return false;
    var vp = document.getElementById('virtual-scroll-viewport');
    var r = tr.getBoundingClientRect();
    if (r.height <= 0 && r.width <= 0) return false;
    if (!vp) return r.bottom > 0 && r.top < window.innerHeight;
    var vr = vp.getBoundingClientRect();
    return r.bottom > vr.top && r.top < vr.bottom;
  }

  function closeAllMenus() {
    document.querySelectorAll('.ea-menu.ea-open').forEach(function (m) {
      m.classList.remove('ea-open');
      m.style.display = 'none';
    });
  }

  function clearEaGap() {
    if (window.VirtualScrollEngine && window.VirtualScrollEngine.setEaTopGap) {
      window.VirtualScrollEngine.setEaTopGap(0);
    }
  }

  function closePanel() {
    closeAllMenus();
    clearEaGap();
    _panelAnchorH = 0;
    if (panel) {
      panel.classList.remove('ea-open');
      panel.style.display = 'none';
      panel.style.visibility = '';
      panel.style.height = '';
      panel.style.maxHeight = '';
      panel.style.overflowY = '';
    }
    if (state.row) state.row.classList.remove('ea-active-row');
    state.row = null;
    state.ctx = null;
    if (state.abort) { try { state.abort.abort(); } catch (_e) {} state.abort = null; }
  }

  function setTargetBadge() {
    if (!targetBadge) return;
    targetBadge.textContent = state.targetCol === COL_RMK ? 'REMARK' : 'REVISION';
  }

  function headerBottomY() {
    var hdr = document.getElementById('virtual-scroll-header-row');
    if (hdr && hdr.getBoundingClientRect) {
      var hr = hdr.getBoundingClientRect();
      if (hr.height > 0) return hr.bottom + 4;
    }
    var table = document.getElementById('virtual-scroll-table');
    if (table) {
      var thead = table.querySelector('thead');
      if (thead) {
        var tr = thead.getBoundingClientRect();
        if (tr.height > 0) return tr.bottom + 4;
      }
    }
    return 8;
  }

  var _repositionScheduled = false;
  function reposition() {
    if (!panel || !panel.classList.contains('ea-open') || !state.row) return;
    if (_repositionScheduled) return;
    _repositionScheduled = true;
    window.requestAnimationFrame(function () {
      _repositionScheduled = false;
      _repositionNow();
    });
  }

  var _panelAnchorH = 0;
  var _adjustingGap = false;

  function activeRowLabel(row) {
    if (!row) return '';
    var hashTd = row.querySelector('td[data-col-name="#"]');
    var txt = '';
    if (hashTd) {
      var t = hashTd.querySelector('.client-no-text, .ic-text');
      txt = ((t ? t.textContent : hashTd.textContent) || '').replace(/[^\d]/g, '').trim();
    }
    if (!txt && row.dataset.virtualIndex != null && row.dataset.virtualIndex !== '') {
      try { txt = String(parseInt(row.dataset.virtualIndex, 10) + 1); } catch (_e) {}
    }
    if (!txt && row.sectionRowIndex >= 0) txt = String(row.sectionRowIndex + 1);
    return txt ? ('Row #' + txt) : 'Active row';
  }

  function updateHeadSub(ctx) {
    if (!panel) return;
    var sub = panel.querySelector('#ea-head-sub');
    if (!sub) return;
    var parts = [];
    var rowLab = activeRowLabel(state.row);
    if (rowLab) parts.push(rowLab);
    if (ctx) {
      var g = ctx.Group || '';
      var t = ctx.Type || '';
      if (g && t) parts.push(g + ' · ' + t);
      else if (g || t) parts.push(g || t);
    }
    var next = parts.join(' — ');
    if (sub.textContent !== next) sub.textContent = next;
  }

  function rePlaceOpenMenus() {
    document.querySelectorAll('.ea-menu.ea-open').forEach(function (menu) {
      if (menu._eaInput && document.body.contains(menu._eaInput)) {
        placeMenu(menu, menu._eaInput);
      }
    });
  }

  function _repositionNow() {
    if (!panel || !panel.classList.contains('ea-open') || !state.row) return;
    if (!document.body.contains(state.row)) {
      panel.classList.remove('ea-open');
      _panelAnchorH = 0;
      clearEaGap();
      return;
    }

    var table = document.getElementById('virtual-scroll-table');
    var tableRect = table ? table.getBoundingClientRect() : { left: 8, width: 600 };
    var left = Math.max(8, tableRect.left);
    var width = Math.max(280, tableRect.width || 600);
    var maxW = window.innerWidth - left - 8;
    if (width > maxW) width = maxW;

    var top = headerBottomY();

    // Fixed chrome geometry — never flip height/overflow model on scroll.
    panel.style.display = 'flex';
    panel.classList.add('ea-open');
    panel.style.flexDirection = 'column';
    panel.style.width = width + 'px';
    panel.style.left = left + 'px';
    panel.style.top = top + 'px';
    panel.style.minHeight = PANEL_MIN_H + 'px';
    panel.style.height = 'auto';
    panel.style.maxHeight = 'min(42vh, 420px)';
    panel.style.overflow = 'hidden';
    panel.style.visibility = '';

    var h = Math.max(PANEL_MIN_H, panel.offsetHeight || PANEL_MIN_H);
    _panelAnchorH = h;

    // Push table rows below the panel so row 1 never hides under EA.
    if (!_adjustingGap && window.VirtualScrollEngine && window.VirtualScrollEngine.setEaTopGap) {
      var need = h + 8;
      var cur = window.VirtualScrollEngine.getEaTopGap
        ? (window.VirtualScrollEngine.getEaTopGap() || 0) : 0;
      if (Math.abs(cur - need) > 1) {
        _adjustingGap = true;
        try {
          window.VirtualScrollEngine.setEaTopGap(need);
        } finally {
          _adjustingGap = false;
        }
      }
    }

    updateHeadSub(state.ctx);
    rePlaceOpenMenus();
  }

  function placeMenu(menu, input) {
    if (!menu || !input) return;
    var r = input.getBoundingClientRect();
    // Menu width must match the field exactly — long values wrap inside.
    var w = Math.max(r.width, 1);
    var left = r.left;
    if (left + w > window.innerWidth - 8) left = Math.max(8, window.innerWidth - w - 8);
    var top = r.bottom + 3;
    var spaceBelow = Math.max(60, window.innerHeight - top - 8);
    var maxH = Math.min(220, spaceBelow);
    menu.style.position = 'fixed';
    menu.style.left = left + 'px';
    menu.style.top = top + 'px';
    menu.style.width = w + 'px';
    menu.style.maxWidth = w + 'px';
    menu.style.boxSizing = 'border-box';
    menu.style.maxHeight = maxH + 'px';
    menu.style.zIndex = '6000';
    menu.style.display = 'block';
  }

  function makeCombo(feature, values, onPick, labelText) {
    var wrap = document.createElement('div');
    wrap.className = 'ea-field';
    wrap.dataset.feature = feature;
    var lab = document.createElement('label');
    lab.textContent = labelText || feature;
    wrap.appendChild(lab);

    var combo = document.createElement('div');
    combo.className = 'ea-combo';
    var input = document.createElement('input');
    input.type = 'text';
    input.autocomplete = 'off';
    input.placeholder = 'search…';
    var menu = document.createElement('div');
    menu.className = 'ea-menu';
    menu._eaInput = input;
    document.body.appendChild(menu);
    combo.appendChild(input);
    wrap.appendChild(combo);

    var list = Array.isArray(values) ? values.slice() : [];

    function hideMenu() {
      menu.classList.remove('ea-open');
      menu.style.display = 'none';
    }

    function showMenu() {
      closeAllMenus();
      render(input.value);
      menu.classList.add('ea-open');
      placeMenu(menu, input);
    }

    function render(filter) {
      var f = String(filter || '').toLowerCase();
      menu.innerHTML = '';
      var matches = list.filter(function (v) {
        return !f || String(v).toLowerCase().indexOf(f) !== -1;
      });
      if (!matches.length) {
        var e = document.createElement('div');
        e.className = 'ea-empty';
        e.textContent = 'No matches';
        menu.appendChild(e);
        return;
      }
      matches.slice(0, 200).forEach(function (v) {
        var row = document.createElement('div');
        row.textContent = v;
        row.addEventListener('mousedown', function (ev) {
          ev.preventDefault();
          hideMenu();
          input.value = v;
          if (typeof onPick === 'function') onPick(v, wrap);
        });
        menu.appendChild(row);
      });
    }

    input.addEventListener('focus', showMenu);
    input.addEventListener('input', showMenu);
    input.addEventListener('blur', function () {
      setTimeout(hideMenu, 160);
    });

    wrap._eaSetDone = function (done) {
      wrap.classList.toggle('ea-done', !!done);
      input.disabled = !!done;
      if (done) hideMenu();
    };
    wrap._eaRefresh = function (vals) {
      list = Array.isArray(vals) ? vals.slice() : [];
    };
    wrap._eaDestroyMenu = function () {
      hideMenu();
      if (menu.parentNode) menu.parentNode.removeChild(menu);
    };
    return wrap;
  }

  function alarmSetFromRow(row) {
    var set = {};
    var cell = getCell(row, 'Alarm_Features');
    if (!cell) return set;
    cell.querySelectorAll('.alarm-chip').forEach(function (chip) {
      var t = String(chip.textContent || '').trim().toLowerCase();
      if (t) set[t] = true;
    });
    return set;
  }

  function makeConflictChip(of, tone) {
    var wrap = document.createElement('div');
    wrap.className = 'ea-ochip-wrap';
    var chip = document.createElement('button');
    chip.type = 'button';
    var isRed = tone === 'red' || of.color === 'red' || of.priority === 'missing' && tone === 'red';
    chip.className = 'ea-ochip' + (isRed ? ' ea-prio-red' : (of.priority === 'missing' ? ' ea-prio-missing' : ''));
    chip.title = isRed ? 'Not in rules — fix first' : (of.priority === 'missing' ? 'Not in rules' : 'Conflict with rules');
    chip.innerHTML = '<b>' + escapeHtml(of.feature) + '</b><span>' + escapeHtml(of.value) + '</span>';
    var comboHost = document.createElement('div');
    comboHost.style.display = 'none';
    comboHost.style.marginTop = '4px';
    var meta = {
      replace_values: of.replace_values || [],
      apply_kind: of.apply_kind || 'value'
    };
    var combo = makeCombo(of.feature, of.values || [], function (val) {
      applyValue(val, meta);
      comboHost.style.display = 'none';
    });
    var lab = combo.querySelector('label');
    if (lab) lab.style.display = 'none';
    comboHost.appendChild(combo);
    chip.addEventListener('click', function () {
      var open = comboHost.style.display !== 'none';
      document.querySelectorAll('#ea-panel .ea-ochip-wrap > div').forEach(function (el) {
        el.style.display = 'none';
      });
      comboHost.style.display = open ? 'none' : 'block';
      if (!open) {
        var inp = combo.querySelector('input');
        if (inp) { inp.focus(); }
        // Do not reposition — keeps panel layout stable when opening a chip.
      }
    });
    wrap.appendChild(chip);
    wrap.appendChild(comboHost);
    return wrap;
  }

  function renderContext(ctx) {
    if (!bodyEl) return;
    _panelAnchorH = 0;
    document.querySelectorAll('.ea-menu').forEach(function (m) {
      if (m.parentNode) m.parentNode.removeChild(m);
    });
    bodyEl.innerHTML = '';
    if (!ctx) {
      bodyEl.innerHTML = '<div class="ea-idle">Loading…</div>';
      return;
    }

    var sub = panel.querySelector('#ea-head-sub');
    if (sub) {
      updateHeadSub(ctx);
    }

    var hasAny = false;
    var reds = ctx.red_fields || [];
    var oranges = ctx.orange_fields || [];
    var alarms = ctx.alarm_fields || [];
    function isSizeFeat(o) {
      return String((o && o.feature) || '').toLowerCase() === 'size';
    }
    var nonSizeOrange = oranges.filter(function (o) { return !isSizeFeat(o); });
    var nonSizeRed = reds.filter(function (o) { return !isSizeFeat(o); });
    // Size-only orange/red must not hide alarm fields or block unmatched /
    // create-size (server already isolates size from other conflicts).
    var blockAlarms = nonSizeOrange.length > 0;
    var noNonSizeConflict = nonSizeOrange.length === 0 && nonSizeRed.length === 0;

    // 1) Red (size not in rules) — highest priority
    if (reds.length) {
      hasAny = true;
      var colR = document.createElement('div');
      colR.className = 'ea-col';
      colR.innerHTML = '<div class="ea-col-label ea-lab-red"><span class="ea-dot"></span>Size · Not in rules</div>';
      var chipsR = document.createElement('div');
      chipsR.className = 'ea-orange-chips';
      reds.forEach(function (of) {
        chipsR.appendChild(makeConflictChip(of, 'red'));
      });
      colR.appendChild(chipsR);
      bodyEl.appendChild(colR);
    }

    // 2) Orange conflicts
    if (oranges.length) {
      hasAny = true;
      var colO = document.createElement('div');
      colO.className = 'ea-col';
      colO.innerHTML = '<div class="ea-col-label ea-lab-orange"><span class="ea-dot"></span>Conflict · Orange</div>';
      var chips = document.createElement('div');
      chips.className = 'ea-orange-chips';
      oranges.forEach(function (of) {
        chips.appendChild(makeConflictChip(of, 'orange'));
      });
      colO.appendChild(chips);
      bodyEl.appendChild(colO);
    }

    // 3) Alarms — after non-size oranges cleared (size-only OK)
    if (!blockAlarms && alarms.length) {
      hasAny = true;
      var colA = document.createElement('div');
      colA.className = 'ea-col';
      colA.innerHTML = '<div class="ea-col-label"><span class="ea-dot"></span>Missing · Alarm</div>';
      var fields = document.createElement('div');
      fields.className = 'ea-fields';
      alarms.forEach(function (af) {
        var feat = af.feature;
        var meta = {
          replace_values: af.replace_values || [],
          apply_kind: af.apply_kind || 'value'
        };
        var combo = makeCombo(feat, af.values || [], function (val, wrap) {
          applyValue(val, meta);
          wrap._eaSetDone(true);
        }, af.label || feat);
        fields.appendChild(combo);
      });
      colA.appendChild(fields);
      bodyEl.appendChild(colA);
    }

    // 4) Unmatched diagnostic
    var unmatched = ctx.unmatched || [];
    // Non-size conflicts only — size-only alerts still allow unmatched /
    // create-size (aligned with server conflict_open).
    var noConflict = noNonSizeConflict;
    if (noConflict && unmatched.length && ctx.code_matched === false) {
      hasAny = true;
      var colD = document.createElement('div');
      colD.className = 'ea-col';
      colD.innerHTML = '<div class="ea-col-label ea-lab-diag"><span class="ea-dot"></span>Unmatched · No FT code</div>';
      var diag = document.createElement('div');
      diag.className = 'ea-diag';
      unmatched.forEach(function (u) {
        var c = document.createElement('span');
        c.className = 'ea-diag-chip';
        c.textContent = u;
        diag.appendChild(c);
      });
      var msg = document.createElement('span');
      msg.className = 'ea-diag-msg';
      msg.textContent = 'not matched in group code table';
      diag.appendChild(msg);
      colD.appendChild(diag);
      bodyEl.appendChild(colD);

      // EXACTLY the case the spec describes: every attribute already
      // matches an existing item in the database, and Size (only) is why
      // this row has no code — the server already proved this via
      // leave-one-out (diagnose_unmatched), not a new client-side guess.
      var onlySize = unmatched.length === 1 &&
        String(unmatched[0] || '').trim().toLowerCase() === 'size';
      if (onlySize && ctx.resolved_values) {
        bodyEl.appendChild(buildCreateSizeOffer(ctx));
      }
    }

    if (ctx.ok || (noConflict && ctx.code_matched === true && !unmatched.length && !(ctx.Alarm || []).length)) {
      hasAny = true;
      var ok = document.createElement('div');
      ok.className = 'ea-ok-msg';
      ok.textContent = 'All clear — alarms empty, no conflicts, code features match.';
      bodyEl.appendChild(ok);
    }

    if (!hasAny) {
      bodyEl.innerHTML = '<div class="ea-idle">No suggestions for this row yet.</div>';
    }

    reposition();
  }

  function escapeHtml(s) {
    return String(s || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  var COL_CODE = 'کد';

  /** The confirmation card offered only when the server's own leave-one-out
   * diagnosis (diagnose_unmatched) found exactly one gap — Size — and
   * everything else already matches a real item. Building the actual item is
   * ea-create-size-item's job; this only collects one explicit confirmation
   * click and shows the result. See that endpoint's docstring for why it is
   * safe to call: it writes nothing itself, it re-validates independently,
   * and it only ever calls the same functions the admin Add Item / Feature
   * Values screens already use.
   */
  function buildCreateSizeOffer(ctx) {
    var wrap = document.createElement('div');
    wrap.className = 'ea-col ea-size-offer';
    var resolved = (ctx.code_selected && Object.keys(ctx.code_selected).length)
      ? ctx.code_selected
      : (ctx.resolved_values || {});
    var displayAttrs = Array.isArray(ctx.display_attrs) ? ctx.display_attrs : null;
    var sizeVal = resolved.Size || resolved.size || '';
    var others;
    if (displayAttrs && displayAttrs.length) {
      others = displayAttrs
        .filter(function (row) {
          return String(row.name || '').toLowerCase() !== 'size' && String(row.value || '').trim();
        })
        .map(function (row) {
          return '<span class="ea-size-kv"><b>' + escapeHtml(row.name) + '</b> ' +
            escapeHtml(row.value) + '</span>';
        })
        .join('');
    } else {
      others = Object.keys(resolved)
        .filter(function (k) { return k.toLowerCase() !== 'size' && String(resolved[k] || '').trim(); })
        .map(function (k) { return '<span class="ea-size-kv"><b>' + escapeHtml(k) + '</b> ' + escapeHtml(resolved[k]) + '</span>'; })
        .join('');
    }

    wrap.innerHTML =
      '<div class="ea-col-label ea-lab-diag"><span class="ea-dot"></span>Create this size?</div>' +
      '<div class="ea-size-offer-body">' +
        '<div class="ea-size-offer-summary">Every other attribute already matches an existing item. Only <b>Size = ' +
          escapeHtml(sizeVal) + '</b> is new. Unspecified features (NO NACE, NO COATING, …) stay absent on the new item.</div>' +
        (others ? '<div class="ea-size-offer-attrs">' + others + '</div>' : '') +
        '<div class="ea-size-offer-codes" id="ea-size-codes" hidden></div>' +
        '<div class="ea-size-offer-actions">' +
          '<button type="button" class="ea-size-btn ea-size-btn-ghost" id="ea-size-cancel" hidden>Cancel</button>' +
          '<button type="button" class="ea-size-btn ea-size-btn-primary" id="ea-size-go">Preview codes</button>' +
        '</div>' +
        '<div class="ea-size-offer-msg" hidden></div>' +
      '</div>';

    var goBtn = wrap.querySelector('#ea-size-go');
    var cancelBtn = wrap.querySelector('#ea-size-cancel');
    var msgEl = wrap.querySelector('.ea-size-offer-msg');
    var codesEl = wrap.querySelector('#ea-size-codes');
    var phase = 'preview'; // preview -> confirm -> done
    var previewTech = '';
    var previewItem = '';

    function showMsg(text, kind) {
      msgEl.hidden = false;
      msgEl.textContent = text;
      msgEl.className = 'ea-size-offer-msg ea-size-offer-' + (kind || 'info');
    }

    function postCreate(dryRun) {
      var row = state.row;
      if (!row) {
        showMsg('Row is no longer active — reopen the assistant on this row.', 'error');
        return Promise.reject(new Error('no-row'));
      }
      var gt = groupType(row);
      // Merge raw fmap (prefix / grade_material / …) under code_selected so
      // the server can re-join asign_code compounds (Concentric + Reducer).
      var payload = Object.assign({}, ctx.resolved_values || {}, resolved);
      var body = new URLSearchParams({
        group: gt.group,
        type: gt.type,
        size_feature: 'size',
        selected: JSON.stringify(payload),
        case_id: String((window.FT_TOOL_SAVE && window.FT_TOOL_SAVE.caseId) || ''),
        row_client_no: (row.dataset.clientRow || cellText(row, '#') || ''),
        dry_run: dryRun ? '1' : '0'
      });
      appendRowLockParams(body, row);
      return fetch('/ajax/ea-create-size-item/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
          'X-CSRFToken': getCookie('csrftoken') || ''
        },
        body: body.toString()
      }).then(function (r) { return r.json(); });
    }

    goBtn.addEventListener('click', function () {
      if (phase === 'preview') {
        goBtn.disabled = true;
        cancelBtn.hidden = false;
        showMsg('Building code preview…', 'info');
        postCreate(true)
          .then(function (res) {
            if (!res || res.ok !== true) {
              showMsg((res && res.error) || 'Could not preview codes.', 'error');
              goBtn.disabled = false;
              return;
            }
            previewTech = res.technical || '';
            previewItem = res.item || '';
            codesEl.hidden = false;
            codesEl.innerHTML =
              '<div class="ea-size-code-row"><span>Technical (large)</span><b>' + escapeHtml(previewTech) + '</b></div>' +
              '<div class="ea-size-code-row"><span>Item (small)</span><b>' + escapeHtml(previewItem) + '</b></div>';
            showMsg('Confirm to create this item with the codes above.', 'info');
            goBtn.textContent = 'Confirm — create it';
            goBtn.disabled = false;
            phase = 'confirm';
          })
          .catch(function () {
            showMsg('Network error — could not reach the server.', 'error');
            goBtn.disabled = false;
          });
        return;
      }

      if (phase !== 'confirm') return;

      goBtn.disabled = true;
      cancelBtn.disabled = true;
      showMsg('Creating…', 'info');

      postCreate(false)
        .then(function (res) {
          if (!res || res.ok !== true) {
            showMsg((res && res.error) || 'Could not create the item.', 'error');
            goBtn.disabled = false;
            cancelBtn.disabled = false;
            phase = 'confirm';
            return;
          }
          var row = state.row;
          var code = res.item || res.technical || '';
          if (row && code && window.FT_TABLE_UI && window.FT_TABLE_UI.setCellValue) {
            window.FT_TABLE_UI.setCellValue(row, COL_CODE, code);
            if (window.FT_TABLE_UI.submitRow) window.FT_TABLE_UI.submitRow(row);
          }
          showMsg('Created — technical ' + res.technical + ' / item ' + res.item +
            (code ? '. Assigned to this row.' : '.'), 'ok');
          goBtn.remove();
          cancelBtn.remove();
          phase = 'done';
          document.dispatchEvent(new CustomEvent('ft-rows-changed'));
        })
        .catch(function () {
          showMsg('Network error — could not reach the server.', 'error');
          goBtn.disabled = false;
          cancelBtn.disabled = false;
        });
    });

    cancelBtn.addEventListener('click', function () {
      phase = 'preview';
      previewTech = '';
      previewItem = '';
      goBtn.textContent = 'Preview codes';
      goBtn.disabled = false;
      cancelBtn.disabled = false;
      cancelBtn.hidden = true;
      codesEl.hidden = true;
      codesEl.innerHTML = '';
      msgEl.hidden = true;
    });

    return wrap;
  }

  function fetchContext(row) {
    if (!row) return;
    if (state.abort) { try { state.abort.abort(); } catch (_e) {} }
    var controller = new AbortController();
    state.abort = controller;
    state.loading = true;
    if (bodyEl) bodyEl.innerHTML = '<div class="ea-idle">Loading…</div>';
    reposition();

    var gt = groupType(row);
    var body = new URLSearchParams({
      text: cellText(row, 'description'),
      group: gt.group,
      type: gt.type,
      remark: cellText(row, COL_RMK),
      revision: cellText(row, COL_REV),
      clean_size: (row.dataset.baseSize || cellText(row, 'size') || ''),
      row_index: String(
        row.dataset.virtualIndex != null && row.dataset.virtualIndex !== ''
          ? row.dataset.virtualIndex
          : 0
      )
    });
    appendRowLockParams(body, row);

    fetch(CTX_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'X-CSRFToken': getCookie('csrftoken') || ''
      },
      body: body.toString(),
      signal: controller.signal
    })
      .then(function (r) { return r.json(); })
      .then(function (ctx) {
        if (state.abort !== controller) return;
        state.loading = false;
        state.ctx = ctx;
        renderContext(ctx);
      })
      .catch(function (err) {
        if (err && err.name === 'AbortError') return;
        state.loading = false;
        if (bodyEl) bodyEl.innerHTML = '<div class="ea-idle">Could not load assistant.</div>';
      });
  }

  function openFor(row, colName) {
    if (!enabled || !row) return;
    if (state.row && state.row !== row) state.row.classList.remove('ea-active-row');
    state.row = row;
    state.targetCol = TARGET_COLS.has(colName) ? colName : COL_REV;
    setTargetBadge();
    row.classList.add('ea-active-row');
    _panelAnchorH = 0;
    panel.classList.add('ea-open');
    // Place above the row immediately (may push list via eaTopGap for early rows).
    reposition();
    fetchContext(row);
  }

  function bindTable() {
    var table = document.getElementById('virtual-scroll-table');
    if (!table || table.dataset.eaBound === '1') return;
    table.dataset.eaBound = '1';

    table.addEventListener('focusin', function (ev) {
      if (!enabled) return;
      var ta = ev.target.closest && ev.target.closest('textarea');
      if (!ta) return;
      var td = ta.closest('td');
      var col = (td && td.dataset.colName) || '';
      if (!TARGET_COLS.has(col)) return;
      var tr = ta.closest('tr');
      if (tr) openFor(tr, col);
    });

    table.addEventListener('click', function (ev) {
      if (!enabled) return;
      var td = ev.target.closest && ev.target.closest('td');
      if (!td) return;
      var col = td.dataset.colName || '';
      if (!TARGET_COLS.has(col)) return;
      var tr = td.closest('tr');
      if (tr) openFor(tr, col);
    });
  }

  document.addEventListener('ft-rows-changed', function (ev) {
    if (!enabled || !panel || !panel.classList.contains('ea-open')) return;
    var row = ev && ev.detail && ev.detail.row;
    if (!row || row !== state.row) return;
    if (state.ctx && state.ctx.alarm_fields) {
      var live = alarmSetFromRow(row);
      panel.querySelectorAll('.ea-field[data-feature]').forEach(function (el) {
        var f = (el.dataset.feature || '').toLowerCase();
        if (el._eaSetDone) el._eaSetDone(!live[f] && !live.phisic);
      });
    }
    // Always re-fetch so a confirmed Revision group change rebuilds EA under
    // the new group / alarms (locked_group is sent from fetchContext).
    fetchContext(row);
  });

  // Explicit hook from the Revision group Confirm button (fires before the
  // process-row response) so the panel switches group immediately.
  document.addEventListener('ft-ea-group-switched', function (ev) {
    if (!enabled || !panel || !panel.classList.contains('ea-open')) return;
    var row = ev && ev.detail && ev.detail.row;
    if (!row || row !== state.row) return;
    fetchContext(row);
  });

  document.addEventListener('DOMContentLoaded', function () {
    mountChrome();
    bindTable();
    setTimeout(bindTable, 400);
  });
})(window, document);
