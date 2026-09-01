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
 *   "us"     the single "our own position" card, "Foolad Tabar" on the
 *            chart. No Add, no Attach: case links are never editable from
 *            the chart. Its own DIRECT click is no longer the same for
 *            every viewer — an ordinary viewer gets the same plain empty
 *            shell an "inert" card shows; an admin/GM viewer instead gets a
 *            flat, live-searchable list of every case in the system
 *            (services.search_all_cases via CFG.allCasesSearchUrl — see
 *            fetchAllCasesSearch/renderAllCasesRows), and picking a row
 *            there runs an Inquiry for that case's client with "us" as the
 *            origin field, exactly as if it had been picked off a label
 *            card. Its own Inquiry button is a separate thing — it just
 *            marks "us" as the query subject — see runInquiryForUs() for
 *            why it goes no further.
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
  // The one reusable "search the whole shared client directory, or create a
  // brand-new company" mechanism — every spot on this page that needs to
  // turn free text into a picked/created Client (the "+ Add company" panel
  // below, and the top-of-page Quick Inquiry card further down) builds one
  // instance of this against its own {list, input} pair rather than each
  // re-implementing its own search/create/debounce logic. Mirrors
  // CFG.clientSearchUrl's row shape and companies.js's own "+ Add ..." rule
  // exactly (an exact-name match already in the results suppresses the
  // create row) — both call sites end up with visually identical rows
  // because both go through buildRow/buildCreateRow here, never their own.
  //
  // Deliberately dumb about WHEN a search fires or WHERE the results are
  // shown — search(query) is exposed for a caller to invoke on whatever
  // trigger fits its own UI (a panel opening, an input gaining focus,
  // etc.); the debounced live-as-you-type search on the input itself is the
  // one behaviour every caller wants unconditionally, so that alone is
  // wired here.
  // ------------------------------------------------------------------------
  function createCompanyPicker(listEl, inputEl, onSelect) {
    var selected = null;
    var seq = 0;
    var debounce = null;

    function select(client) {
      selected = client;
      onSelect(client);
    }

    function search(query) {
      var mySeq = ++seq;
      get(CFG.clientSearchUrl, { q: query || '' }).then(function (data) {
        if (mySeq !== seq || !data.ok) { return; }
        renderRows(data.clients, query);
      });
    }

    function renderRows(companies, query) {
      listEl.innerHTML = '';
      companies.forEach(function (company) { listEl.appendChild(buildRow(company)); });
      var q = (query || '').trim();
      if (!companies.length) {
        var empty = document.createElement('div');
        empty.className = 'rc-row-empty';
        empty.textContent = q ? 'No companies match "' + q + '".' : 'No companies yet.';
        listEl.appendChild(empty);
      }
      // "+ Add ..." only when there is text to add, it does not already name
      // a result already in view, AND this viewer can actually create one —
      // client_create refuses a view-only GM/admin server-side regardless,
      // but a picker with no outer CAN_EDIT gate of its own (the Quick
      // Inquiry widget, unlike the "+ Add company" panel whose own trigger
      // button is already CAN_EDIT-gated) must not show a live-looking
      // affordance that can only ever silently no-op for that viewer — the
      // same "no point rendering a dead click" rule this file's row-remove
      // button already follows.
      if (q && CAN_EDIT) {
        var qLower = q.toLowerCase();
        var exact = companies.some(function (c) { return c.name.toLowerCase() === qLower; });
        if (!exact) { listEl.appendChild(buildCreateRow(q)); }
      }
    }

    // A row here is a plain div, not a native control, so it needs its own
    // keyboard support — the same tabIndex/keydown pattern buildTickRow
    // already uses elsewhere in this file for the identical reason.
    function activateOnKey(row, activate) {
      row.tabIndex = 0;
      row.setAttribute('role', 'option');
      row.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); activate(); }
      });
    }

    function buildRow(company) {
      var row = document.createElement('div');
      row.className = 'rc-row';
      if (selected && selected.id === company.id) { row.classList.add('is-selected'); }
      var name = document.createElement('span');
      name.className = 'rc-row-name';
      name.textContent = company.name;
      name.setAttribute('dir', 'auto');
      row.appendChild(name);
      function activate() {
        select({ id: company.id, name: company.name, code: company.code });
        Array.prototype.forEach.call(listEl.querySelectorAll('.rc-row'), function (r) {
          r.classList.remove('is-selected');
        });
        row.classList.add('is-selected');
      }
      row.addEventListener('click', activate);
      activateOnKey(row, activate);
      return row;
    }

    function buildCreateRow(name) {
      var row = document.createElement('div');
      row.className = 'rc-row mc-row-add';
      row.textContent = '+ Add "' + name + '"';
      function activate() {
        post(CFG.clientCreateUrl, { name: name }).then(function (data) {
          if (!data.ok) { return; }
          // Whatever came back — brand new, or an existing near-duplicate the
          // server matched instead — is treated identically: stage it as the
          // selected company and re-list so it shows (and reads as selected)
          // among the real results too.
          inputEl.value = '';
          search('');
          select({ id: data.client.id, name: data.client.name, code: data.client.code });
        });
      }
      row.addEventListener('click', activate);
      activateOnKey(row, activate);
      return row;
    }

    inputEl.addEventListener('input', function () {
      var q = inputEl.value;
      if (selected) { select(null); }
      // Same debounce timing as companies.js's own "All" tab search and this
      // file's own admin "us" search — reused here rather than a third
      // value (US_SEARCH_DEBOUNCE_MS is declared further down this file, but
      // already has its value by the time this callback ever actually runs).
      window.clearTimeout(debounce);
      debounce = window.setTimeout(function () { search(q); }, US_SEARCH_DEBOUNCE_MS);
    });

    return {
      search: search,
      reset: function () {
        inputEl.value = '';
        if (selected) { select(null); }
        listEl.innerHTML = '';
        window.clearTimeout(debounce);
      },
      selected: function () { return selected; }
    };
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

  // The last successful runInquiryForClient() answer, kept around only for
  // the duration of that query — {client:{id,name}, byField:{field:[doc_no,
  // ...]}}, one entry per lit/focus field ("us" included, keyed off the same
  // doc-number list the panel/annotation already use). openModalForField
  // reads this to show a lit/focus card's OWN filtered view mid-query
  // instead of its normal full list; cleared, like every other query-mode
  // visual, by clearQueryMarks().
  var lastQueryData = null;

  // A lit/focus card's badge temporarily shows what THIS query attached to
  // it rather than its normal server-rendered total (see setContextualBadge,
  // called from runInquiryForClient) — this is where each field's ORIGINAL
  // badge text is stashed so clearQueryMarks() can always put it back,
  // whether or not that field happened to still be lit when Clear was hit.
  var stashedBadgeText = {};

  function setContextualBadge(field, count) {
    var g = nodesByField[field];
    var textEl = g && g.querySelector('.rc-badge-text');
    if (!textEl) { return; }
    if (!Object.prototype.hasOwnProperty.call(stashedBadgeText, field)) {
      stashedBadgeText[field] = textEl.textContent;
    }
    textEl.textContent = count > 999 ? '999+' : String(count);
  }

  function restoreContextualBadges() {
    Object.keys(stashedBadgeText).forEach(function (field) {
      var g = nodesByField[field];
      var textEl = g && g.querySelector('.rc-badge-text');
      if (textEl) { textEl.textContent = stashedBadgeText[field]; }
    });
    stashedBadgeText = {};
  }

  function clearQueryMarks() {
    Object.keys(nodesByField).forEach(function (field) {
      nodesByField[field].classList.remove('is-focus', 'is-lit');
    });
    restoreContextualBadges();
    lastQueryData = null;
    queryLinesG.innerHTML = '';
    hideUsCasesPanel();
    clearNodeOverlays();
  }

  // ------------------------------------------------------------------------
  // Per-card name overlays — while an Inquiry is active, every TOUCHED card
  // (the focus card AND every lit card it connects to) gets a small floating
  // tag showing the queried entity's own name directly below its own box, so
  // it reads right on the chart itself rather than only inside a card's own
  // modal (the owner's own separate ask for the focus card doubles up here
  // too — see runInquiryForClient below). Built generically over "whatever
  // names lastQueryData.namesByField lists for this field" rather than
  // hardcoding a single name, even though today's data model only ever
  // attaches ONE focused entity per query, so every touched field's array
  // happens to hold just that one name — see runInquiryForClient's own
  // namesByField construction.
  //
  // Positioned with plain HTML (position:absolute inside #rcCanvas), reusing
  // the EXACT same viewBox-to-pixel conversion the "us" cases panel's own
  // drawUsCasesConnector() above already uses, just in the opposite direction
  // (SVG units -> screen pixels rather than the reverse) — see
  // positionNodeOverlay(). Placed entirely BELOW the card's own box (never
  // overlapping it), so it can never steal a click meant for the node itself;
  // capped to a handful of short rows in CSS (rolechart.css's
  // .rc-node-overlay) with its own overflow-y:auto scroll, the same
  // "build once, cap the height, scroll for more" convention the "us" cases
  // list already follows, so a future multi-name field never grows the chart
  // itself. Built fresh on every successful query and torn down, all at
  // once, by clearNodeOverlays() (called from clearQueryMarks() above).
  // ------------------------------------------------------------------------
  var nodeOverlays = {}; // field -> the overlay element currently shown for it

  function buildNodeOverlay(names) {
    var el = document.createElement('div');
    el.className = 'rc-node-overlay';
    names.forEach(function (name) {
      var nameEl = document.createElement('div');
      nameEl.className = 'rc-node-overlay-name';
      nameEl.textContent = name;
      nameEl.setAttribute('dir', 'auto');
      el.appendChild(nameEl);
    });
    return el;
  }

  // rect (nodeRect(field)) is in the SVG's own viewBox units — converted to a
  // pixel offset relative to #rcCanvas (the overlay's own containing block)
  // via the svg's real on-screen rect and the viewBox->pixel scale, the same
  // ratio drawUsCasesConnector()/positionUsCasesPanel() already derive
  // elsewhere in this file, just applied to place a point rather than to
  // measure one. Anchored on the box's own horizontal centre and just below
  // its bottom edge; `transform:translateX(-50%)` (rolechart.css) does the
  // actual centring so this only has to compute one x, not a left edge.
  function positionNodeOverlay(el, field) {
    var rect = nodeRect(field);
    var svgBox = svg.getBoundingClientRect();
    if (!rect || !rcCanvas || !svgBox.width || !svgBox.height) { return; }
    var canvasBox = rcCanvas.getBoundingClientRect();
    var viewBoxParts = svg.getAttribute('viewBox').split(' ');
    var viewW = parseFloat(viewBoxParts[2]);
    var viewH = parseFloat(viewBoxParts[3]);
    var scaleX = svgBox.width / viewW;
    var scaleY = svgBox.height / viewH;
    var left = (svgBox.left - canvasBox.left) + rect.cx * scaleX;
    var top = (svgBox.top - canvasBox.top) + (rect.y + rect.h) * scaleY;
    el.style.left = left + 'px';
    el.style.top = (top + 4) + 'px'; // a small gap below the card's own box
  }

  function showNodeOverlays(fields) {
    if (!lastQueryData || !rcCanvas) { return; }
    fields.forEach(function (field) {
      var names = lastQueryData.namesByField[field];
      if (!names || !names.length) { return; }
      var el = buildNodeOverlay(names);
      rcCanvas.appendChild(el);
      positionNodeOverlay(el, field);
      nodeOverlays[field] = el;
    });
  }

  function clearNodeOverlays() {
    Object.keys(nodeOverlays).forEach(function (field) {
      var el = nodeOverlays[field];
      if (el && el.parentNode) { el.parentNode.removeChild(el); }
    });
    nodeOverlays = {};
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
  // vertically placed here (in pixels, at open time) around
  // usCasesAnchorY()'s own point but clamped to the canvas's own current
  // bounds — see positionUsCasesPanel() below for why a plain percentage
  // centred on that point is not enough on its own.
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
  // sub-heading per label plus its cases underneath, rather than one
  // flat list. The label's own display text is read off the matching label
  // card's own `data-role` attribute (nodesByField), the same Persian name
  // that card already shows, so nothing here has to duplicate FIELD_LABELS.
  //
  // Each case row also shows a small status badge (c.status_fa — the
  // server-computed Persian status text now included alongside doc_no/label
  // on every case entry this same array already carries) using the same
  // .rc-row-badge pill this file already uses elsewhere for a small
  // trailing tag; shown for every viewer, since it is purely informational.
  // Admin/GM viewers (CFG.isAdminTier) additionally get the row itself
  // clickable, navigating to that case's own detail page via
  // CFG.caseDetailUrlBase — see home.html for how that URL base is built.
  // Not gated for anyone else: a non-admin/GM viewer's row stays exactly as
  // plain/unclickable as it always was.
  function renderUsCasesPanelContent(cases) {
    usCasesList.innerHTML = '';
    var order = [];
    var byLabel = {};
    cases.forEach(function (c) {
      if (!byLabel[c.label]) { byLabel[c.label] = []; order.push(c.label); }
      byLabel[c.label].push(c);
    });
    order.forEach(function (label) {
      var labelCases = byLabel[label];
      var group = document.createElement('div');
      group.className = 'rc-us-cases-group';
      var heading = document.createElement('div');
      heading.className = 'rc-us-cases-group-heading';
      var labelNode = nodesByField[label];
      heading.textContent = (labelNode ? labelNode.getAttribute('data-role') : label) + ' (' + labelCases.length + ')';
      heading.setAttribute('dir', 'auto');
      group.appendChild(heading);
      labelCases.forEach(function (c) {
        var row = document.createElement('div');
        row.className = 'rc-us-cases-row';
        var docSpan = document.createElement('span');
        docSpan.textContent = c.doc_no;
        row.appendChild(docSpan);
        if (c.status_fa) {
          var badge = document.createElement('span');
          badge.className = 'rc-row-badge';
          badge.textContent = c.status_fa;
          badge.setAttribute('dir', 'auto');
          row.appendChild(badge);
        }
        if (CFG.isAdminTier && CFG.caseDetailUrlBase && c.case_id != null) {
          row.classList.add('is-clickable');
          row.addEventListener('click', function () {
            window.location.href = CFG.caseDetailUrlBase + c.case_id + '/';
          });
        }
        group.appendChild(row);
      });
      usCasesList.appendChild(group);
    });
  }

  // Anchored to the vertical span "rival" and "supplier" occupy TOGETHER
  // (they're stacked, one directly above the other, in the chart's own
  // bottom-left corner — see rolechart.py) rather than to the "us" node's
  // own centre-y: the owner wants this panel genuinely OCCUPYING the
  // now-freed bottom-right corner, symmetrically opposite that stack, not
  // floating near "us". Weighted toward the TOP of that span (25% of the way
  // down, not the exact 50% midpoint) so the panel itself sits comfortably
  // higher rather than crowding the chart's own bottom edge. Falls back to
  // "us"'s own centre-y only if either rect is somehow missing (should not
  // happen on this chart's fixed layout, but nodeRect() already returns null
  // defensively).
  function usCasesAnchorY() {
    var rivalRect = nodeRect('rival');
    var supplierRect = nodeRect('supplier');
    if (rivalRect && supplierRect) {
      var spanTop = rivalRect.y;
      var spanBottom = supplierRect.y + supplierRect.h;
      return spanTop + (spanBottom - spanTop) * 0.25;
    }
    var usRect = nodeRect('us');
    return usRect ? usRect.cy : null;
  }

  // The list's own max-height, in pixels, bounded against the CHART
  // CANVAS's own current on-screen height rather than a fixed value that
  // could exceed a short canvas on a small screen — re-measured every time
  // the panel opens (openUsCasesPanel rebuilds it fresh each time anyway).
  // ``chrome`` accounts for the panel's own title row and padding
  // (.rc-us-cases-title plus the panel's own top/bottom padding). Reserves
  // ``margin`` on BOTH the top and bottom of the canvas (not just once) —
  // this is only the FIRST of two guards against the panel sticking out
  // past the canvas: it caps the panel's total height against the canvas's
  // own total height regardless of where the panel ends up sitting;
  // positionUsCasesPanel() below is the second, tighter guard, clamping the
  // panel's actual on-screen TOP against its own real rendered height once
  // this cap has already been applied.
  var US_CASES_PANEL_CHROME = 46; // title row + vertical padding, roughly
  var US_CASES_PANEL_MARGIN = 24; // breathing room top/bottom within the canvas
  function usCasesListMaxHeight() {
    if (!rcCanvas) { return 440; }
    var canvasH = rcCanvas.getBoundingClientRect().height;
    if (!canvasH) { return 440; }
    var available = canvasH - US_CASES_PANEL_CHROME - 2 * US_CASES_PANEL_MARGIN;
    return Math.max(90, Math.min(620, available));
  }

  // Places the now-rendered, now-sized panel's own TOP so the whole box —
  // not just the point it is nominally anchored to — stays inside the
  // canvas. usCasesAnchorY() (in the SVG's own viewBox Y-units) is first
  // converted to an actual on-screen pixel offset from the canvas's own top
  // edge, the same viewBox-height ratio drawUsCasesConnector() already uses
  // elsewhere in this file for the reverse conversion. The panel is then
  // measured AT ITS REAL RENDERED SIZE (already capped by
  // usCasesListMaxHeight() above, so a case-heavy client's list is already
  // scrolling, not still growing) and centred on that pixel as closely as
  // the canvas allows — clamped into [MARGIN, canvasH - height - MARGIN] so
  // neither edge can ever sit outside the canvas. A plain CSS
  // transform:translateY(-50%) used to do the centring instead, which broke
  // exactly the case this function exists for: the rival/supplier anchor
  // usually sits well down toward the canvas's own bottom-right corner, so
  // centring blindly on it let a tall panel's bottom edge sail straight past
  // the canvas even with plenty of headroom sitting unused above.
  function positionUsCasesPanel(anchorY) {
    if (!rcCanvas) { return; }
    var canvasH = rcCanvas.getBoundingClientRect().height;
    if (!canvasH) { return; }
    var viewBoxParts = svg.getAttribute('viewBox').split(' ');
    var viewH = parseFloat(viewBoxParts[3]);
    var anchorPx = (anchorY / viewH) * canvasH;
    var panelH = usCasesPanel.getBoundingClientRect().height ||
      (usCasesList.getBoundingClientRect().height + US_CASES_PANEL_CHROME);
    var top = anchorPx - panelH / 2;
    var minTop = US_CASES_PANEL_MARGIN;
    var maxTop = canvasH - panelH - US_CASES_PANEL_MARGIN;
    top = maxTop >= minTop ? Math.max(minTop, Math.min(top, maxTop)) : Math.max(0, (canvasH - panelH) / 2);
    usCasesPanel.style.top = top + 'px';
  }

  function openUsCasesPanel(cases) {
    var anchorY = usCasesAnchorY();
    if (anchorY === null) { return; }
    renderUsCasesPanelContent(cases);
    usCasesList.style.maxHeight = usCasesListMaxHeight() + 'px';
    // Unhidden BEFORE positioning — positionUsCasesPanel() needs the
    // panel's own real rendered height, which a [hidden] (display:none)
    // element cannot report.
    usCasesPanel.hidden = false;
    positionUsCasesPanel(anchorY);
  }

  function hideUsCasesPanel() {
    usCasesPanel.hidden = true;
    usCasesList.innerHTML = '';
  }

  // The trunk: the "us" node's own right edge to the panel's left edge, at
  // whatever y the panel is anchored to (see usCasesAnchorY/openUsCasesPanel
  // above — no longer "us"'s own centre-y, now the freed bottom-right
  // corner) — a small elbow rather than the old flat horizontal line, since
  // the panel usually sits well below "us" now. The panel is plain HTML
  // positioned over the responsive SVG, so its own pixel-space geometry is
  // converted back into the SVG's viewBox units (the same units every other
  // query line is already drawn in) via the ratio between the viewBox width
  // and the SVG's current on-screen width — every rect read from the DOM
  // below (the panel's own, and each group heading's, for the branches)
  // reuses this exact same ``scale`` conversion, never re-derived.
  // Reuses .rc-query-line and animateFill() exactly as every other query
  // line does — no second trunk-line style.
  //
  // Then the branches: one short, light path per case GROUP heading
  // currently rendered in the panel ("شاخه شاخه شاخه" — branch by branch by
  // branch — the owner's own words) — reaching a little way INSIDE the
  // panel toward that heading's own y, clearly thinner/fainter than the
  // trunk (.rc-us-cases-branch, not .rc-query-line — see rolechart.css) so
  // it reads as a light flourish, not competing visual noise. A heading
  // currently scrolled out of view within the panel's own scrolling list
  // (.rc-us-cases-list already scrolls — see the CSS) is skipped rather than
  // drawn toward a garbage point: checked against the LIST's own visible
  // rect, not just a zero-size check, since a scrolled-out heading still has
  // a real size, just outside what is currently shown.
  function drawUsCasesConnector() {
    if (usCasesPanel.hidden) { return; }
    var usRect = nodeRect('us');
    var svgBox = svg.getBoundingClientRect();
    var panelBox = usCasesPanel.getBoundingClientRect();
    if (!usRect || !svgBox.width || !panelBox.width) { return; }
    var viewBoxParts = svg.getAttribute('viewBox').split(' ');
    var viewW = parseFloat(viewBoxParts[2]);
    var scale = viewW / svgBox.width;
    var toSvgX = function (clientX) { return (clientX - svgBox.left) * scale; };
    var toSvgY = function (clientY) { return (clientY - svgBox.top) * scale; };
    var panelLeftX = toSvgX(panelBox.left);
    var panelWidth = panelBox.width * scale;
    var panelCenterY = toSvgY(panelBox.top + panelBox.height / 2);
    var ns = 'http://www.w3.org/2000/svg';

    var trunkStart = { x: usRect.x + usRect.w, y: usRect.cy };
    var trunkEnd = { x: panelLeftX, y: panelCenterY };
    var trunkMidX = trunkStart.x + (trunkEnd.x - trunkStart.x) * 0.55;
    var trunkPts = dedupePoints([
      trunkStart,
      { x: trunkMidX, y: trunkStart.y },
      { x: trunkMidX, y: trunkEnd.y },
      trunkEnd
    ]);
    if (trunkPts.length < 2) { return; }
    var trunk = document.createElementNS(ns, 'path');
    trunk.setAttribute('class', 'rc-query-line');
    trunk.setAttribute('d', elbowPath(trunkPts, ELBOW_R));
    queryLinesG.appendChild(trunk);
    animateFill(trunk);

    var listBox = usCasesList.getBoundingClientRect();
    var branchInX = panelLeftX + panelWidth * 0.55; // "as if it goes inside the box"
    Array.prototype.forEach.call(usCasesList.querySelectorAll('.rc-us-cases-group-heading'), function (heading) {
      var hb = heading.getBoundingClientRect();
      if (!hb.width || !hb.height) { return; }
      if (hb.bottom <= listBox.top || hb.top >= listBox.bottom) { return; } // scrolled out of view
      var headingY = toSvgY(hb.top + hb.height / 2);
      var branchPts = dedupePoints([
        trunkEnd,
        { x: branchInX, y: trunkEnd.y },
        { x: branchInX, y: headingY },
        { x: panelLeftX + panelWidth * 0.85, y: headingY }
      ]);
      if (branchPts.length < 2) { return; }
      var branch = document.createElementNS(ns, 'path');
      branch.setAttribute('class', 'rc-us-cases-branch');
      branch.setAttribute('d', elbowPath(branchPts, 8));
      queryLinesG.appendChild(branch);
      animateFill(branch);
    });
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
      // Each lit/focus field's badge temporarily shows what THIS query
      // attached to it — case_numbers.length where a case backs it,
      // falling back to 1 for a manual-only tag with no case behind it —
      // and "us" uses the same distinct doc-number count the panel/
      // annotation already computed above (docNos), not a per-label count.
      // The same per-field doc numbers are stashed on lastQueryData so a
      // click on any of these cards, while the query stays active, can show
      // just that field's own slice instead of its normal full list — see
      // openModalForField's own is-query check. Both are undone together by
      // clearQueryMarks().
      var byField = {};
      data.labels.forEach(function (l) {
        var count = (l.case_numbers && l.case_numbers.length) ? l.case_numbers.length : 1;
        byField[l.label] = l.case_numbers || [];
        setContextualBadge(l.label, count);
      });
      if (showUsPanel) {
        byField.us = docNos;
        setContextualBadge('us', docNos.length);
      }
      // One name per touched field, generically — every field in allFields
      // gets the SAME array today (there is only ever one focused entity per
      // query in the current data model), but this is built per-field rather
      // than hardcoded so a future round that attaches more than one entity
      // to a field just works — see showNodeOverlays() above.
      var namesByField = {};
      allFields.forEach(function (field) { namesByField[field] = [client.name]; });
      lastQueryData = { client: { id: client.id, name: client.name }, byField: byField, namesByField: namesByField };
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
        // Every touched field — focus and lit alike — gets its own small
        // name overlay; allFields already IS that whole set (focusField
        // plus litFields, and "us" too when showUsPanel pushed it above).
        showNodeOverlays(allFields);
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
  // too, but that reads as scope creep for a button that already has its own
  // distinct meaning from this card's own direct click (see
  // fetchAllCasesSearch/renderAllCasesRows) — this just marks "us" as the
  // query subject, matching the task's own simpler fallback reading.
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

  // ------------------------------------------------------------------------
  // The "+ Add company" panel (change 2) — a reusable right-side sub-panel
  // available from EVERY label card's modal, in both default browsing and
  // wizard "picking" mode alike. Built once here, moving the template's own
  // existing modal-body nodes (the search box, the company list, and both
  // action bars) into a new .rc-modal-columns/.rc-modal-main wrapper — see
  // rolechart.css — so this panel can sit BESIDE them as a second column
  // instead of only ever stacking underneath. The same "build once near the
  // top of the file, toggle hidden/visible on demand" convention as
  // #rcWizardBar / the "us" cases panel above; opening it just widens the
  // SAME modal (.rc-modal.is-wide) — never a second window.
  // ------------------------------------------------------------------------
  var modalEl = overlay.querySelector('.rc-modal');
  var modalHeadEl = overlay.querySelector('.rc-modal-head');

  var modalColumns = document.createElement('div');
  modalColumns.className = 'rc-modal-columns';
  var modalMain = document.createElement('div');
  modalMain.className = 'rc-modal-main';
  // Moves (does not clone) the template's own existing nodes — appendChild
  // on an element already in the document detaches it from its old parent
  // first, so this is purely a reparent, never a re-template.
  modalMain.appendChild(searchWrap);
  modalMain.appendChild(listEl);
  modalMain.appendChild(actionsDefault);
  modalMain.appendChild(actionsPicking);
  modalColumns.appendChild(modalMain);
  modalEl.appendChild(modalColumns);

  // The trigger lives in the modal HEAD, not either action bar — the one
  // spot shared by both modalMode "default" and "picking", so a single
  // button (not two, one per mode) covers change 2b/2d's "visible in BOTH
  // modes" requirement without duplicating it.
  var addCompanyBtn = document.createElement('button');
  addCompanyBtn.type = 'button';
  addCompanyBtn.className = 'btn btn-sm rc-modal-add';
  addCompanyBtn.textContent = '+ Add company';
  addCompanyBtn.hidden = true;
  modalHeadEl.insertBefore(addCompanyBtn, closeX);

  var addCompanyPanel = document.createElement('div');
  addCompanyPanel.className = 'rc-add-company-panel';
  addCompanyPanel.hidden = true;

  var addCompanyHead = document.createElement('div');
  addCompanyHead.className = 'rc-add-company-head';
  var addCompanyTitle = document.createElement('div');
  addCompanyTitle.className = 'rc-add-company-title';
  addCompanyTitle.textContent = 'Add a company';
  addCompanyHead.appendChild(addCompanyTitle);
  var addCompanyCloseBtn = document.createElement('button');
  addCompanyCloseBtn.type = 'button';
  addCompanyCloseBtn.className = 'rc-modal-x';
  addCompanyCloseBtn.setAttribute('aria-label', 'Close add-company panel');
  addCompanyCloseBtn.innerHTML = '<i class="fa-solid fa-xmark" aria-hidden="true"></i>';
  addCompanyHead.appendChild(addCompanyCloseBtn);
  addCompanyPanel.appendChild(addCompanyHead);

  // Reuses .rc-modal-search/.rc-modal-search-input exactly as the main
  // column's own search box does — see rolechart.css.
  var addCompanySearchWrap = document.createElement('div');
  addCompanySearchWrap.className = 'rc-modal-search';
  var addCompanySearchInput = document.createElement('input');
  addCompanySearchInput.type = 'text';
  addCompanySearchInput.className = 'rc-modal-search-input';
  addCompanySearchInput.placeholder = 'Search the whole directory…';
  addCompanySearchInput.autocomplete = 'off';
  addCompanySearchInput.setAttribute('dir', 'auto');
  addCompanySearchWrap.appendChild(addCompanySearchInput);
  addCompanyPanel.appendChild(addCompanySearchWrap);

  // Reuses .rc-modal-list/.rc-row/.rc-row-empty/.mc-row-add exactly as
  // companies.js's own "All" tab and this file's own label list already do.
  var addCompanyList = document.createElement('div');
  addCompanyList.className = 'rc-modal-list';
  addCompanyPanel.appendChild(addCompanyList);

  var addCompanyActions = document.createElement('div');
  addCompanyActions.className = 'rc-modal-actions';
  var addCompanyConfirmBtn = document.createElement('button');
  addCompanyConfirmBtn.type = 'button';
  addCompanyConfirmBtn.className = 'btn btn-sm btn-primary';
  addCompanyConfirmBtn.textContent = 'Add';
  addCompanyConfirmBtn.disabled = true;
  addCompanyActions.appendChild(addCompanyConfirmBtn);
  addCompanyPanel.appendChild(addCompanyActions);

  modalColumns.appendChild(addCompanyPanel);

  // At most one company staged at a time, either picked straight off the
  // search results or just registered via the create-new row — the two are
  // functionally identical from here on, since client_create's own near-
  // duplicate check (services.get_or_create_client/normalize_persian) may
  // hand back an EXISTING client for a near-duplicate name instead of a
  // fresh one — see createCompanyPicker's own buildCreateRow, which relies
  // on that server-side check rather than any separate client-side one
  // (change 2e). The search/create mechanism itself is the one shared
  // helper further up this file (createCompanyPicker) — this is just that
  // helper's own confirm-button wiring, specific to THIS panel.
  var addCompanyPicker = createCompanyPicker(addCompanyList, addCompanySearchInput, function (client) {
    addCompanyConfirmBtn.disabled = !client;
  });

  function updateAddCompanyButtonVisibility() {
    addCompanyBtn.hidden = !(CAN_EDIT && modalKind === 'label');
  }

  function openAddCompanyPanel() {
    if (!CAN_EDIT || modalKind !== 'label') { return; }
    modalEl.classList.add('is-wide');
    addCompanyPanel.hidden = false;
    addCompanyPicker.reset();
    addCompanyConfirmBtn.disabled = true;
    addCompanyPicker.search('');
    addCompanySearchInput.focus();
  }

  function closeAddCompanyPanel() {
    modalEl.classList.remove('is-wide');
    addCompanyPanel.hidden = true;
    addCompanyPicker.reset();
  }

  // Commits the staged company to the CURRENTLY OPEN card's own field —
  // identical in default and picking mode, since both just mean "attach
  // this company to modalField" (change 2d: no special-casing between the
  // two modes at all).
  addCompanyConfirmBtn.addEventListener('click', function () {
    var client = addCompanyPicker.selected();
    if (!client || modalKind !== 'label' || !modalField) { return; }
    var field = modalField;
    addCompanyConfirmBtn.disabled = true;
    post(CFG.labelToggleUrl, { client_id: client.id, label: field, add: 1 }).then(function (data) {
      addCompanyConfirmBtn.disabled = false;
      if (!data.ok || modalField !== field) { return; }
      closeAddCompanyPanel();
      refreshLabelList();
    });
  });

  addCompanyBtn.addEventListener('click', openAddCompanyPanel);
  addCompanyCloseBtn.addEventListener('click', closeAddCompanyPanel);

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
  // The admin/GM "us" card search's own debounce handle — same shape as
  // companies.js's searchDebounce/SEARCH_DEBOUNCE_MS pair for its own
  // server-backed search (the label card's own search stays a plain
  // instant local filter, same as companies.js's label-tab branch, so it
  // has no debounce of its own to mirror).
  var usSearchDebounce = null;
  var US_SEARCH_DEBOUNCE_MS = 200;

  function closeModal() {
    overlay.hidden = true;
    modalField = null;
    modalKind = null;
    selectedEntity = null;
    currentLabelCompanies = [];
    window.clearTimeout(usSearchDebounce);
    closeAddCompanyPanel();
  }
  closeX.addEventListener('click', closeModal);
  closeDefaultBtn.addEventListener('click', closeModal);
  closeCardBtn.addEventListener('click', closeModal);
  overlay.addEventListener('click', function (ev) {
    if (ev.target === overlay) { closeModal(); }
  });
  document.addEventListener('keydown', function (ev) {
    if (ev.key !== 'Escape' || overlay.hidden) { return; }
    // The "+ Add company" panel reads as a layer ON TOP of the card's own
    // modal (it widens the same window rather than opening a new one) — so
    // Escape backs out of that layer first, same as it would a second real
    // dialog, and only closes the whole modal once that layer is gone.
    if (!addCompanyPanel.hidden) { closeAddCompanyPanel(); return; }
    closeModal();
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
    // The label card's own browsing search stays exactly as it was; an
    // admin/GM's "us" card click gets the same search box, wired instead to
    // fetchAllCasesSearch (see below) rather than the local company filter.
    searchWrap.hidden = !(modalMode === 'default' && (modalKind === 'label' || (modalKind === 'us' && CFG.isAdminTier)));
    actionsDefault.hidden = modalMode !== 'default';
    actionsPicking.hidden = modalMode !== 'picking';
    configureDefaultActions();
    updateActionState();
    // A fresh open never inherits a previous card's widened "+ Add company"
    // layer — always starts collapsed; the button's own visibility is then
    // set fresh for THIS card's kind/mode.
    closeAddCompanyPanel();
    updateAddCompanyButtonVisibility();
    overlay.hidden = false;
    listEl.innerHTML = '';

    // Mid-query contextual view (applies to every viewer, not just
    // admin/GM): a card that is currently lit or focused by an ACTIVE query
    // shows only what THAT query attached to it, not its normal full list —
    // see runInquiryForClient's own ``lastQueryData`` stash. Skipped
    // whenever wizard "picking" mode applies instead — the two are
    // unrelated, and picking mode always keeps its own normal behaviour.
    if (modalMode === 'default' && chartRoot.classList.contains('is-query') && lastQueryData) {
      var g = nodesByField[field];
      if (g && (g.classList.contains('is-lit') || g.classList.contains('is-focus'))) {
        searchWrap.hidden = true;
        // Re-running Inquiry or Attach from inside an already-active query's
        // read-out does not make sense — configureDefaultActions() just set
        // both of these visible for a 'label' kind card moments ago; this
        // overrides that specifically for the contextual case, leaving only
        // the always-present Close button. attachBtn was already effectively
        // dead here (selectedEntity is never set in this read-only mode) —
        // hiding it alongside inquiryBtn is a small consistency improvement,
        // not a behaviour change. Restored on the next open that does not
        // hit this branch, since configureDefaultActions() runs fresh at the
        // top of every openModalForField() call. "+ Add company" gets the
        // same treatment, for the same reason.
        inquiryBtn.hidden = true;
        attachBtn.hidden = true;
        addCompanyBtn.hidden = true;
        renderContextualQueryRow(field);
        return;
      }
    }

    if (modalMode === 'picking') {
      renderPickingRow();
    } else if (modalKind === 'inert') {
      renderInertMessage();
    } else if (modalKind === 'us') {
      if (CFG.isAdminTier) { fetchAllCasesSearch(''); } else { renderEmptyRow('Nothing connected yet.'); }
    } else {
      fetchLabelCompanies();
    }
    if (!searchWrap.hidden) { searchInput.focus(); }
  }

  // The one shared empty-state row — a plain, centred, neutral message, no
  // list, no controls beyond whatever the modal's own action bar already
  // shows. Used by "inert" cards below, and by an ordinary viewer's own
  // "Foolad Tabar" click (see openModalForField's ``us`` branch) now that its
  // own read-only case list is gone for everyone but admin/GM.
  function renderEmptyRow(text) {
    var empty = document.createElement('div');
    empty.className = 'rc-row-empty';
    empty.textContent = text;
    listEl.appendChild(empty);
  }

  // ---- mid-query contextual view: a lit/focused card, clicked while an
  // Inquiry is still active — see openModalForField's own is-query check.
  // Not admin/GM-gated: any viewer can run an ordinary Inquiry from a label
  // card, and this reads the very same lastQueryData every viewer's
  // runInquiryForClient() already stashes (module-level, cleared by
  // clearQueryMarks()). Shows just the queried company's own name — no case
  // numbers here (the owner's own words: "its case names come up which
  // should not come up") — reusing the read-only static row shell
  // (.rc-row-static) the old renderUsRows used for the same shape, since
  // this row is equally never clickable (it is a read-out of the active
  // query, not a new selection).
  function renderContextualQueryRow(field) {
    listEl.innerHTML = '';
    if (!lastQueryData) { renderEmptyRow('Nothing connected yet.'); return; }
    var row = document.createElement('div');
    row.className = 'rc-row rc-row-static';
    var name = document.createElement('span');
    name.className = 'rc-row-name';
    name.textContent = lastQueryData.client.name;
    name.setAttribute('dir', 'auto');
    row.appendChild(name);
    listEl.appendChild(row);
  }

  // ---- "inert" cards: project / phase / laboratory / tpi / supplier / rival
  // Deliberate empty state, not a bug — nothing feeds any of these six
  // fields this round, so there is nothing to list and nothing to do beyond
  // Close (already the only visible button — see configureDefaultActions).
  function renderInertMessage() {
    renderEmptyRow('Not connected to anything yet.');
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

  // Refreshes whichever view of a label card's company list is currently
  // showing — the normal default-mode fetch/render, or, while wizard
  // "picking" mode is showing a DIFFERENT card, that mode's own fetch/render
  // — so a row action (the × on any row, per change 1, or a completed
  // "+ Add company", per change 2c) always redraws into the view the viewer
  // is actually looking at instead of snapping back to default-mode
  // browsing regardless of what was open.
  function refreshLabelList() {
    if (modalMode === 'picking') { renderPickingRow(); } else { fetchLabelCompanies(); }
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
    } else if (modalKind === 'us' && modalMode === 'default') {
      // Unlike the label branch above, this IS a server round trip (every
      // case in the system, not a small already-fetched label list), so it
      // gets the same debounce companies.js's own server-backed search uses
      // — see usSearchDebounce/US_SEARCH_DEBOUNCE_MS above.
      var q = searchInput.value;
      window.clearTimeout(usSearchDebounce);
      usSearchDebounce = window.setTimeout(function () { fetchAllCasesSearch(q); }, US_SEARCH_DEBOUNCE_MS);
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
    // "via case ..." badge here (that per-case detail lives on the query
    // overlay's own contextual filtered row while a query is active, see
    // renderContextualQueryRow, and on the Companies tab's separate
    // companies.js, neither of which this touches). A
    // case-derived fact still never gets a delete control — it can NEVER be
    // edited from the chart — it just now renders identically to any other
    // row instead of growing a badge for it.

    // A manual tag gets a "×" only when THIS viewer actually owns it
    // (``removable``) — an Expert cannot remove a Supervisor's or another
    // Expert's tag; the endpoint refuses it server-side too, but there is no
    // point rendering a dead click. Unchanged whether this row is rendered
    // by the default browsing view OR by wizard "picking" mode (change 1) —
    // buildLabelRow itself has no notion of which mode called it; only the
    // refresh below (refreshLabelList) has to know, so the right view
    // redraws afterward.
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
          refreshLabelList();
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

  // ---- "us" card, admin/GM viewer: flat, live-searchable all-cases list --
  // Deliberately NOT grouped/clustered like the "cases connected to Us" side
  // panel elsewhere in this file (openUsCasesPanel/renderUsCasesPanelContent)
  // — that panel is about ONE focused company's own cases mid-query; this is
  // a flat browse/search over every case in the system, admin/GM only (the
  // endpoint itself refuses anyone else — see marketing/views.py's
  // all_cases_search). Called once with an empty query the moment the modal
  // opens for this card (see openModalForField's ``us`` branch) so the list
  // starts already populated with the newest cases, and again, debounced, on
  // every keystroke in the modal's own search box (see the searchInput
  // listener above) — the same modalSeq staleness guard fetchLabelCompanies
  // already uses, so a slow first answer can never race a faster later one.
  function fetchAllCasesSearch(query) {
    var seq = modalSeq;
    get(CFG.allCasesSearchUrl, { q: query || '' }).then(function (data) {
      if (seq !== modalSeq || !data.ok) { return; }
      renderAllCasesRows(data.cases);
    });
  }

  // One plain row per case — doc_no plus the client's own name, its
  // label_fa trailing as a small informational tag (.rc-row-badge, the same
  // pill this file already uses for a "via case ..." note elsewhere) — NOT
  // the read-only .rc-row-static shell renderContextualQueryRow above reuses,
  // since these rows ARE actionable: clicking one closes the modal and runs
  // an Inquiry for that case's client with "us" as the origin field, which
  // lights up every other card that client is connected to across the whole
  // chart for free (runInquiryForClient's own client_connections fetch
  // already does this — nothing extra needed here).
  function renderAllCasesRows(cases) {
    listEl.innerHTML = '';
    if (!cases.length) {
      renderEmptyRow('No cases found.');
      return;
    }
    cases.forEach(function (c) {
      var row = document.createElement('div');
      row.className = 'rc-row';
      var name = document.createElement('span');
      name.className = 'rc-row-name';
      name.textContent = c.doc_no + ' — ' + c.client_name;
      name.setAttribute('dir', 'auto');
      row.appendChild(name);
      if (c.label_fa) {
        var badge = document.createElement('span');
        badge.className = 'rc-row-badge';
        badge.textContent = c.label_fa;
        badge.setAttribute('dir', 'auto');
        row.appendChild(badge);
      }
      row.addEventListener('click', function () {
        closeModal();
        runInquiryForClient({ id: c.client_id, name: c.client_name }, 'us');
      });
      listEl.appendChild(row);
    });
  }

  // ---- wizard "picking" mode: reopening ANOTHER label card while armed ---
  // B's own REAL, FULL existing company list — fetched via CFG.labelCompaniesUrl
  // the same way fetchLabelCompanies()/buildLabelRow() fetch and render it for
  // normal browsing. Every row B genuinely carries — X's own included,
  // whenever X already happens to be tagged here — now renders through that
  // SAME buildLabelRow() the default browsing view uses: selectable, and
  // removable via its own × whenever this viewer owns/can-remove that tag,
  // exactly as a normal (non-wizard) click on this card would show it (fix
  // for change 1 — these rows used to be inert, read-only tick-rows; they no
  // longer are). The one thing that stays special to this mode is X's own
  // SYNTHESIZED toggle row, staged via wizard.staged — but only for the ADD
  // case, when X does NOT already carry this label: once X is already a
  // genuine member of B's list (rendered above, like everyone else), its own
  // × already covers "take X out of this card" — a second, separate staged-
  // removal path for the very same fact would just be two conflicting ways
  // to do the one thing, so it is deliberately gone.
  function renderPickingRow() {
    var seq = modalSeq;
    var field = modalField;
    var armedWizard = wizard;
    get(CFG.labelCompaniesUrl, { label: field }).then(function (data) {
      if (seq !== modalSeq || !data.ok || wizard !== armedWizard) { return; }
      var companies = data.companies || [];
      var sourceId = armedWizard.source.id;
      var hasLabel = companies.some(function (c) { return c.id === sourceId; });

      listEl.innerHTML = '';

      // A genuinely empty label (B has zero companies today — the owner's
      // own "Subcontractor" example) still gets X's own togglable row below
      // (the whole point of the wizard is letting X be the FIRST company
      // tagged under an empty card) — this note sits ALONGSIDE it, not in
      // place of it, reusing renderLabelRows' own empty-state text/class.
      // Companies.length already excludes X whenever X is not yet a member
      // (fetched from the server as-is), so this is naturally "B's real list
      // aside from X's own synthesized row," no extra filtering needed.
      if (!companies.length) {
        var empty = document.createElement('div');
        empty.className = 'rc-row-empty';
        empty.textContent = 'No companies tagged yet.';
        listEl.appendChild(empty);
      }

      // Every company B genuinely carries — including X's own, when X is
      // already tagged here — is now a real, interactive row: selectable,
      // and removable via its own × exactly like normal default-mode
      // browsing. Nothing bespoke or read-only left for these.
      companies.forEach(function (company) {
        listEl.appendChild(buildLabelRow(company));
      });

      // X's own SYNTHESIZED toggle row — only when X does NOT already carry
      // this label, the ADD case, which is the whole reason the wizard
      // exists. A small "attaching" tag (.rc-row-badge, already used
      // elsewhere in this file for a small trailing note) marks it as
      // distinct from B's pre-existing members above.
      if (!hasLabel) {
        var staged = Object.prototype.hasOwnProperty.call(armedWizard.staged, field)
          ? armedWizard.staged[field] : false;
        var checkedNow = staged;
        var sourceRow = buildTickRow(armedWizard.source.name, checkedNow, false);
        var attachingTag = document.createElement('span');
        attachingTag.className = 'rc-row-badge';
        attachingTag.textContent = 'attaching';
        sourceRow.appendChild(attachingTag);
        var checkGlyph = sourceRow.querySelector('.mc-panel-check');
        var toggle = function () {
          // Staging is a client-side wizard concept — nothing is sent to the
          // server until Final Confirm. An entry that lands back on the
          // server's own current state (false — X is not a member yet) is
          // dropped rather than kept at "no-op", so the wizard bar's own
          // count only ever reflects real changes.
          checkedNow = !checkedNow;
          sourceRow.classList.toggle('is-checked', checkedNow);
          sourceRow.setAttribute('aria-checked', checkedNow ? 'true' : 'false');
          checkGlyph.textContent = checkedNow ? '✓' : '';
          if (!checkedNow) { delete armedWizard.staged[field]; }
          else { armedWizard.staged[field] = checkedNow; }
          updateWizardBar();
        };
        sourceRow.addEventListener('click', toggle);
        sourceRow.addEventListener('keydown', function (ev) {
          if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); toggle(); }
        });
        listEl.appendChild(sourceRow);
      }
    });
  }

  // The shared tick-row markup — identical shape to companies.js's own
  // buildPanelRow (see that file's comments for why a plain clickable div
  // replaced a native checkbox there): a <div class="rc-row mc-panel-row">
  // toggling is-checked/is-locked, a .mc-panel-check "✓" glyph, a
  // .mc-panel-text name. Returned un-wired — callers that need it
  // interactive (renderPickingRow's own source row) attach their own
  // click/keydown handlers; a `locked` row is left exactly as rendered here,
  // with no tabIndex and aria-disabled set, since it is never meant to be
  // interactive at all.
  function buildTickRow(name, checked, locked) {
    var row = document.createElement('div');
    row.className = 'rc-row mc-panel-row';
    row.classList.toggle('is-checked', checked);
    row.classList.toggle('is-locked', locked);
    row.setAttribute('role', 'checkbox');
    row.setAttribute('aria-checked', checked ? 'true' : 'false');
    if (!locked) { row.tabIndex = 0; } else { row.setAttribute('aria-disabled', 'true'); }

    var check = document.createElement('span');
    check.className = 'mc-panel-check';
    check.setAttribute('aria-hidden', 'true');
    check.textContent = checked ? '✓' : '';
    row.appendChild(check);

    var text = document.createElement('span');
    text.className = 'mc-panel-text';
    text.textContent = name;
    text.setAttribute('dir', 'auto');
    row.appendChild(text);

    return row;
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
  // The top-of-page "Quick Inquiry" card — home.html's own small standalone
  // panel in .page-head, rendered only alongside this same rcChart (the
  // 'roles' tab), well above it on the page. Two fields — a company, then
  // (once picked) a dropdown of THAT company's own current roles, with a
  // small affordance to add a role it does not have yet — and a Confirm
  // button that just calls runInquiryForClient() directly, the exact same
  // function a normal in-chart Inquiry already runs. Nothing here builds a
  // second query mechanism: this only ever gathers the two inputs
  // runInquiryForClient already takes.
  //
  // Every element is looked up and guarded independently, the same
  // convention this whole file already follows (see the top-of-file
  // comment / the `if (!chartRoot || !CFG)` guard above) — the card is only
  // ever templated onto the 'roles' tab, so its absence elsewhere (e.g. the
  // Companies tab) is expected, not an error, and this block simply does
  // nothing on that page.
  // ------------------------------------------------------------------------
  var quickPanel = document.getElementById('rcQuickInquiry');
  if (quickPanel) {
    var quickCompanyInput = document.getElementById('rcQuickCompanyInput');
    var quickCompanyList = document.getElementById('rcQuickCompanyList');
    var quickRoleSelect = document.getElementById('rcQuickRoleSelect');
    var quickAddRoleToggle = document.getElementById('rcQuickAddRoleToggle');
    var quickNewRoleWrap = document.getElementById('rcQuickNewRoleWrap');
    var quickNewRoleSelect = document.getElementById('rcQuickNewRoleSelect');
    var quickNewRoleAdd = document.getElementById('rcQuickNewRoleAdd');
    var quickConfirmBtn = document.getElementById('rcQuickConfirm');

    if (quickCompanyInput && quickCompanyList && quickRoleSelect && quickAddRoleToggle &&
        quickNewRoleWrap && quickNewRoleSelect && quickNewRoleAdd && quickConfirmBtn) {

      // Every "label" card on the chart, read straight off the chart's own
      // nodes (never a second copy of rolechart.py's LABEL_KEYS) — the full
      // set a role can be picked from, and what "add a new role" offers
      // once the ones the company already holds are filtered out below.
      var quickAllLabels = [];
      Array.prototype.forEach.call(svg.querySelectorAll('.rc-node[data-kind="label"]'), function (g) {
        quickAllLabels.push({ field: g.getAttribute('data-field'), role_fa: g.getAttribute('data-role') });
      });

      var quickCompany = null; // the currently picked company, or null
      var quickRoles = [];     // that company's own current labels — client_connections' own shape

      function quickShowDropdown(show) { quickCompanyList.hidden = !show; }

      function quickUpdateConfirmState() {
        quickConfirmBtn.disabled = !quickCompany || !quickRoleSelect.value;
      }

      function quickResetRoleField(placeholderText) {
        quickRoleSelect.innerHTML = '';
        var opt = document.createElement('option');
        opt.value = '';
        opt.textContent = placeholderText;
        quickRoleSelect.appendChild(opt);
        quickRoleSelect.disabled = true;
        quickAddRoleToggle.hidden = true;
        quickNewRoleWrap.hidden = true;
        quickUpdateConfirmState();
      }

      // Renders quickRoles (the company's own current labels) into the role
      // select, optionally leaving a specific field pre-selected — used
      // right after a brand-new role is added, so the reader lands on it
      // rather than back on "Choose a role…".
      function quickPopulateRoleField(selectField) {
        quickRoleSelect.innerHTML = '';
        if (!quickRoles.length) {
          var empty = document.createElement('option');
          empty.value = '';
          empty.textContent = 'No roles yet';
          quickRoleSelect.appendChild(empty);
        } else {
          var placeholder = document.createElement('option');
          placeholder.value = '';
          placeholder.textContent = 'Choose a role…';
          quickRoleSelect.appendChild(placeholder);
          quickRoles.forEach(function (l) {
            var opt = document.createElement('option');
            opt.value = l.label;
            opt.textContent = l.label_fa;
            quickRoleSelect.appendChild(opt);
          });
        }
        quickRoleSelect.disabled = false;
        if (selectField) { quickRoleSelect.value = selectField; }
        // Adding a role from here is the same mutation Attach already makes
        // from the chart (CFG.labelToggleUrl) — gated the same way, on
        // CAN_EDIT, everywhere else in this file already gates a write.
        quickAddRoleToggle.hidden = !CAN_EDIT;
        quickNewRoleWrap.hidden = true;
        quickUpdateConfirmState();
      }

      function quickFetchRoles(company, selectFieldAfter) {
        get(CFG.clientConnectionsUrl, { client_id: company.id }).then(function (data) {
          // Guards against a slow answer landing after the reader already
          // picked a DIFFERENT company (or cleared the field) — the same
          // "only the latest request may paint" discipline every other
          // fetch in this file already follows.
          if (!data.ok || !quickCompany || quickCompany.id !== company.id) { return; }
          quickRoles = data.labels || [];
          quickPopulateRoleField(selectFieldAfter);
        });
      }

      var quickPicker = createCompanyPicker(quickCompanyList, quickCompanyInput, function (client) {
        quickCompany = client;
        quickShowDropdown(false);
        if (client) {
          // The dropdown collapses the instant a company is picked (unlike
          // the "+ Add company" panel, which stays open with the row itself
          // showing is-selected) — so the input's own text becomes the only
          // visible record of what's picked; set it to the company's name
          // rather than leaving whatever the reader had typed to find it.
          quickCompanyInput.value = client.name;
          quickResetRoleField('Loading roles…');
          quickFetchRoles(client, null);
        } else {
          quickResetRoleField('Pick a company first…');
        }
        quickUpdateConfirmState();
      });

      quickCompanyInput.addEventListener('focus', function () {
        quickShowDropdown(true);
        if (!quickCompanyList.childElementCount) { quickPicker.search(quickCompanyInput.value); }
      });
      quickCompanyInput.addEventListener('input', function () { quickShowDropdown(true); });
      // Closes the dropdown on any click outside the whole card — picking a
      // row already closes it itself (quickShowDropdown(false) above), so
      // this is only for "the reader clicked away without choosing".
      document.addEventListener('click', function (ev) {
        if (!quickPanel.contains(ev.target)) { quickShowDropdown(false); }
      });

      quickRoleSelect.addEventListener('change', quickUpdateConfirmState);

      quickAddRoleToggle.addEventListener('click', function () {
        if (!quickCompany) { return; }
        var have = {};
        quickRoles.forEach(function (l) { have[l.label] = true; });
        var available = quickAllLabels.filter(function (l) { return !have[l.field]; });
        quickNewRoleSelect.innerHTML = '';
        if (!available.length) {
          var noneOpt = document.createElement('option');
          noneOpt.value = '';
          noneOpt.textContent = 'Already has every role';
          quickNewRoleSelect.appendChild(noneOpt);
          quickNewRoleAdd.disabled = true;
        } else {
          var placeholder = document.createElement('option');
          placeholder.value = '';
          placeholder.textContent = 'Choose a role to add…';
          quickNewRoleSelect.appendChild(placeholder);
          available.forEach(function (l) {
            var opt = document.createElement('option');
            opt.value = l.field;
            opt.textContent = l.role_fa;
            quickNewRoleSelect.appendChild(opt);
          });
          quickNewRoleAdd.disabled = true;
        }
        quickNewRoleWrap.hidden = false;
      });

      quickNewRoleSelect.addEventListener('change', function () {
        quickNewRoleAdd.disabled = !quickNewRoleSelect.value;
      });

      // Reuses CFG.labelToggleUrl — the exact same "assign a role" endpoint
      // every other flow in this file already calls (Attach's wizard
      // confirm, the "+ Add company" panel, a label row's own ×) — nothing
      // new added to marketing/views.py for this.
      quickNewRoleAdd.addEventListener('click', function () {
        if (!quickCompany || !quickNewRoleSelect.value) { return; }
        var field = quickNewRoleSelect.value;
        var company = quickCompany;
        quickNewRoleAdd.disabled = true;
        post(CFG.labelToggleUrl, { client_id: company.id, label: field, add: 1 }).then(function (data) {
          if (!data.ok || !quickCompany || quickCompany.id !== company.id) { return; }
          quickFetchRoles(company, field);
        });
      });

      // Runs the EXACT SAME thing a normal in-chart Inquiry does — the
      // picked company and role/field handed straight to
      // runInquiryForClient(), never a parallel query path of its own. The
      // widget sits above the fold and the chart usually does not, so the
      // chart's own panel is scrolled into view right after, plainly, so
      // the reader actually sees the query state change on it.
      quickConfirmBtn.addEventListener('click', function () {
        if (!quickCompany || !quickRoleSelect.value) { return; }
        runInquiryForClient(quickCompany, quickRoleSelect.value);
        chartRoot.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });

      quickResetRoleField('Pick a company first…');
    }
  }

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

  // ------------------------------------------------------------------------
  // The "View in marketing chart" deep link (?case=<id>, wired through as
  // CFG.deepLinkCaseId by the view — see marketing/views.py's home()). Runs
  // once, after the chart's own normal setup above, and lands the reader
  // straight on an already-running Inquiry for that case's client — no modal
  // ever opens for this path. allCasesSearchUrl?case_id= is admin/GM-only
  // (see marketing/views.py's all_cases_search); a non-admin/GM reader's
  // request simply 403s, and a stale/deleted case id comes back with zero
  // cases — both are treated identically here: do nothing, no error shown,
  // since ``get()`` resolves on any JSON body regardless of status code.
  if (typeof CFG.deepLinkCaseId === 'number') {
    get(CFG.allCasesSearchUrl, { case_id: CFG.deepLinkCaseId }).then(function (data) {
      if (!data || !data.ok || !data.cases || data.cases.length !== 1) { return; }
      var c = data.cases[0];
      runInquiryForClient({ id: c.client_id, name: c.client_name }, 'us');
    }).catch(function () {});
  }
})();
