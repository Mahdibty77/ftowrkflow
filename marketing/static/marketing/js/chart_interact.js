/* Marketing — on-chart interaction: every field on the project role chart is
 * its own clickable card, opening one reusable modal whose behaviour is
 * gated by the card's own GROUP ("kind", read off its data-kind attribute —
 * see marketing/rolechart.py's ``_kind_of``):
 *
 *   "label"  the twelve MarketingLabel cards. Lists the companies currently
 *            tagged under it (manual and/or case-derived — see
 *            marketing/services.py::companies_for_label), lets a viewer
 *            select one to Attach (tag it under other label cards too, via
 *            the wizard below) or Inquiry (light up everywhere else it is
 *            tagged).
 *   "us"     the single "our own position" card. A read-only report of every
 *            client connected through a case (services.us_connections) —
 *            no Add, no Attach: case links are never editable from the
 *            chart. Its own Inquiry button just marks "us" as the query
 *            subject — see runInquiryForUs() for why it goes no further.
 *   "inert"  project / phase / laboratory / tpi / supplier / rival. No data
 *            source feeds any of these this round, so their card opens to a
 *            plain empty state and a Close button. Deliberate, not a bug.
 *
 * The chart itself is a plain responsive SVG (see rolechart.css's
 * `.rc-canvas svg{width:100%}`) — no zoom, no pan, nothing here computes or
 * changes its size at all.
 *
 * There is no per-field state kept here beyond what is open right now — the
 * chart itself (the count badge on each card) only ever changes on a fresh
 * page load; every OTHER thing a click can show (a label's company list, a
 * company's own connections) is asked of the server fresh each time, the
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
  // The attach wizard's floating status bar — survives the modal opening and
  // closing across as many cards as the user visits; only Final Confirm or
  // Cancel ever clears it.
  //
  // wizard = {
  //   source: {id, name, code},  // the COMPANY the wizard is tagging
  //   field:  <label key>,       // the label card Attach was armed from —
  //                               // reopening THIS card stays in "default"
  //                               // browsing mode, same carve-out as before
  //   staged: {labelKey: true|false}  // desired end state, only for entries
  // }                                 // that actually differ from the
  //                                   // server's current state
  // ------------------------------------------------------------------------
  var wizardBar = document.getElementById('rcWizardBar');
  var wizardSource = document.getElementById('rcWizardSource');
  var wizardCount = document.getElementById('rcWizardCount');
  var wizardConfirmBtn = document.getElementById('rcWizardConfirm');
  var wizardCancelBtn = document.getElementById('rcWizardCancel');

  var wizard = null;

  function updateWizardBar() {
    if (!wizard) { wizardBar.hidden = true; return; }
    wizardBar.hidden = false;
    wizardSource.textContent = wizard.source.name;
    var n = Object.keys(wizard.staged).length;
    wizardCount.textContent = n + (n === 1 ? ' change' : ' changes');
  }

  wizardCancelBtn.addEventListener('click', function () {
    wizard = null;
    updateWizardBar();
    updateActionState();
  });

  wizardConfirmBtn.addEventListener('click', function () {
    if (!wizard) { return; }
    var source = wizard.source;
    var staged = wizard.staged;
    wizard = null;
    updateWizardBar();
    updateActionState();
    // One fetch per staged label — there is no bulk endpoint — run in
    // sequence so a slow request never races the next one for the same
    // company.
    var chain = Promise.resolve();
    Object.keys(staged).forEach(function (labelKey) {
      var add = staged[labelKey];
      chain = chain.then(function () {
        return post(CFG.labelToggleUrl, { client_id: source.id, label: labelKey, add: add ? 1 : 0 });
      });
    });
    chain.then(function () { runInquiryForClient(source, null); });
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
    hideUsCasesPanel();
  }

  // ------------------------------------------------------------------------
  // The "us" cases panel — only while an Inquiry's connections include "us"
  // (the focused company has at least one case). Built once here and
  // reused across opens; hidden (and its own connector line cleared, since
  // that line lives in queryLinesG same as every other query line) by the
  // very same clearQueryMarks() every other query-mode visual already goes
  // through — see the call above.
  //
  // Positioned `position:absolute` inside `#rcCanvas`, the same containing
  // block `#rcQueryPill` already anchors to (see rolechart.css: `.rc-canvas`
  // is `position:relative`) — anchored to the canvas's right edge via CSS,
  // vertically aligned here to the "us" node's own centre-y, read as a
  // PERCENTAGE of the chart's own viewBox height so it stays aligned
  // whether the responsive SVG is currently rendered wide or narrow.
  // ------------------------------------------------------------------------
  var rcCanvas = document.getElementById('rcCanvas');
  var usCasesPanel = document.createElement('div');
  usCasesPanel.className = 'rc-us-cases-panel';
  usCasesPanel.id = 'rcUsCasesPanel';
  usCasesPanel.hidden = true;
  var usCasesTitle = document.createElement('div');
  usCasesTitle.className = 'rc-us-cases-title';
  usCasesTitle.textContent = 'Cases connected to Us';
  usCasesPanel.appendChild(usCasesTitle);
  // The one scrollable region — height-capped in CSS (max-height +
  // overflow-y:auto) so a client with a hundred-plus cases never grows the
  // panel past a sane size or reflows anything else on the chart. Built
  // once per open, from the array already in hand (no virtual scrolling,
  // no re-render on scroll, no re-grouping per frame — see the CSS section
  // for the height cap itself).
  var usCasesList = document.createElement('div');
  usCasesList.className = 'rc-us-cases-list';
  usCasesPanel.appendChild(usCasesList);
  if (rcCanvas) { rcCanvas.appendChild(usCasesPanel); }

  // Groups ``cases`` (the SAME array client_connections/us Inquiry already
  // fetched — nothing refetched here) by each row's own ``label`` key —
  // "خوشه‌ای" (clustered), the owner's own word — into one small
  // sub-heading per label plus its doc numbers underneath, rather than one
  // flat list. The label's own display text is read off the matching label
  // card's own `data-role` attribute (nodesByField), the same Persian name
  // that card already shows, so nothing here has to duplicate FIELD_LABELS.
  function renderUsCasesPanelContent(cases) {
    usCasesList.innerHTML = '';
    var order = [];
    var byLabel = {};
    cases.forEach(function (c) {
      if (!byLabel[c.label]) { byLabel[c.label] = []; order.push(c.label); }
      byLabel[c.label].push(c.doc_no);
    });
    order.forEach(function (label) {
      var docNos = byLabel[label];
      var group = document.createElement('div');
      group.className = 'rc-us-cases-group';
      var heading = document.createElement('div');
      heading.className = 'rc-us-cases-group-heading';
      var labelNode = nodesByField[label];
      heading.textContent = (labelNode ? labelNode.getAttribute('data-role') : label) + ' (' + docNos.length + ')';
      heading.setAttribute('dir', 'auto');
      group.appendChild(heading);
      docNos.forEach(function (docNo) {
        var row = document.createElement('div');
        row.className = 'rc-us-cases-row';
        row.textContent = docNo;
        group.appendChild(row);
      });
      usCasesList.appendChild(group);
    });
  }

  function openUsCasesPanel(cases) {
    var usRect = nodeRect('us');
    if (!usRect) { return; }
    renderUsCasesPanelContent(cases);
    var viewBoxParts = svg.getAttribute('viewBox').split(' ');
    var viewH = parseFloat(viewBoxParts[3]);
    usCasesPanel.style.top = (usRect.cy / viewH * 100) + '%';
    usCasesPanel.hidden = false;
  }

  function hideUsCasesPanel() {
    usCasesPanel.hidden = true;
    usCasesList.innerHTML = '';
  }

  // The one additional short segment: the "us" node's own right edge to the
  // panel's left edge — straight, not routed through the centre lane like
  // the node-to-node lines, so plain node geometry is enough. The panel is
  // plain HTML positioned over the responsive SVG, so its own pixel-space
  // left edge is converted back into the SVG's viewBox units (the same
  // units every other query line is already drawn in) via the ratio
  // between the viewBox width and the SVG's current on-screen width.
  // Reuses .rc-query-line and animateFill() exactly as every other query
  // line does — no second line style.
  function drawUsCasesConnector() {
    if (usCasesPanel.hidden) { return; }
    var usRect = nodeRect('us');
    var svgBox = svg.getBoundingClientRect();
    var panelBox = usCasesPanel.getBoundingClientRect();
    if (!usRect || !svgBox.width) { return; }
    var viewBoxParts = svg.getAttribute('viewBox').split(' ');
    var viewW = parseFloat(viewBoxParts[2]);
    var scale = viewW / svgBox.width;
    var panelLeftX = (panelBox.left - svgBox.left) * scale;
    var ns = 'http://www.w3.org/2000/svg';
    var path = document.createElementNS(ns, 'path');
    path.setAttribute('class', 'rc-query-line');
    path.setAttribute('d', 'M' + (usRect.x + usRect.w) + ' ' + usRect.cy + ' L' + panelLeftX + ' ' + usRect.cy);
    queryLinesG.appendChild(path);
    animateFill(path);
  }

  // The chart's own vertical centre lane. rolechart.py fixes this at its
  // module-level CEN = 650 and always places "project" on it; reading it
  // from that node's own on-screen centre means this file never has to
  // duplicate rolechart.py's constant or assume it stays 650.
  function centerLaneX() {
    var r = nodeRect('project');
    if (r) { return r.cx; }
    var viewBoxParts = svg.getAttribute('viewBox').split(' ');
    return parseFloat(viewBoxParts[2]) / 2;
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

  // ``annotations`` is an optional {field: text} map — used for the one
  // requirement that needs a line to carry more than its own shape: an
  // Inquiry on a company annotates the "us" line with its case doc_no(s), a
  // simple <title> tooltip, so there is a proper place on the chart showing
  // HOW "Us" connects to that company (the owner's own requirement). The
  // routing, the elbow shape and the fill-in animation are all unchanged.
  function drawQueryLines(focusField, litFields, annotations) {
    annotations = annotations || {};
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
      if (annotations[field]) {
        var titleEl = document.createElementNS(ns, 'title');
        titleEl.textContent = annotations[field];
        path.appendChild(titleEl);
      }
      queryLinesG.appendChild(path);
      animateFill(path);
    });
  }

  // The Inquiry for a COMPANY selected out of a "label" card — the common
  // case, and also what Final Confirm runs on the wizard's own source once
  // every staged change has landed (that call passes ``originField: null``,
  // since a wizard session can touch several label cards at once and there
  // is no longer one single card it came "from").
  function runInquiryForClient(client, originField) {
    get(CFG.clientConnectionsUrl, { client_id: client.id }).then(function (data) {
      if (!data.ok) { return; }
      clearQueryMarks();
      chartRoot.classList.add('is-query');
      var allFields = data.labels.map(function (l) { return l.label; });
      var annotations = {};
      // "us" is among the lit fields exactly when the focused company has
      // at least one case — the same condition that already earns it the
      // "via case ..." line annotation below now also opens the clustered
      // case-list panel (see openUsCasesPanel / drawUsCasesConnector).
      var showUsPanel = data.cases.length > 0;
      if (showUsPanel) {
        allFields.push('us');
        var docNos = [];
        data.cases.forEach(function (c) {
          if (docNos.indexOf(c.doc_no) === -1) { docNos.push(c.doc_no); }
        });
        annotations.us = 'via case ' + docNos.join(', ');
      }
      var focusField = (originField && allFields.indexOf(originField) !== -1) ? originField : null;
      var litFields = focusField ? allFields.filter(function (f) { return f !== focusField; }) : allFields;
      if (focusField) {
        var focusG = nodesByField[focusField];
        if (focusG) { focusG.classList.add('is-focus'); }
      }
      litFields.forEach(function (field) {
        var g = nodesByField[field];
        if (g) { g.classList.add('is-lit'); }
      });
      window.requestAnimationFrame(function () {
        // Without a focus field there is no single anchor to route the
        // centre-lane line FROM (see the wizard-confirm call above), so we
        // just leave every applicable card lit and skip the line-drawing
        // step entirely — the highlight itself is the answer here.
        if (focusField) { drawQueryLines(focusField, litFields, annotations); }
        // The panel's own connector line is drawn AFTER drawQueryLines,
        // never before — drawQueryLines() clears queryLinesG at its own
        // start, which would otherwise wipe this one right back out.
        if (showUsPanel) {
          openUsCasesPanel(data.cases);
          drawUsCasesConnector();
        }
      });
      queryName.textContent = client.name;
      queryPill.hidden = false;
    });
  }

  // The "us" card's OWN Inquiry button — there is nothing to individually
  // select (the subject IS "us"), and the parties it connects to are
  // companies, not chart cards, so there is nothing else on the chart to
  // draw a line to either way. Considered fetching connections_of_client for
  // every connected company and lighting up the union of their label cards
  // too, but that reads as scope creep for a button whose modal already
  // shows the full company/case list read-only (see renderUsRows) — this
  // just marks "us" as the query subject, matching the task's own simpler
  // fallback reading.
  function runInquiryForUs() {
    clearQueryMarks();
    chartRoot.classList.add('is-query');
    var focusG = nodesByField.us;
    if (focusG) { focusG.classList.add('is-focus'); }
    queryName.textContent = 'Us';
    queryPill.hidden = false;
  }

  queryClearBtn.addEventListener('click', function () {
    chartRoot.classList.remove('is-query');
    clearQueryMarks();
    queryPill.hidden = true;
  });

  // ------------------------------------------------------------------------
  // The one reusable modal. Three "kind"-driven content modes (label
  // default / us / inert) plus one "wizard picking" mode that can layer over
  // a label card while a different company's wizard is armed.
  // ------------------------------------------------------------------------
  var overlay = document.getElementById('rcModalOverlay');
  var modalTitle = document.getElementById('rcModalTitle');
  var closeX = document.getElementById('rcModalClose');
  var searchWrap = document.getElementById('rcModalSearchWrap');
  var searchInput = document.getElementById('rcModalSearch');
  var listEl = document.getElementById('rcModalList');
  var actionsDefault = document.getElementById('rcModalActionsDefault');
  var actionsPicking = document.getElementById('rcModalActionsPicking');
  var attachBtn = document.getElementById('rcModalAttach');
  var inquiryBtn = document.getElementById('rcModalInquiry');
  var closeDefaultBtn = document.getElementById('rcModalCloseBtn');
  var confirmCardBtn = document.getElementById('rcModalConfirmCard');
  var closeCardBtn = document.getElementById('rcModalCloseCard');

  var modalField = null;
  var modalKind = null;            // 'label' | 'us' | 'inert'
  var modalMode = 'default';       // 'default' | 'picking'
  var selectedEntity = null;       // label default mode: at most one company
  var currentLabelCompanies = [];  // label default mode: the full fetched list, for local search filtering
  // Bumped on every open (see openModalForField) — every async render below
  // captures it at request time and checks it again once the server answers,
  // so a stale response (a slow first fetch losing a race to a fast reopen
  // of the very same card) can never paint over what the viewer sees now.
  var modalSeq = 0;

  function closeModal() {
    overlay.hidden = true;
    modalField = null;
    modalKind = null;
    selectedEntity = null;
    currentLabelCompanies = [];
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

  // Which of the default-mode action buttons apply to this card's kind.
  // Inert cards get neither — just the Close button that is always there.
  function configureDefaultActions() {
    if (modalMode !== 'default') { return; }
    if (modalKind === 'label') {
      attachBtn.hidden = !CAN_EDIT;
      inquiryBtn.hidden = false;
    } else if (modalKind === 'us') {
      attachBtn.hidden = true;  // case links are never editable from the chart
      inquiryBtn.hidden = false;
    } else {
      attachBtn.hidden = true;
      inquiryBtn.hidden = true;
    }
  }

  function updateActionState() {
    if (modalMode !== 'default') { return; }
    if (modalKind === 'label') {
      // Disabled whenever a wizard is already armed, even with a selection
      // made here — re-arming would silently overwrite the staged changes
      // already gathered from other cards (this field's own modal reopens
      // in "default" rather than "picking" mode specifically so its own
      // value can still be Inquired-on, but starting a SECOND wizard from
      // it must go through Cancel or Final Confirm first, not through this
      // button).
      attachBtn.disabled = !selectedEntity || !!wizard;
      attachBtn.title = wizard ? 'Finish or cancel the current attach wizard first' : '';
      inquiryBtn.disabled = !selectedEntity;
    } else if (modalKind === 'us') {
      // Nothing to individually select here — the subject IS "us".
      inquiryBtn.disabled = false;
    }
  }

  function openModalForField(field, roleFa, abbr, kind) {
    modalSeq += 1;
    modalField = field;
    modalKind = kind;
    // Linking within the source's OWN field was never asked for, so
    // reopening it while a wizard is armed is just a normal reopen in
    // default mode. Only "label" cards ever enter picking mode at all.
    modalMode = (kind === 'label' && wizard && field !== wizard.field) ? 'picking' : 'default';
    selectedEntity = null;
    currentLabelCompanies = [];
    modalTitle.textContent = roleFa + ' · ' + abbr;
    searchInput.value = '';
    searchWrap.hidden = !(modalKind === 'label' && modalMode === 'default');
    actionsDefault.hidden = modalMode !== 'default';
    actionsPicking.hidden = modalMode !== 'picking';
    configureDefaultActions();
    updateActionState();
    overlay.hidden = false;
    listEl.innerHTML = '';

    if (modalMode === 'picking') {
      renderPickingRow();
    } else if (modalKind === 'inert') {
      renderInertMessage();
    } else if (modalKind === 'us') {
      fetchUsConnections();
    } else {
      fetchLabelCompanies();
    }
    if (!searchWrap.hidden) { searchInput.focus(); }
  }

  // ---- "inert" cards: project / phase / laboratory / tpi / supplier / rival
  // Deliberate empty state, not a bug — nothing feeds any of these six
  // fields this round, so there is nothing to list and nothing to do beyond
  // Close (already the only visible button — see configureDefaultActions).
  function renderInertMessage() {
    var empty = document.createElement('div');
    empty.className = 'rc-row-empty';
    empty.textContent = 'Not connected to anything yet.';
    listEl.appendChild(empty);
  }

  // ---- "label" cards: default browsing mode ------------------------------
  function fetchLabelCompanies() {
    var seq = modalSeq;
    get(CFG.labelCompaniesUrl, { label: modalField }).then(function (data) {
      if (seq !== modalSeq || !data.ok) { return; }
      currentLabelCompanies = data.companies;
      renderLabelRows(filterCompanies(searchInput.value));
    });
  }

  function filterCompanies(q) {
    q = (q || '').trim().toLowerCase();
    if (!q) { return currentLabelCompanies; }
    return currentLabelCompanies.filter(function (c) {
      return c.name.toLowerCase().indexOf(q) !== -1;
    });
  }

  searchInput.addEventListener('input', function () {
    if (modalKind === 'label' && modalMode === 'default') {
      renderLabelRows(filterCompanies(searchInput.value));
    }
  });

  function renderLabelRows(companies) {
    listEl.innerHTML = '';
    if (!companies.length) {
      var empty = document.createElement('div');
      empty.className = 'rc-row-empty';
      empty.textContent = 'No companies tagged yet.';
      listEl.appendChild(empty);
      return;
    }
    companies.forEach(function (company) {
      listEl.appendChild(buildLabelRow(company));
    });
  }

  function buildLabelRow(company) {
    var row = document.createElement('div');
    row.className = 'rc-row';
    if (selectedEntity && selectedEntity.id === company.id) { row.classList.add('is-selected'); }

    var name = document.createElement('span');
    name.className = 'rc-row-name';
    name.textContent = company.name;
    name.setAttribute('dir', 'auto');
    row.appendChild(name);

    // A label card's own browsing list shows the bare name only — no
    // "via case ..." badge here (that per-case detail lives on the "us"
    // card's own read-only report, see renderUsRows, and on the Companies
    // tab's separate companies.js, neither of which this touches). A
    // case-derived fact still never gets a delete control — it can NEVER be
    // edited from the chart — it just now renders identically to any other
    // row instead of growing a badge for it.

    // A manual tag gets a "×" only when THIS viewer actually owns it
    // (``removable``) — an Expert cannot remove a Supervisor's or another
    // Expert's tag; the endpoint refuses it server-side too, but there is no
    // point rendering a dead click.
    var canRemove = (company.source === 'manual' || company.also_manual) && company.removable;
    if (CAN_EDIT && canRemove) {
      var xBtn = document.createElement('button');
      xBtn.type = 'button';
      xBtn.className = 'rc-row-remove';
      xBtn.setAttribute('aria-label', 'Remove ' + company.name);
      xBtn.textContent = '×';
      xBtn.addEventListener('click', function (ev) {
        ev.stopPropagation();
        var seq = modalSeq;
        post(CFG.labelToggleUrl, { client_id: company.id, label: modalField, add: 0 }).then(function (data) {
          if (seq !== modalSeq || !data.ok) { return; }
          if (selectedEntity && selectedEntity.id === company.id) {
            selectedEntity = null;
            updateActionState();
          }
          fetchLabelCompanies();
        });
      });
      row.appendChild(xBtn);
    }

    // Selecting the row (its name, not the × control) is what enables
    // Attach / Inquiry below — same as the old default-mode selection.
    row.addEventListener('click', function (ev) {
      if (ev.target.closest && ev.target.closest('.rc-row-remove')) { return; }
      selectedEntity = company;
      Array.prototype.forEach.call(listEl.querySelectorAll('.rc-row'), function (r) {
        r.classList.remove('is-selected');
      });
      row.classList.add('is-selected');
      updateActionState();
    });
    return row;
  }

  // ---- "us" card: read-only company/case report --------------------------
  function fetchUsConnections() {
    var seq = modalSeq;
    get(CFG.usConnectionsUrl, {}).then(function (data) {
      if (seq !== modalSeq || !data.ok) { return; }
      renderUsRows(data.companies);
    });
  }

  function renderUsRows(companies) {
    listEl.innerHTML = '';
    if (!companies.length) {
      var empty = document.createElement('div');
      empty.className = 'rc-row-empty';
      empty.textContent = 'Nothing connected yet.';
      listEl.appendChild(empty);
      return;
    }
    companies.forEach(function (company) {
      var row = document.createElement('div');
      row.className = 'rc-row rc-row-static';
      var name = document.createElement('span');
      name.className = 'rc-row-name';
      name.textContent = company.name;
      name.setAttribute('dir', 'auto');
      row.appendChild(name);
      if ((company.cases || []).length) {
        var casesWrap = document.createElement('div');
        casesWrap.className = 'rc-row-cases';
        company.cases.forEach(function (c) {
          var line = document.createElement('span');
          line.className = 'rc-row-badge';
          line.textContent = 'via case ' + c.doc_no + ' (' + c.label + ')';
          casesWrap.appendChild(line);
        });
        row.appendChild(casesWrap);
      }
      listEl.appendChild(row);
    });
  }

  // ---- wizard "picking" mode: reopening ANOTHER label card while armed ---
  // One row: the wizard's own source company, with a single checkbox for
  // THIS card's label — fetched fresh from client_connections rather than
  // the heavier label_companies list, since all that is needed here is a
  // yes/no answer about one client.
  function renderPickingRow() {
    var seq = modalSeq;
    var field = modalField;
    var armedWizard = wizard;
    get(CFG.clientConnectionsUrl, { client_id: armedWizard.source.id }).then(function (data) {
      if (seq !== modalSeq || !data.ok || wizard !== armedWizard) { return; }
      var entry = null;
      data.labels.forEach(function (l) { if (l.label === field) { entry = l; } });
      var hasLabel = !!entry;
      // Disabled whenever this viewer could not remove it if it were
      // checked — a case-derived fact with no manual row of this viewer's
      // own on top of it (removable === false) is the example the task
      // spells out; the same rule also covers another user's manual tag,
      // by the identical "cannot remove what you don't own" principle.
      var lockedOn = hasLabel && !(entry && entry.removable);

      var row = document.createElement('div');
      row.className = 'rc-row rc-row-picking';
      var checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      var staged = Object.prototype.hasOwnProperty.call(armedWizard.staged, field)
        ? armedWizard.staged[field] : hasLabel;
      checkbox.checked = staged;
      checkbox.disabled = lockedOn;
      row.appendChild(checkbox);

      var name = document.createElement('span');
      name.className = 'rc-row-name';
      name.textContent = armedWizard.source.name;
      name.setAttribute('dir', 'auto');
      row.appendChild(name);

      if (lockedOn) {
        var note = document.createElement('span');
        note.className = 'rc-row-badge';
        note.textContent = 'implied by case';
        row.appendChild(note);
      }

      checkbox.addEventListener('change', function () {
        // Staging is a client-side wizard concept — nothing is sent to the
        // server until Final Confirm. An entry that lands back on the
        // server's own current state is dropped rather than kept at
        // "no-op", so the wizard bar's own count only ever reflects real
        // changes.
        if (checkbox.checked === hasLabel) { delete armedWizard.staged[field]; }
        else { armedWizard.staged[field] = checkbox.checked; }
        updateWizardBar();
      });

      listEl.appendChild(row);
    });
  }

  attachBtn.addEventListener('click', function () {
    if (!CAN_EDIT || modalKind !== 'label' || !selectedEntity || wizard) { return; }
    wizard = { source: selectedEntity, field: modalField, staged: {} };
    closeModal();
    updateWizardBar();
  });

  inquiryBtn.addEventListener('click', function () {
    if (modalKind === 'us') {
      closeModal();
      runInquiryForUs();
      return;
    }
    if (!selectedEntity) { return; }
    var client = selectedEntity;
    var originField = modalField;
    closeModal();
    runInquiryForClient(client, originField);
  });

  // Nothing left to send here — the picking row's own checkbox already
  // staged its change (or lack of one) on the wizard the moment it was
  // toggled, so "Confirm this card" is just a way back to the chart.
  confirmCardBtn.addEventListener('click', closeModal);

  // ------------------------------------------------------------------------
  // Node clicks — every one of the nineteen fields opens the same modal.
  // ------------------------------------------------------------------------
  Array.prototype.forEach.call(svg.querySelectorAll('.rc-node'), function (g) {
    var field = g.getAttribute('data-field');
    var kind = g.getAttribute('data-kind');
    function open() {
      openModalForField(field, g.getAttribute('data-role'), g.getAttribute('data-abbr'), kind);
    }
    g.addEventListener('click', open);
    g.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); open(); }
    });
  });
})();
