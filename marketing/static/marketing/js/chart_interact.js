/* Marketing — on-chart interaction: every field on the project role chart is
 * its own clickable card, opening one reusable modal that runs in one of two
 * modes (plain search/select, or multi-select while an attach wizard is
 * armed), plus the attach wizard's own floating status bar and the query
 * (Inquiry) highlight overlay, plus pan/zoom over the SVG.
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
  var zoomLayer = document.getElementById('rcZoomLayer');
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
  // Pan & zoom — a CSS transform on the layer wrapping the SVG, never the
  // viewBox itself, so nothing here has to redo the chart's own arithmetic.
  // ------------------------------------------------------------------------
  var MIN_SCALE = 0.35, MAX_SCALE = 2.5;
  var viewBoxParts = svg.getAttribute('viewBox').split(' ');
  var VIEW_W = parseFloat(viewBoxParts[2]);
  var VIEW_H = parseFloat(viewBoxParts[3]);
  var view = { scale: 1, x: 0, y: 0 };

  function applyTransform() {
    zoomLayer.style.transform = 'translate(' + view.x + 'px,' + view.y + 'px) scale(' + view.scale + ')';
  }

  function fitScale() {
    var w = viewport.clientWidth || 900;
    var h = viewport.clientHeight || 600;
    return Math.max(MIN_SCALE, Math.min(1, w / VIEW_W, h / VIEW_H));
  }

  function resetView() {
    view.scale = fitScale();
    view.x = 0;
    view.y = 0;
    applyTransform();
  }

  function zoomBy(factor, atX, atY) {
    var next = Math.max(MIN_SCALE, Math.min(MAX_SCALE, view.scale * factor));
    if (atX === undefined) {
      var r = viewport.getBoundingClientRect();
      atX = r.width / 2;
      atY = r.height / 2;
    }
    // Keep the point under the cursor (or the viewport centre) fixed while
    // the scale changes, otherwise every zoom click recentres the drawing.
    view.x = atX - (atX - view.x) * (next / view.scale);
    view.y = atY - (atY - view.y) * (next / view.scale);
    view.scale = next;
    applyTransform();
  }

  document.getElementById('rcZoomIn').addEventListener('click', function () { zoomBy(1.2); });
  document.getElementById('rcZoomOut').addEventListener('click', function () { zoomBy(1 / 1.2); });
  document.getElementById('rcZoomReset').addEventListener('click', resetView);

  viewport.addEventListener('wheel', function (ev) {
    ev.preventDefault();
    var r = viewport.getBoundingClientRect();
    zoomBy(ev.deltaY < 0 ? 1.1 : 1 / 1.1, ev.clientX - r.left, ev.clientY - r.top);
  }, { passive: false });

  // Plain mousedown/mousemove/mouseup with a drag threshold: below it, the
  // gesture is a click and a node's own listener handles it; at or past it,
  // this is a pan and the click that would otherwise fire on mouseup is
  // swallowed by ``justDragged`` below.
  var justDragged = false;
  var panning = false, moved = false, dragStartX = 0, dragStartY = 0, panStartX = 0, panStartY = 0;

  viewport.addEventListener('mousedown', function (ev) {
    if (ev.button !== 0) { return; }
    panning = true;
    moved = false;
    dragStartX = ev.clientX;
    dragStartY = ev.clientY;
    panStartX = view.x;
    panStartY = view.y;
  });
  window.addEventListener('mousemove', function (ev) {
    if (!panning) { return; }
    var dx = ev.clientX - dragStartX;
    var dy = ev.clientY - dragStartY;
    if (!moved && (Math.abs(dx) > 4 || Math.abs(dy) > 4)) {
      moved = true;
      viewport.classList.add('is-panning');
    }
    if (moved) {
      view.x = panStartX + dx;
      view.y = panStartY + dy;
      applyTransform();
    }
  });
  window.addEventListener('mouseup', function () {
    if (moved) {
      justDragged = true;
      window.setTimeout(function () { justDragged = false; }, 0);
    }
    panning = false;
    moved = false;
    viewport.classList.remove('is-panning');
  });

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

  function nodeCenter(field) {
    var g = nodesByField[field];
    if (!g) { return null; }
    var box = g.querySelector('.rc-box').getBBox();
    return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
  }

  function clearQueryMarks() {
    Object.keys(nodesByField).forEach(function (field) {
      nodesByField[field].classList.remove('is-focus', 'is-lit');
    });
    queryLinesG.innerHTML = '';
  }

  function drawQueryLines(focusField, litFields) {
    queryLinesG.innerHTML = '';
    var c0 = nodeCenter(focusField);
    if (!c0) { return; }
    var ns = 'http://www.w3.org/2000/svg';
    litFields.forEach(function (field) {
      var c1 = nodeCenter(field);
      if (!c1) { return; }
      var dx = c1.x - c0.x, dy = c1.y - c0.y;
      var len = Math.sqrt(dx * dx + dy * dy) || 1;
      var nx = -dy / len, ny = dx / len, bow = 26;
      var mx = (c0.x + c1.x) / 2 + nx * bow;
      var my = (c0.y + c1.y) / 2 + ny * bow;
      var path = document.createElementNS(ns, 'path');
      path.setAttribute('class', 'rc-query-line');
      path.setAttribute('d', 'M' + c0.x + ' ' + c0.y + ' Q' + mx + ' ' + my + ' ' + c1.x + ' ' + c1.y);
      queryLinesG.appendChild(path);
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
      if (justDragged) { return; }
      openModalForField(g.getAttribute('data-field'), g.getAttribute('data-role'), g.getAttribute('data-abbr'));
    }
    g.addEventListener('click', open);
    g.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); }
    });
  });
})();
