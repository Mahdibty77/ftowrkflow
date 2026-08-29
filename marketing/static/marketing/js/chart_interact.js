/* Marketing — on-chart interaction: every field on the project role chart is
 * its own clickable card, opening one reusable modal that runs in one of two
 * modes (plain search/select, or multi-select while an attach wizard is
 * armed), plus the attach wizard's own floating status bar and the query
 * (Inquiry) highlight overlay, plus zoom over the SVG (no drag-to-pan — see
 * the zoom section below for why).
 *
 * There is no per-field state kept here beyond what is open right now — the
 * chart itself (the count badge on each card) only ever changes on a fresh
 * page load; every OTHER thing a click can show (a field's list, who is
 * connected to a focus entity) is asked of the server fresh each time, the
 * same discipline the deleted directory.js used and for the same reason: a
 * rapid sequence of clicks must never show something a later server answer
 * has already made stale.
 */
(function () {
  'use strict';

  var CFG = window.FT_MARKETING_CHART;
  var chartRoot = document.getElementById('rcChart');
  if (!chartRoot || !CFG) { return; }

  var CAN_EDIT = !!CFG.canEdit;

  var canvas = document.getElementById('rcCanvas');
  var viewport = document.getElementById('rcViewport');
  var svg = document.getElementById('rcSvg');
  var queryLinesG = document.getElementById('rcQueryLines');

  var nodesByField = {};
  Array.prototype.forEach.call(svg.querySelectorAll('.rc-node'), function (g) {
    nodesByField[g.getAttribute('data-field')] = g;
  });

  // ---- fetch helpers -------------------------------------------------- //
  function headers() {
    return { 'Content-Type': 'application/x-www-form-urlencoded', 'X-CSRFToken': CFG.csrfToken };
  }
  function post(url, params) {
    return fetch(url, { method: 'POST', headers: headers(), body: new URLSearchParams(params).toString() })
      .then(function (r) { return r.json(); });
  }
  function get(url, params) {
    var u = new URL(url, window.location.origin);
    Object.keys(params).forEach(function (k) { u.searchParams.set(k, params[k]); });
    return fetch(u.toString()).then(function (r) { return r.json(); });
  }

  // ------------------------------------------------------------------------
  // Zoom only — no drag-to-pan. The owner does not want the chart moved
  // around by dragging: a bounded region, only zoom in/out (buttons + mouse
  // wheel), and its default state unchanged ("clear and organized... like
  // before"). Once zoomed past the canvas's own fixed-size box, that box's
  // own native scrollbars (see .rc-canvas/.rc-viewport in rolechart.css) are
  // how the rest becomes reachable — never a custom drag gesture.
  //
  // Zoom changes the SVG ELEMENT's own rendered pixel size
  // (style.width/style.height), computed from the fixed viewBox size times
  // the current scale — the viewBox attribute itself never changes. This is
  // what fixes the blur a CSS `transform:scale()` on a wrapping layer used
  // to cause: that approach rasterizes the SVG once and stretches the
  // bitmap, while resizing the element itself asks the SVG to re-render its
  // vector content crisply at every size.
  // ------------------------------------------------------------------------
  var MIN_SCALE = 0.35, MAX_SCALE = 2.5;
  var viewBoxParts = svg.getAttribute('viewBox').split(' ');
  var VIEW_W = parseFloat(viewBoxParts[2]);
  var VIEW_H = parseFloat(viewBoxParts[3]);
  var scale = 1;

  function applySize() {
    svg.style.width = (VIEW_W * scale) + 'px';
    svg.style.height = (VIEW_H * scale) + 'px';
  }

  function fitScale() {
    var w = viewport.clientWidth || 900;
    var h = viewport.clientHeight || 600;
    return Math.max(MIN_SCALE, Math.min(1, w / VIEW_W, h / VIEW_H));
  }

  function resetView() {
    scale = fitScale();
    applySize();
  }

  function zoomBy(factor) {
    scale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, scale * factor));
    applySize();
  }

  document.getElementById('rcZoomIn').addEventListener('click', function () { zoomBy(1.2); });
  document.getElementById('rcZoomOut').addEventListener('click', function () { zoomBy(1 / 1.2); });
  document.getElementById('rcZoomReset').addEventListener('click', resetView);

  // A plain scale in/out — there is no pan offset left to keep a cursor
  // point fixed under, so this does not try to preserve the old
  // cursor-relative recentring math.
  viewport.addEventListener('wheel', function (ev) {
    ev.preventDefault();
    zoomBy(ev.deltaY < 0 ? 1.1 : 1 / 1.1);
  }, { passive: false });

  resetView();
  window.addEventListener('resize', resetView);

  // ------------------------------------------------------------------------
  // The attach wizard's floating status bar — survives the modal opening and
  // closing across as many cards as the user visits; only Final Confirm or
  // Cancel ever clears it.
  // ------------------------------------------------------------------------
  var wizardBar = document.getElementById('rcWizardBar');
  var wizardSource = document.getElementById('rcWizardSource');
  var wizardCount = document.getElementById('rcWizardCount');
  var wizardConfirmBtn = document.getElementById('rcWizardConfirm');
  var wizardCancelBtn = document.getElementById('rcWizardCancel');

  var wizard = null; // {source: {id,field,name}, targets: Map(id -> entity)}

  function updateWizardBar() {
    if (!wizard) { wizardBar.hidden = true; return; }
    wizardBar.hidden = false;
    wizardSource.textContent = wizard.source.name;
    var n = wizard.targets.size;
    wizardCount.textContent = n + (n === 1 ? ' target' : ' targets');
  }

  wizardCancelBtn.addEventListener('click', function () {
    wizard = null;
    updateWizardBar();
    updateActionState();
  });

  wizardConfirmBtn.addEventListener('click', function () {
    if (!wizard) { return; }
    var source = wizard.source;
    var targets = Array.from(wizard.targets.values());
    wizard = null;
    updateWizardBar();
    updateActionState();
    // One fetch per pair — there is no bulk endpoint — run in sequence so a
    // slow request never races the next one for the same source.
    var chain = Promise.resolve();
    targets.forEach(function (t) {
      chain = chain.then(function () { return post(CFG.linkUrl, { a_id: source.id, b_id: t.id }); });
    });
    chain.then(function () { runInquiry(source); });
  });

  // ------------------------------------------------------------------------
  // The query (Inquiry) overlay.
  // ------------------------------------------------------------------------
  var queryPill = document.getElementById('rcQueryPill');
  var queryName = document.getElementById('rcQueryName');
  var queryClearBtn = document.getElementById('rcQueryClear');

  function nodeRect(field) {
    var g = nodesByField[field];
    if (!g) { return null; }
    var box = g.querySelector('.rc-box').getBBox();
    return {
      x: box.x, y: box.y, w: box.width, h: box.height,
      cx: box.x + box.width / 2, cy: box.y + box.height / 2
    };
  }

  function clearQueryMarks() {
    Object.keys(nodesByField).forEach(function (field) {
      nodesByField[field].classList.remove('is-focus', 'is-lit');
    });
    queryLinesG.innerHTML = '';
  }

  // The chart's own vertical centre lane. rolechart.py fixes this at its
  // module-level CEN = 650 and always places "project" on it; reading it
  // from that node's own on-screen centre means this file never has to
  // duplicate rolechart.py's constant or assume it stays 650.
  function centerLaneX() {
    var r = nodeRect('project');
    return r ? r.cx : VIEW_W / 2;
  }

  // The point on a node's own edge closest to the centre lane, at the
  // node's own centre-y — where a query line touches the box instead of
  // floating into its middle. A node already sitting on the lane
  // (cx === cen) has no "nearest edge toward the lane" to speak of, so this
  // returns the lane point itself; dedupePoints() below then collapses that
  // into its neighbour and the segment leading to it is simply never drawn.
  function laneEdgePoint(rect, cen) {
    if (rect.cx === cen) { return { x: cen, y: rect.cy }; }
    return { x: rect.cx < cen ? rect.x + rect.w : rect.x, y: rect.cy };
  }

  function dedupePoints(pts) {
    var out = [];
    pts.forEach(function (p) {
      var last = out[out.length - 1];
      if (!last || Math.abs(last.x - p.x) > 0.5 || Math.abs(last.y - p.y) > 0.5) {
        out.push(p);
      }
    });
    return out;
  }

  var ELBOW_R = 16; // same corner radius rolechart.py's own _route() uses

  // A path string through every point in ``pts``, straight on each segment
  // except that every interior corner is pulled back by (at most) ``r`` on
  // each side and bridged with a quadratic curve through the corner point —
  // the same "modern rounded elbow" language rolechart.py's _route() draws
  // for the chart's own edges, replicated here from plain on-screen points
  // rather than imported from it.
  function elbowPath(pts, r) {
    if (pts.length < 2) { return ''; }
    var d = 'M' + pts[0].x + ' ' + pts[0].y;
    for (var i = 1; i < pts.length; i++) {
      var prev = pts[i - 1], cur = pts[i], next = pts[i + 1];
      if (!next) { d += ' L' + cur.x + ' ' + cur.y; continue; }
      var len1 = Math.hypot(cur.x - prev.x, cur.y - prev.y) || 1;
      var len2 = Math.hypot(next.x - cur.x, next.y - cur.y) || 1;
      var rr = Math.max(0, Math.min(r, len1 / 2, len2 / 2));
      var inX = cur.x - (cur.x - prev.x) / len1 * rr;
      var inY = cur.y - (cur.y - prev.y) / len1 * rr;
      var outX = cur.x + (next.x - cur.x) / len2 * rr;
      var outY = cur.y + (next.y - cur.y) / len2 * rr;
      d += ' L' + inX + ' ' + inY + ' Q' + cur.x + ' ' + cur.y + ' ' + outX + ' ' + outY;
    }
    return d;
  }

  var QUERY_FILL_MS = 600;

  // "As if a fluid filled it": draw the full path immediately, then animate
  // its own stroke-dashoffset from full length down to zero — a single
  // one-shot fill-in, not a loop or pulse, since the owner explicitly wants
  // this light and subtle, not flashy.
  function animateFill(path) {
    var len = path.getTotalLength();
    path.style.strokeDasharray = String(len);
    path.style.strokeDashoffset = String(len);
    path.getBoundingClientRect(); // force layout before starting the transition
    path.style.transition = 'stroke-dashoffset ' + QUERY_FILL_MS + 'ms ease-out';
    window.requestAnimationFrame(function () {
      path.style.strokeDashoffset = '0';
    });
  }

  function drawQueryLines(focusField, litFields) {
    queryLinesG.innerHTML = '';
    var focusRect = nodeRect(focusField);
    if (!focusRect) { return; }
    var cen = centerLaneX();
    var ns = 'http://www.w3.org/2000/svg';
    litFields.forEach(function (field) {
      var litRect = nodeRect(field);
      if (!litRect) { return; }
      // Always the same three-segment orthogonal elbow — out to the centre
      // lane, along it, back out to the target — rather than an independent
      // diagonal that could cross over other nodes and edges.
      var pts = dedupePoints([
        laneEdgePoint(focusRect, cen),
        { x: cen, y: focusRect.cy },
        { x: cen, y: litRect.cy },
        laneEdgePoint(litRect, cen)
      ]);
      if (pts.length < 2) { return; }
      var path = document.createElementNS(ns, 'path');
      path.setAttribute('class', 'rc-query-line');
      path.setAttribute('d', elbowPath(pts, ELBOW_R));
      queryLinesG.appendChild(path);
      animateFill(path);
    });
  }

  function runInquiry(entity) {
    get(CFG.connectionsUrl, { entity_id: entity.id }).then(function (data) {
      if (!data.ok) { return; }
      clearQueryMarks();
      chartRoot.classList.add('is-query');
      var focusG = nodesByField[data.entity.field];
      if (focusG) { focusG.classList.add('is-focus'); }
      var litFields = data.connections.map(function (g) { return g.field; });
      litFields.forEach(function (field) {
        var g = nodesByField[field];
        if (g) { g.classList.add('is-lit'); }
      });
      window.requestAnimationFrame(function () { drawQueryLines(data.entity.field, litFields); });
      queryName.textContent = data.entity.name;
      queryPill.hidden = false;
    });
  }

  queryClearBtn.addEventListener('click', function () {
    chartRoot.classList.remove('is-query');
    clearQueryMarks();
    queryPill.hidden = true;
  });

  // ------------------------------------------------------------------------
  // The one reusable modal — default mode (search, single-select, Add /
  // Attach / Inquiry) or target-picking mode (search, multi-select via
  // checkboxes, Confirm this card / Close) while a wizard is armed for a
  // DIFFERENT field than the one being opened.
  // ------------------------------------------------------------------------
  var overlay = document.getElementById('rcModalOverlay');
  var modalTitle = document.getElementById('rcModalTitle');
  var addBtn = document.getElementById('rcModalAdd');
  var closeX = document.getElementById('rcModalClose');
  var searchInput = document.getElementById('rcModalSearch');
  var listEl = document.getElementById('rcModalList');
  var actionsDefault = document.getElementById('rcModalActionsDefault');
  var actionsPicking = document.getElementById('rcModalActionsPicking');
  var attachBtn = document.getElementById('rcModalAttach');
  var inquiryBtn = document.getElementById('rcModalInquiry');
  var closeDefaultBtn = document.getElementById('rcModalCloseBtn');
  var confirmCardBtn = document.getElementById('rcModalConfirmCard');
  var closeCardBtn = document.getElementById('rcModalCloseCard');

  if (!CAN_EDIT) { addBtn.hidden = true; }

  var modalField = null;
  var modalMode = 'default';       // 'default' | 'picking'
  var selectedEntity = null;       // default mode: at most one
  var checkedEntities = null;      // picking mode: {id: entity}
  var searchSeq = 0;
  var searchTimer = null;

  function closeModal() {
    overlay.hidden = true;
    modalField = null;
    selectedEntity = null;
    checkedEntities = null;
  }
  closeX.addEventListener('click', closeModal);
  closeDefaultBtn.addEventListener('click', closeModal);
  closeCardBtn.addEventListener('click', closeModal);
  overlay.addEventListener('click', function (ev) {
    if (ev.target === overlay) { closeModal(); }
  });
  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape' && !overlay.hidden) { closeModal(); }
  });

  function updateActionState() {
    if (modalMode !== 'default') { return; }
    attachBtn.hidden = !CAN_EDIT;
    // Disabled whenever a wizard is already armed, even with a selection made
    // here — re-arming would silently overwrite the targets already gathered
    // from other cards (this field's own modal reopens in "default" rather
    // than "picking" mode specifically so its own value can still be Added or
    // Inquired-on, but starting a SECOND wizard from it must go through
    // Cancel or Final Confirm first, not through this button).
    attachBtn.disabled = !selectedEntity || !!wizard;
    attachBtn.title = wizard ? 'Finish or cancel the current attach wizard first' : '';
    inquiryBtn.disabled = !selectedEntity;
  }

  function openModalForField(field, roleFa, abbr) {
    modalField = field;
    // Linking within the source's OWN field was never asked for, so opening
    // it while a wizard is armed is just a normal reopen in default mode.
    modalMode = (wizard && field !== wizard.source.field) ? 'picking' : 'default';
    selectedEntity = null;
    checkedEntities = modalMode === 'picking' ? {} : null;
    modalTitle.textContent = roleFa + ' · ' + abbr;
    searchInput.value = '';
    actionsDefault.hidden = modalMode !== 'default';
    actionsPicking.hidden = modalMode !== 'picking';
    updateActionState();
    overlay.hidden = false;
    fetchList('');
    searchInput.focus();
  }

  function fetchList(query) {
    var seq = ++searchSeq;
    get(CFG.searchUrl, { field: modalField, q: query }).then(function (data) {
      if (seq !== searchSeq || !data.ok) { return; }
      renderRows(data.entities);
    });
  }

  searchInput.addEventListener('input', function () {
    var q = searchInput.value;
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(function () { fetchList(q.trim()); }, 200);
  });

  function renderRows(entities) {
    listEl.innerHTML = '';
    if (!entities.length) {
      var empty = document.createElement('div');
      empty.className = 'rc-row-empty';
      empty.textContent = 'Nothing registered yet.';
      listEl.appendChild(empty);
      return;
    }
    entities.forEach(function (entity) {
      listEl.appendChild(buildRow(entity));
    });
  }

  function buildRow(entity) {
    var row = document.createElement('div');
    row.className = 'rc-row';
    var checkbox = null;
    if (modalMode === 'picking') {
      checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.checked = !!checkedEntities[entity.id];
      row.appendChild(checkbox);
    } else if (selectedEntity && selectedEntity.id === entity.id) {
      row.classList.add('is-selected');
    }
    var name = document.createElement('span');
    name.className = 'rc-row-name';
    name.textContent = entity.name;
    name.setAttribute('dir', 'auto');
    row.appendChild(name);

    row.addEventListener('click', function (ev) {
      if (ev.target === checkbox) { return; }
      if (modalMode === 'picking') {
        checkbox.checked = !checkbox.checked;
        setChecked(entity, checkbox.checked);
        return;
      }
      selectedEntity = entity;
      Array.prototype.forEach.call(listEl.querySelectorAll('.rc-row'), function (r) {
        r.classList.remove('is-selected');
      });
      row.classList.add('is-selected');
      updateActionState();
    });
    if (checkbox) {
      checkbox.addEventListener('change', function () { setChecked(entity, checkbox.checked); });
    }
    return row;
  }

  function setChecked(entity, checked) {
    if (checked) { checkedEntities[entity.id] = entity; }
    else { delete checkedEntities[entity.id]; }
  }

  addBtn.addEventListener('click', function () {
    if (!CAN_EDIT || !modalField) { return; }
    var name = searchInput.value.trim();
    if (!name) { return; }
    post(CFG.createUrl, { field: modalField, name: name }).then(function (data) {
      if (!data.ok) { return; }
      if (modalMode === 'picking') { checkedEntities[data.entity.id] = data.entity; }
      else { selectedEntity = data.entity; }
      searchInput.value = '';
      fetchList('');
      updateActionState();
    });
  });

  attachBtn.addEventListener('click', function () {
    if (!CAN_EDIT || !selectedEntity) { return; }
    wizard = { source: selectedEntity, targets: new Map() };
    closeModal();
    updateWizardBar();
  });

  inquiryBtn.addEventListener('click', function () {
    if (!selectedEntity) { return; }
    var entity = selectedEntity;
    closeModal();
    runInquiry(entity);
  });

  confirmCardBtn.addEventListener('click', function () {
    if (wizard) {
      Object.keys(checkedEntities).forEach(function (id) {
        wizard.targets.set(Number(id), checkedEntities[id]);
      });
      updateWizardBar();
    }
    closeModal();
  });

  // ------------------------------------------------------------------------
  // Node clicks — every one of the nineteen fields opens the same modal.
  // ------------------------------------------------------------------------
  Array.prototype.forEach.call(svg.querySelectorAll('.rc-node'), function (g) {
    function open() {
      openModalForField(g.getAttribute('data-field'), g.getAttribute('data-role'), g.getAttribute('data-abbr'));
    }
    g.addEventListener('click', open);
    g.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); }
    });
  });
})();
