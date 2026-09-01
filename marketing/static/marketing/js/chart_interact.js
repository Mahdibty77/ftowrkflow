/* Marketing — on-chart interaction: every field on the project role chart is
 * its own clickable card, opening one reusable modal whose behaviour is
 * gated by the card's own GROUP ("kind", read off its data-kind attribute —
 * see marketing/rolechart.py's ``_kind_of``):
 *
 *   "label"  the twelve MarketingLabel cards. Lists the companies currently
 *            tagged under it (manual and/or case-derived — see
 *            marketing/services.py::companies_for_label), lets a viewer
 *            select one to Inquiry (light up everywhere else it is tagged
 *            — and, in the same stroke, set the ACTIVE ANCHOR — see below).
 *   "us"     the single "our own position" card, "Foolad Tabar" on the
 *            chart. No Add: case links are never editable from the chart.
 *            Its own DIRECT click is no longer the same for every viewer —
 *            an ordinary viewer gets the same plain empty shell an "inert"
 *            card shows; an admin/GM viewer instead gets a flat,
 *            live-searchable list of every case in the system
 *            (services.search_all_cases via CFG.allCasesSearchUrl — see
 *            fetchAllCasesSearch/renderAllCasesRows), and picking a row
 *            there runs an Inquiry for that case's client with "us" as the
 *            origin field, exactly as if it had been picked off a label
 *            card (this one plain Inquiry does NOT set the active anchor —
 *            see below). Its own Inquiry button is a separate thing — it
 *            just marks "us" as the query subject — see runInquiryForUs()
 *            for why it goes no further.
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
 *
 * ONE piece of state DOES live at module level across the whole session:
 * ``activeAnchor`` — one (company, role) pair the whole chart is currently
 * pointed at, e.g. "ASP, as Owner/Client." There are exactly THREE ways to
 * set it, all of which funnel through the one function, ``setActiveAnchor``:
 *
 *   a) The Inquiry button on any "label" card's own browsing list (pick a
 *      company, click Inquiry) — see the ``inquiryBtn`` listener below.
 *   b) "Case mode" (rcCaseModeWidget/rcCaseModeBanner, home.html) — entering
 *      it sets the anchor to the case's own real client, playing whatever
 *      role its ``marketing_label`` currently resolves to, WITH a case id
 *      attached (see ``caseMeta``/``withCaseId`` below) so every connection
 *      made while it is active is recorded as belonging to that case, and
 *      that one card gets the extra ``.is-case-anchor`` mark (see
 *      rolechart.css) — see ``enterCaseMode`` near the bottom of this file.
 *   c) The "Activate as this" pivot control rendered on every real row of
 *      a "label" card's own list (buildLabelRow) — clicking it re-anchors
 *      the whole chart on THAT row's own company, under the field it is
 *      currently listed under, replacing whichever anchor (if any) was
 *      active before. This is how a reader "walks the graph".
 *
 * Setting the anchor always re-runs the SAME visual query every Inquiry
 * already runs (runInquiryForClient) — there is no separate "activate"
 * step. WHILE AN ANCHOR IS ACTIVE, opening any OTHER "label" card (lit or
 * not) switches that card's own modal into "connect" mode instead of plain
 * browsing: every row shows whether the anchor is already connected to it
 * under THIS card's role (ticked) or not (unticked — see
 * renderAnchorConnectRows), a tick/untick stages a change, and this card's
 * own Confirm button (unchanged ids, repurposed — see ``confirmCardBtn``)
 * commits every staged tick as a create_connection/remove_connection call
 * anchored on the active anchor — never on any other card's own client, so
 * two non-anchor cards can never be connected to each other from here.
 * Opening the anchor's OWN field, or opening anything while no anchor is
 * set at all, stays plain browsing, unchanged.
 *
 * ``caseMeta`` is the one piece of the old "case mode" that is NOT folded
 * into ``activeAnchor`` itself: the case's own doc number/client name, kept
 * only for the banner's own text, so a later pivot (which moves the anchor
 * to some OTHER company) does not erase which case the reader is still
 * "inside" — the anchor's own ``caseId`` (and hence every write's own case
 * scoping via ``withCaseId``) carries forward across a pivot; ``caseMeta``
 * itself only ever changes on a fresh case-mode entry or a full
 * deactivation. See ``deactivateAnchor`` for the one shared teardown every
 * "clear" control (Deactivate, Leave case mode, Clear query) now goes
 * through, reusing ``clearQueryMarks()`` exactly as the query-clear control
 * always did.
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
    // keyboard support — the same tabIndex/keydown pattern this file's other
    // hand-built option rows already use for the identical reason.
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
  // The Quick Inquiry widget's Role field — a small parallel to
  // createCompanyPicker just above, built specifically for a LOCAL,
  // already-in-hand list rather than a server search: the Role field only
  // ever filters the currently-picked company's own current roles (an array
  // the Quick Inquiry block below already fetches via
  // CFG.clientConnectionsUrl), so there is nothing to debounce or fetch
  // here — every keystroke just re-filters ``items`` in place. Deliberately
  // mirrors createCompanyPicker's own markup/interaction (.rc-row/
  // .rc-row-name/.rc-row-empty rows, is-selected highlight, Enter/Space
  // keyboard activation) so the Role field reads and behaves identically to
  // the Company field beside it, per the owner's own request — just without
  // a "+ Add ..." row of its own, since "+ Add a new role" already exists as
  // its own separate, CAN_EDIT-gated affordance next to this field (see the
  // Quick Inquiry block below), not something this picker needs to offer a
  // second time.
  // ------------------------------------------------------------------------
  function createRolePicker(listEl, inputEl, onSelect) {
    var items = [];          // [{field, label_fa}, ...] — the current company's own roles
    var selectedField = null;

    function select(item) {
      selectedField = item ? item.field : null;
      onSelect(item);
    }

    function activateOnKey(row, activate) {
      row.tabIndex = 0;
      row.setAttribute('role', 'option');
      row.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); activate(); }
      });
    }

    function buildRow(item) {
      var row = document.createElement('div');
      row.className = 'rc-row';
      if (selectedField === item.field) { row.classList.add('is-selected'); }
      var name = document.createElement('span');
      name.className = 'rc-row-name';
      name.textContent = item.label_fa;
      name.setAttribute('dir', 'auto');
      row.appendChild(name);
      function activate() {
        select(item);
        inputEl.value = item.label_fa;
        Array.prototype.forEach.call(listEl.querySelectorAll('.rc-row'), function (r) {
          r.classList.remove('is-selected');
        });
        row.classList.add('is-selected');
        listEl.hidden = true;
      }
      row.addEventListener('click', activate);
      activateOnKey(row, activate);
      return row;
    }

    function render(query) {
      listEl.innerHTML = '';
      var q = (query || '').trim();
      var qLower = q.toLowerCase();
      var matches = !q ? items : items.filter(function (item) {
        return item.label_fa.toLowerCase().indexOf(qLower) !== -1;
      });
      matches.forEach(function (item) { listEl.appendChild(buildRow(item)); });
      if (!matches.length) {
        var empty = document.createElement('div');
        empty.className = 'rc-row-empty';
        empty.textContent = !items.length ? 'No roles yet.' : 'No roles match "' + q + '".';
        listEl.appendChild(empty);
      }
    }

    inputEl.addEventListener('input', function () {
      if (selectedField) { select(null); }
      render(inputEl.value);
      listEl.hidden = false;
    });

    return {
      // Replaces the filterable set outright (a freshly-picked company's own
      // roles) and drops whatever was selected before — the same "a new
      // company means a clean slate" rule the Quick Inquiry block already
      // enforced on the old native <select>.
      setItems: function (newItems) {
        items = newItems;
        selectedField = null;
      },
      render: render,
      // Programmatically selects the row for ``field`` (used right after a
      // brand-new role is added, so the reader lands on it — the same
      // "selectField" carve-out quickPopulateRoleField already had for the
      // old native <select>) without requiring a click/keydown of its own.
      selectByField: function (field) {
        var match = items.filter(function (item) { return item.field === field; })[0];
        select(match || null);
        inputEl.value = match ? match.label_fa : '';
      },
      reset: function () {
        inputEl.value = '';
        items = [];
        select(null);
        listEl.innerHTML = '';
      },
      selectedField: function () { return selectedField; }
    };
  }

  // ------------------------------------------------------------------------
  // The active anchor — one (company, role) pair the whole chart is pointed
  // at, set by setActiveAnchor() (the one mechanism behind all three ways to
  // set it — see the module-level comment at the top of this file) and shown
  // in this small persistent indicator bar, which survives the modal opening
  // and closing across as many cards as the user visits; only Deactivate (or
  // the query-clear control, or leaving case mode — all three now share one
  // teardown, deactivateAnchor()) ever clears it. Reuses the OLD attach
  // wizard's own fixed-position floating-bar markup/ids in the template
  // (rcWizardBar et al. → rcAnchorBar et al.) rather than a bespoke element.
  //
  // activeAnchor = {
  //   client: {id, name},  // the COMPANY every "connect" mode below anchors on
  //   field:  <label key>, // the ROLE it is currently anchored as — opening
  //                        // THIS field's own card stays in plain browsing
  //                        // mode (nothing to connect it to itself)
  //   caseId: <int|null>   // OPTIONAL — set by case mode, carried forward
  //                        // across a later pivot (change c) so a write made
  //                        // after "walking" away from the case's own real
  //                        // anchor is still recorded against that case —
  //                        // see withCaseId()/caseMeta below.
  // }
  // null when no anchor is active — the ordinary, unscoped browsing every
  // card already had before this round.
  // ------------------------------------------------------------------------
  var anchorBar = document.getElementById('rcAnchorBar');
  var anchorSourceEl = document.getElementById('rcAnchorSource');
  var anchorRoleEl = document.getElementById('rcAnchorRole');
  var anchorDeactivateBtn = document.getElementById('rcAnchorDeactivate');

  var activeAnchor = null;

  // The one piece of "case mode" that does NOT fold into activeAnchor
  // itself — the case's own doc number and its own real client's name, kept
  // purely for the banner's own text (updateCaseModeBanner, below) so a
  // later pivot (which moves activeAnchor.client to some OTHER company) does
  // not erase which case the reader is still "inside". Set only by
  // enterCaseMode; cleared only by deactivateAnchor() — never touched by a
  // pivot. See withCaseId(): the actual write-scoping uses
  // activeAnchor.caseId, not this.
  var caseMeta = null;

  function updateAnchorBar() {
    if (!anchorBar) { return; }
    if (!activeAnchor) { anchorBar.hidden = true; return; }
    anchorBar.hidden = false;
    anchorSourceEl.textContent = activeAnchor.client.name;
    var node = nodesByField[activeAnchor.field];
    anchorRoleEl.textContent = node ? node.getAttribute('data-role') : activeAnchor.field;
  }

  function updateCaseModeBanner() {
    if (!caseModeBanner) { return; }
    if (!caseMeta) { caseModeBanner.hidden = true; caseModeBannerText.textContent = ''; return; }
    caseModeBanner.hidden = false;
    caseModeBannerText.textContent = 'Editing chart for case ' + caseMeta.docNo + ' — ' + caseMeta.clientName;
  }

  // The ONE mechanism behind all three ways to set the active anchor (see
  // the module comment) — sets activeAnchor and re-runs the exact same
  // visual query runInquiryForClient() already runs for a plain Inquiry;
  // there is no separate "activate" step. ``opts.caseId`` sets the anchor's
  // own case scope explicitly (case mode's own entry); omitted, it carries
  // the PREVIOUS anchor's own caseId forward (a pivot away from a case's own
  // anchor stays scoped to that case — see caseMeta's own comment above)
  // rather than silently dropping it. ``opts.isCaseAnchor`` is passed
  // straight through to runInquiryForClient's own param of the same name —
  // only case mode's own entry ever sets it true.
  function setActiveAnchor(client, field, opts) {
    opts = opts || {};
    var caseId = Object.prototype.hasOwnProperty.call(opts, 'caseId')
      ? opts.caseId
      : (activeAnchor ? activeAnchor.caseId : null);
    activeAnchor = { client: { id: client.id, name: client.name }, field: field, caseId: caseId || null };
    updateAnchorBar();
    runInquiryForClient(client, field, !!opts.isCaseAnchor);
  }

  // The one shared teardown every "clear" control now goes through —
  // Deactivate (this bar's own button, below), "Leave case mode", and the
  // query pill's own Clear button (see the query overlay section next) —
  // rather than three separate mechanisms. Fully clears the anchor AND the
  // case-mode banner alongside the same visual state clearQueryMarks()
  // already tears down for an ordinary Clear, since the active anchor is
  // always exactly what the current query is showing.
  function deactivateAnchor() {
    activeAnchor = null;
    caseMeta = null;
    chartRoot.classList.remove('is-query');
    clearQueryMarks();
    queryPill.hidden = true;
    updateAnchorBar();
    updateCaseModeBanner();
    if (caseModeInput) { caseModeInput.value = ''; }
    if (caseModeList) { caseModeList.innerHTML = ''; }
  }

  if (anchorDeactivateBtn) { anchorDeactivateBtn.addEventListener('click', deactivateAnchor); }

  // Merges ``case_id: activeAnchor.caseId`` into ``params`` whenever the
  // active anchor carries one, leaving ``params`` untouched otherwise — the
  // one place every case-scoped read/write below reaches through, rather
  // than each call site re-checking activeAnchor on its own.
  function withCaseId(params) {
    if (activeAnchor && activeAnchor.caseId) { params.case_id = activeAnchor.caseId; }
    return params;
  }

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

  // The case-mode anchor's own extra mark on top of is-focus — see
  // runInquiryForClient's own ``isCaseAnchor`` branch, and rolechart.css's
  // ``.is-case-anchor`` rules for the actual look (a distinctly-coloured
  // ring/glow plus this small corner dot, never a per-field colour). Built
  // as real SVG child elements of the anchor node's own <g> — the same
  // "small clickable card is an SVG group" shape every other per-node
  // decoration on this chart already is (compare ``.rc-badge``/
  // ``.rc-badge-text``, drawn once in rolechart.py) — rather than an HTML
  // overlay like ``.rc-card-grow``, since this sits INSIDE the box itself at
  // a fixed corner, not stacked below it. ``caseAnchorField`` remembers which
  // node currently carries it so clearQueryMarks() can always remove it
  // again, whether or not that field is still lit at the time.
  var caseAnchorField = null;

  function addCaseAnchorBadge(field) {
    var rect = nodeRect(field);
    if (!rect) { return; }
    var ns = 'http://www.w3.org/2000/svg';
    var g = document.createElementNS(ns, 'g');
    g.setAttribute('class', 'rc-case-anchor-badge');
    g.setAttribute('aria-hidden', 'true');
    var cx = rect.x + 13, cy = rect.y + 13;
    var dot = document.createElementNS(ns, 'circle');
    dot.setAttribute('class', 'rc-case-anchor-dot');
    dot.setAttribute('cx', cx);
    dot.setAttribute('cy', cy);
    dot.setAttribute('r', 7);
    g.appendChild(dot);
    // A plain check-mark glyph, not a font character — reads cleanly at this
    // size in both themes with nothing more than the same currentColor-style
    // stroke rule .rc-case-anchor-glyph sets in CSS.
    var check = document.createElementNS(ns, 'path');
    check.setAttribute('class', 'rc-case-anchor-glyph');
    check.setAttribute('d',
      'M' + (cx - 3) + ' ' + cy + ' L' + (cx - 0.8) + ' ' + (cy + 2.6) + ' L' + (cx + 3.4) + ' ' + (cy - 2.8));
    g.appendChild(check);
    nodesByField[field].appendChild(g);
    caseAnchorField = field;
  }

  function removeCaseAnchorBadge() {
    if (!caseAnchorField) { return; }
    var g = nodesByField[caseAnchorField];
    var badge = g && g.querySelector('.rc-case-anchor-badge');
    if (badge && badge.parentNode) { badge.parentNode.removeChild(badge); }
    caseAnchorField = null;
  }

  // The last successful runInquiryForClient() answer, kept around only for
  // the duration of that query — {client:{id,name}, byField:{field:[doc_no,
  // ...]}, namesByField, labelsRaw}, one byField/namesByField entry per
  // lit/focus field ("us" included, keyed off the same doc-number list the
  // panel/annotation already use). openModalForField reads this to show a
  // lit/focus card's OWN filtered view mid-query instead of its normal full
  // list; ``labelsRaw`` (the server's own ``data.labels`` array, unmodified)
  // is what renderAnchorConnectRows reads to tick a card's real rows for
  // FREE when this happens to already be the active anchor's own client's
  // report — see connectedIdsForField(), which falls back to a fresh fetch
  // whenever this does not apply (a non-anchor Inquiry ran more recently and
  // overwrote it with some OTHER client's report). Cleared, like every other
  // query-mode visual, by clearQueryMarks().
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
      nodesByField[field].classList.remove('is-focus', 'is-lit', 'is-case-anchor');
    });
    restoreContextualBadges();
    lastQueryData = null;
    queryLinesG.innerHTML = '';
    hideUsCasesPanel();
    clearCardGrowth();
    removeCaseAnchorBadge();
  }

  // ------------------------------------------------------------------------
  // In-place card growth — while an Inquiry is active, every TOUCHED card
  // (the focus card AND every lit card it connects to) grows its own box
  // taller to show the queried company's own "connected" name(s) (possibly
  // several — see marketing/services.py::connections_of_client's own
  // ``connected`` field, and runInquiryForClient's own per-field
  // namesByField construction below) INSIDE the card itself, so it reads as
  // one taller card rather than a second floating box stacked underneath it
  // (an earlier round tried exactly that — a separate .rc-node-overlay div —
  // and the owner rejected it outright).
  //
  // Built as a plain HTML element (position:absolute inside #rcCanvas, the
  // same containing block #rcQueryPill/#rcUsCasesPanel already use) rather
  // than growing the SVG <rect> itself: the box's own text elements
  // (rc-role/rc-abbr/the badge) would all need repositioning too, and every
  // OTHER row's fixed Y coordinate comes straight from rolechart.py's own
  // pre-computed layout — reflowing that from here would mean duplicating
  // its geometry rather than just reading it. An HTML slab positioned flush
  // against the box's own bottom edge, sharing its exact left/right extent
  // and its own resolved fill/stroke (read live off the box via
  // getComputedStyle, not re-derived from rolechart.css's own state rules a
  // second time — see growCard below), reads as a seamless continuation of
  // the same card instead, with the card's existing Persian role title
  // staying exactly where it already sits, at the top of the (unchanged)
  // box above this.
  //
  // How tall it may grow is measured, not assumed: rolechart.py's row gap
  // (_GAP) was raised specifically to leave room for this, but this still
  // measures the REAL on-screen distance to the nearest card below it in the
  // same horizontal band before growing into it, and caps its own height
  // (with its own overflow-y:auto scroll) at whatever that leaves — see
  // growCard's own ``available`` computation — rather than trusting the
  // constant never to matter. Built fresh on every successful query and torn
  // down, all at once, by clearCardGrowth() (called from clearQueryMarks()
  // above); a field not currently touched is never grown at all.
  // ------------------------------------------------------------------------
  var cardGrowEls = {}; // field -> the growth element currently shown for it

  var CARD_GROW_MARGIN_PX = 6;   // breathing room kept above the next card down
  var CARD_GROW_FALLBACK_PX = 160; // used only when nothing sits below at all (e.g. the chart's own bottom row)
  var CARD_GROW_MAX_PX = 220;    // "a bit taller", not a card that dwarfs its row — see the module comment above

  // How much vertical room, in real on-screen pixels, exists below
  // ``boxScreen`` before the nearest OTHER card sharing its horizontal band
  // begins — measured directly off every other card's own current
  // getBoundingClientRect() rather than assumed from rolechart.py's _GAP, so
  // this stays correct however the layout is tuned later. "Sharing its
  // horizontal band" is a plain x-range overlap check: a card in a different
  // column two rows down is irrelevant, only the one(s) directly beneath
  // this card's own column(s) can ever be grown into.
  function availableGrowthPx(field, boxScreen) {
    var nextTop = Infinity;
    Object.keys(nodesByField).forEach(function (otherField) {
      if (otherField === field) { return; }
      var otherBox = nodesByField[otherField].querySelector('.rc-box');
      if (!otherBox) { return; }
      var r = otherBox.getBoundingClientRect();
      var overlapsX = r.left < boxScreen.right && r.right > boxScreen.left;
      if (overlapsX && r.top >= boxScreen.bottom - 1) {
        nextTop = Math.min(nextTop, r.top);
      }
    });
    var raw = nextTop === Infinity ? CARD_GROW_FALLBACK_PX : (nextTop - boxScreen.bottom - CARD_GROW_MARGIN_PX);
    return Math.max(0, Math.min(raw, CARD_GROW_MAX_PX));
  }

  function growCard(field, names) {
    var g = nodesByField[field];
    var boxEl = g && g.querySelector('.rc-box');
    if (!g || !boxEl || !rcCanvas || !names || !names.length) { return; }
    var boxScreen = boxEl.getBoundingClientRect();
    var canvasBox = rcCanvas.getBoundingClientRect();
    if (!boxScreen.width || !canvasBox.width) { return; }
    var available = availableGrowthPx(field, boxScreen);
    // No usable room at all (should not happen on this chart's own fixed,
    // now-generously-gapped layout, but nodeRect()-style helpers elsewhere in
    // this file stay defensive too) — leave the card at its normal size
    // rather than force a sliver nothing could actually read.
    if (available < 18) { return; }

    var el = document.createElement('div');
    el.className = 'rc-card-grow';
    var boxStyle = window.getComputedStyle(boxEl);
    var strokeW = parseFloat(boxStyle.strokeWidth) || 1.4;
    el.style.background = boxStyle.fill;
    el.style.borderColor = boxStyle.stroke;
    el.style.borderWidth = strokeW + 'px';
    el.style.maxHeight = available + 'px';
    names.forEach(function (name) {
      var row = document.createElement('div');
      row.className = 'rc-card-grow-name';
      row.textContent = name;
      row.setAttribute('dir', 'auto');
      el.appendChild(row);
    });
    rcCanvas.appendChild(el);
    el.style.left = (boxScreen.left - canvasBox.left) + 'px';
    el.style.width = boxScreen.width + 'px';
    // Pulled up by half the box's own stroke width so this slab's top edge
    // sits exactly where the box's stroke is centred, covering the box's own
    // bottom border rather than leaving it as a visible seam between "the
    // card" and "the growth" — the whole point is reading as one shape.
    el.style.top = (boxScreen.bottom - canvasBox.top - strokeW / 2) + 'px';
    cardGrowEls[field] = el;
  }

  function showCardGrowth(fields) {
    if (!lastQueryData || !rcCanvas) { return; }
    fields.forEach(function (field) {
      growCard(field, lastQueryData.namesByField[field]);
    });
  }

  function clearCardGrowth() {
    Object.keys(cardGrowEls).forEach(function (field) {
      var el = cardGrowEls[field];
      if (el && el.parentNode) { el.parentNode.removeChild(el); }
    });
    cardGrowEls = {};
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
  // case, and also what a connect-mode card's own Confirm runs once its
  // staged changes have landed, to refresh the chart's own highlighted state
  // (see confirmCardBtn's own listener).
  //
  // ``isCaseAnchor`` is optional, and true for exactly one caller: case
  // mode's own automatic Inquiry, run the moment a case is confirmed in
  // rcCaseModeWidget (see the case-mode wiring block below), on that case's
  // own real client under its own effective role — this marks the resulting
  // focus card with the extra .is-case-anchor treatment (see
  // rolechart.css) so it reads as "this case's own real anchor", not just
  // "whatever happens to be lit right now". Every other caller (Quick
  // Inquiry, a normal in-chart Inquiry, a connect-mode card's own post-Confirm
  // re-query, a pivot) leaves it undefined/false and gets the plain is-focus
  // look unchanged.
  //
  // The client_connections fetch itself always goes through withCaseId() —
  // regardless of ``isCaseAnchor`` — so ANY Inquiry run while case mode is
  // active (not only this one automatic call) picks up that case's own
  // case-scoped Connection rows, per the goal's own requirement.
  function runInquiryForClient(client, originField, isCaseAnchor) {
    get(CFG.clientConnectionsUrl, withCaseId({ client_id: client.id })).then(function (data) {
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
        if (focusG) {
          focusG.classList.add('is-focus');
          // See this function's own ``isCaseAnchor`` comment above — only
          // ever set by case mode's own automatic entry Inquiry.
          if (isCaseAnchor) {
            focusG.classList.add('is-case-anchor');
            addCaseAnchorBadge(focusField);
          }
        }
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
      // One NAME LIST per touched field — each label field's own real
      // ``connected`` array (marketing/services.py::connections_of_client),
      // the actual company/companies that should show under THAT card, which
      // is not always ``client`` itself any more (the owner's own bug report:
      // a company connected under a DIFFERENT company's role must show ITS
      // OWN name there, not the focused client's repeated everywhere) — see
      // growCard()/showCardGrowth() above for where this is read back out.
      // "us" is the one exception: it is never one of ``data.labels``'
      // entries (that array only ever holds the fourteen label keys), it is
      // pushed onto allFields separately above purely because ``client`` has
      // at least one case, and it has always shown ``client``'s own name —
      // unchanged here, on purpose.
      var namesByField = {};
      data.labels.forEach(function (l) {
        var names = (l.connected && l.connected.length) ? l.connected.map(function (c) { return c.name; }) : [client.name];
        namesByField[l.label] = names;
      });
      if (showUsPanel) { namesByField.us = [client.name]; }
      lastQueryData = {
        client: { id: client.id, name: client.name },
        byField: byField, namesByField: namesByField,
        labelsRaw: data.labels
      };
      window.requestAnimationFrame(function () {
        // Without a focus field there is no single anchor to route the
        // centre-lane line FROM (originField not among this client's own
        // touched fields at all — e.g. a "us"-origin Inquiry for a client
        // with no cases of its own), so we
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
        // Every touched field — focus and lit alike — grows in place;
        // allFields already IS that whole set (focusField plus litFields,
        // and "us" too when showUsPanel pushed it above).
        showCardGrowth(allFields);
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

  // The active anchor is always exactly what the current query is showing,
  // so clearing the query clears the anchor too — deactivateAnchor() does
  // both (and the case-mode banner) in one shared teardown.
  queryClearBtn.addEventListener('click', deactivateAnchor);

  // ------------------------------------------------------------------------
  // The one reusable modal. Three "kind"-driven content modes (label
  // default / us / inert) plus one "connect" mode a "label" card's own
  // modal switches into instead of plain browsing whenever an anchor is
  // active and this is not the anchor's own field — see
  // renderAnchorConnectRows further down. actionsPicking/confirmCardBtn/
  // closeCardBtn are the OLD attach wizard's own per-card action bar,
  // repurposed rather than rebuilt: confirmCardBtn now actually commits this
  // card's own staged connect/disconnect changes (see its own listener
  // below) instead of just closing the modal.
  // ------------------------------------------------------------------------
  var overlay = document.getElementById('rcModalOverlay');
  var modalTitle = document.getElementById('rcModalTitle');
  var closeX = document.getElementById('rcModalClose');
  var searchWrap = document.getElementById('rcModalSearchWrap');
  var searchInput = document.getElementById('rcModalSearch');
  var listEl = document.getElementById('rcModalList');
  var actionsDefault = document.getElementById('rcModalActionsDefault');
  var actionsPicking = document.getElementById('rcModalActionsPicking');
  var inquiryBtn = document.getElementById('rcModalInquiry');
  var closeDefaultBtn = document.getElementById('rcModalCloseBtn');
  var confirmCardBtn = document.getElementById('rcModalConfirmCard');
  var closeCardBtn = document.getElementById('rcModalCloseCard');

  // ------------------------------------------------------------------------
  // The "+ Add company" panel (change 2) — a reusable right-side sub-panel
  // available from EVERY label card's modal, in both default browsing and
  // "connect" mode alike. Built once here, moving the template's own
  // existing modal-body nodes (the search box, the company list, and both
  // action bars) into a new .rc-modal-columns/.rc-modal-main wrapper — see
  // rolechart.css — so this panel can sit BESIDE them as a second column
  // instead of only ever stacking underneath. The same "build once near the
  // top of the file, toggle hidden/visible on demand" convention as
  // #rcAnchorBar / the "us" cases panel above; opening it just widens the
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

  // Clears just the just-committed pick back to a fresh, empty search —
  // used after EVERY successful "Add" below instead of closeAddCompanyPanel,
  // so the panel itself stays open, at its current widened size, ready for
  // the reader to find or create the NEXT company in the same sitting (the
  // owner's own requirement: picking a result must not read as the panel
  // "suddenly closing"). Only the panel's own × button, Escape, or the whole
  // modal closing ever calls closeAddCompanyPanel now.
  function resetAddCompanyPickerForNextPick() {
    addCompanyPicker.reset();
    addCompanyConfirmBtn.disabled = true;
    addCompanyPicker.search('');
    addCompanySearchInput.focus();
  }

  // Commits the staged company to the CURRENTLY OPEN card's own field — used
  // to be identical in default and connect mode (both just meant "attach
  // this company to modalField" directly). Connect mode's own meaning is
  // different: the found/created company here plays modalField (B)'s role,
  // CONNECTED to the active anchor rather than tagged itself — the exact
  // same fact a tick on one of B's already-real rows stages (see
  // renderAnchorConnectRows's own connectCtx), just reached through
  // search-or-create instead of an existing row, so it lands in the SAME
  // anchorConnectState.staged bucket rather than committed here — this
  // card's own Confirm is what actually calls create_connection, same as
  // every other staged tick. Default browsing mode is untouched: it still
  // commits immediately, via a plain manual tag.
  //
  // Neither branch closes the panel any more (change 2) — refreshLabelList()
  // redraws the card's own main/left list (or, in connect mode, its ticked
  // connect-row view) to show the just-added company THERE, exactly as any
  // other staged/attached addition already would, while this panel stays
  // open beside it for another pick.
  addCompanyConfirmBtn.addEventListener('click', function () {
    var client = addCompanyPicker.selected();
    if (!client || modalKind !== 'label' || !modalField) { return; }
    var field = modalField;
    if (modalMode === 'connect' && anchorConnectState) {
      anchorConnectState.staged[client.id] = { name: client.name, add: true };
      resetAddCompanyPickerForNextPick();
      renderAnchorConnectListBody();
      return;
    }
    addCompanyConfirmBtn.disabled = true;
    post(CFG.labelToggleUrl, { client_id: client.id, label: field, add: 1 }).then(function (data) {
      if (!data.ok || modalField !== field) { addCompanyConfirmBtn.disabled = false; return; }
      resetAddCompanyPickerForNextPick();
      refreshLabelList();
    });
  });

  addCompanyBtn.addEventListener('click', openAddCompanyPanel);
  addCompanyCloseBtn.addEventListener('click', closeAddCompanyPanel);

  var modalField = null;
  var modalKind = null;            // 'label' | 'us' | 'inert'
  var modalMode = 'default';       // 'default' | 'connect'
  var selectedEntity = null;       // label default mode: at most one company
  var currentLabelCompanies = [];  // label default/connect mode: the full fetched list, for local search filtering
  // Present only while modalMode === 'connect' — the currently open card's
  // own connect-mode state. See renderAnchorConnectRows/renderAnchorConnectListBody.
  //
  // anchorConnectState = {
  //   field: <label key>,      // the card currently open (never the active
  //                            // anchor's own field — see openModalForField)
  //   anchor: activeAnchor,    // captured at open time so this card can
  //                            // always tell whether a pivot/deactivation
  //                            // elsewhere replaced the anchor while this
  //                            // modal stayed open, rather than trusting a
  //                            // stale module-level reference
  //   alreadyConnectedIds: {clientId: name, ...}, // the anchor's REAL
  //                            // current connections under ``field`` (from
  //                            // lastQueryData, or a fresh fetch — see
  //                            // connectedIdsForField), NAMED so a connected
  //                            // client B's own list never mentions (no
  //                            // ClientLabel of its own for ``field``) can
  //                            // still render its own "extra" row — see
  //                            // renderAnchorConnectListBody
  //   staged: {clientId: {name, add}, ...} // this card's own pending
  //                            // tick/untick changes, committed by this
  //                            // card's own Confirm (confirmCardBtn below)
  // }
  var anchorConnectState = null;
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
    anchorConnectState = null;
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
  // No Attach button any more — see setActiveAnchor()/the pivot control on
  // every row (buildLabelRow) for how the anchor is set now.
  function configureDefaultActions() {
    if (modalMode !== 'default') { return; }
    inquiryBtn.hidden = !(modalKind === 'label' || modalKind === 'us');
  }

  function updateActionState() {
    if (modalMode !== 'default') { return; }
    if (modalKind === 'label') {
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
    // Opening the active anchor's OWN field has nothing to connect it to —
    // stays a normal browsing reopen. Every OTHER "label" card switches to
    // "connect" mode while an anchor is active, lit or not.
    modalMode = (kind === 'label' && activeAnchor && field !== activeAnchor.field) ? 'connect' : 'default';
    selectedEntity = null;
    currentLabelCompanies = [];
    anchorConnectState = null;
    modalTitle.textContent = roleFa + ' · ' + abbr;
    searchInput.value = '';
    // The label card's own browsing/connect search stays exactly as it was;
    // an admin/GM's "us" card click gets the same search box, wired instead
    // to fetchAllCasesSearch (see below) rather than the local company
    // filter.
    searchWrap.hidden = !((modalMode === 'default' || modalMode === 'connect') &&
      (modalKind === 'label' || (modalKind === 'us' && CFG.isAdminTier)));
    actionsDefault.hidden = modalMode !== 'default';
    actionsPicking.hidden = modalMode !== 'connect';
    if (modalMode === 'connect') { confirmCardBtn.hidden = !CAN_EDIT; }
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
    // admin/GM): a card that is currently lit or focused by a query that did
    // NOT set the active anchor (Quick Inquiry, the admin "us" case list, the
    // ?case= deep link — none of those three set an anchor, see the module
    // comment) shows only what THAT query attached to it, not its normal
    // full list — see runInquiryForClient's own ``lastQueryData`` stash.
    // Skipped whenever "connect" mode applies instead (an anchor IS active
    // and this is not its own field) — connect mode always shows the card's
    // real, editable list, lit or not, per the owner's own requirement — and
    // also skipped for the active anchor's OWN field (modalMode is already
    // 'default' there, but is-query/is-focus are both still true — that
    // card must stay normal browsing too, not this read-out).
    if (modalMode === 'default' && modalKind === 'label' &&
        !(activeAnchor && field === activeAnchor.field) &&
        chartRoot.classList.contains('is-query') && lastQueryData) {
      var g = nodesByField[field];
      if (g && (g.classList.contains('is-lit') || g.classList.contains('is-focus'))) {
        searchWrap.hidden = true;
        // Re-running Inquiry from inside an already-active query's read-out
        // does not make sense — configureDefaultActions() just made it
        // visible for a 'label' kind card moments ago; this overrides that
        // specifically for the contextual case, leaving only the
        // always-present Close button (plus "+ Add company", which stays
        // available here on purpose — the owner explicitly wants to be able
        // to add a company to a card that is currently lit while an Inquiry
        // is active, not just when browsing normally; see
        // updateAddCompanyButtonVisibility() above for its own CAN_EDIT/kind
        // gate, unaffected by is-query). Restored on the next open that does
        // not hit this branch, since configureDefaultActions() runs fresh at
        // the top of every openModalForField() call.
        inquiryBtn.hidden = true;
        renderContextualQueryRow(field);
        return;
      }
    }

    if (modalMode === 'connect') {
      renderAnchorConnectRows(field);
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
  // showing — the normal default-mode fetch/render, or, while "connect" mode
  // is showing a DIFFERENT card, that mode's own fetch/render (preserving
  // whatever is already staged there — see renderAnchorConnectRows's own
  // ``preserveStaged`` param) — so a row action (the × on any row, or a
  // completed "+ Add company") always redraws into the view the viewer is
  // actually looking at instead of snapping back to default-mode browsing
  // regardless of what was open.
  function refreshLabelList() {
    if (modalMode === 'connect') {
      renderAnchorConnectRows(modalField, anchorConnectState ? anchorConnectState.staged : null);
    } else {
      fetchLabelCompanies();
    }
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
    } else if (modalKind === 'label' && modalMode === 'connect') {
      renderAnchorConnectListBody();
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

  // ``connectCtx`` — {checked, onToggle(nowChecked), readOnly} — is present
  // ONLY when this row is being rendered inside "connect" mode
  // (renderAnchorConnectRows below), turning the row's own click from
  // "select this company for Inquiry" (irrelevant there — connect mode
  // shows no Inquiry button, see configureDefaultActions) into "stage/
  // unstage a connection from the active anchor to THIS one, under the card
  // currently open". Rendered ON TOP of the row's existing name/×/pivot — a
  // small tick glyph (.mc-panel-check, the exact same glyph the synthesized
  // "connecting" row further down already uses) prepended to the row.
  // ``readOnly`` (set for a viewer who cannot edit) still shows the tick's
  // real current state but wires no click/keydown of its own — a view-only
  // viewer gets the truth, never a dead-looking but silently-inert control.
  function buildLabelRow(company, connectCtx) {
    var row = document.createElement('div');
    row.className = 'rc-row';
    if (!connectCtx && selectedEntity && selectedEntity.id === company.id) { row.classList.add('is-selected'); }

    var check = null;
    if (connectCtx) {
      check = document.createElement('span');
      check.className = 'mc-panel-check';
      check.setAttribute('aria-hidden', 'true');
      check.textContent = connectCtx.checked ? '✓' : '';
      row.appendChild(check);
      row.classList.toggle('is-checked', connectCtx.checked);
      if (!connectCtx.readOnly) {
        row.setAttribute('role', 'checkbox');
        row.setAttribute('aria-checked', connectCtx.checked ? 'true' : 'false');
      }
    }

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
    // point rendering a dead click. Unchanged whether this row is rendered by
    // the default browsing view OR by connect mode — buildLabelRow itself
    // has no notion of which mode called it; only the refresh below
    // (refreshLabelList) has to know, so the right view redraws afterward.
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

    // The pivot control — "Activate as this": re-anchors the WHOLE chart on
    // THIS row's own company, under the field it is currently listed under
    // (modalField), replacing whichever anchor (if any) was active before —
    // the exact same mechanism the Inquiry button/case mode use
    // (setActiveAnchor). Available on every real row regardless of whether
    // an anchor is active right now (this is how a reader "walks the graph")
    // and never CAN_EDIT-gated — it is a read, exactly like Inquiry itself,
    // never a write.
    //
    // NOT rendered for a "connection only" synthesized row (source === null,
    // set by renderAnchorConnectListBody's pseudo object below): that company
    // is connected to the CURRENT anchor under this field, but does not
    // itself directly hold this role (no ClientLabel, no case) —
    // connections_of_client only ever surfaces a connection whose anchor_role
    // is one the anchor's own client genuinely holds, so pivoting onto this
    // row would silently anchor the chart on a (client, role) pair with no
    // read path back to it: any connection confirmed afterward would write
    // successfully but never be visible again, from this card or any other.
    // Found and confirmed by an independent review after this file's own
    // first pass shipped; fixed by simply not offering the pivot here rather
    // than trying to redefine what "directly holds" means.
    if (company.source !== null) {
      var pivotBtn = document.createElement('button');
      pivotBtn.type = 'button';
      pivotBtn.className = 'rc-row-pivot';
      pivotBtn.title = 'Activate as this';
      pivotBtn.setAttribute('aria-label', 'Activate ' + company.name + ' as this card’s anchor');
      pivotBtn.textContent = '→';
      pivotBtn.addEventListener('click', function (ev) {
        ev.stopPropagation();
        var field = modalField;
        closeModal();
        setActiveAnchor(company, field, {});
      });
      row.appendChild(pivotBtn);
    }

    if (connectCtx && !connectCtx.readOnly) {
      // Toggles this row's own connection-target tick — nothing sent to the
      // server here; onToggle just stages/unstages the change on this card's
      // own anchorConnectState, committed only once this card's own Confirm
      // is pressed (confirmCardBtn below), same as the synthesized
      // "connecting" row's own toggle further down.
      var toggleConnect = function () {
        connectCtx.checked = !connectCtx.checked;
        row.classList.toggle('is-checked', connectCtx.checked);
        row.setAttribute('aria-checked', connectCtx.checked ? 'true' : 'false');
        check.textContent = connectCtx.checked ? '✓' : '';
        connectCtx.onToggle(connectCtx.checked);
      };
      row.tabIndex = 0;
      row.addEventListener('click', function (ev) {
        if (ev.target.closest && (ev.target.closest('.rc-row-remove') || ev.target.closest('.rc-row-pivot'))) { return; }
        toggleConnect();
      });
      row.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); toggleConnect(); }
      });
    } else if (!connectCtx) {
      // Selecting the row (its name, not the ×/pivot controls) is what
      // enables Inquiry below — same as the old default-mode selection.
      row.addEventListener('click', function (ev) {
        if (ev.target.closest && (ev.target.closest('.rc-row-remove') || ev.target.closest('.rc-row-pivot'))) { return; }
        selectedEntity = company;
        Array.prototype.forEach.call(listEl.querySelectorAll('.rc-row'), function (r) {
          r.classList.remove('is-selected');
        });
        row.classList.add('is-selected');
        updateActionState();
      });
    }
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

  // ---- "connect" mode: opening ANOTHER label card while an anchor is active
  // B's own REAL, FULL existing company list — fetched via
  // CFG.labelCompaniesUrl the same way fetchLabelCompanies()/buildLabelRow()
  // fetch and render it for normal browsing — is where each real row's
  // INITIAL tick state comes from: a row here starts checked when the active
  // anchor is already connected to that company under B, so re-opening a
  // card mid-session, or days later, shows the truth rather than always
  // starting blank. The connected-ids half of that comes from
  // connectedIdsForField() below — the anchor's own client_connections
  // report, reused from lastQueryData when it is still fresh (the common
  // case: setActiveAnchor() just fetched it), or fetched directly otherwise.
  // Every row B genuinely carries renders through that SAME buildLabelRow()
  // the default browsing view uses, now passed a connectCtx: selectable as a
  // connection TARGET, and still removable via its own × whenever this
  // viewer owns/can-remove that tag, exactly as a normal (non-anchor) click
  // on this card would show it. One more thing layers on top of that real
  // list: one synthesized row per staged connection target that is NOT
  // itself a genuine member of B (reached only through the "+ Add company"
  // panel's own connect-mode branch, since every OTHER way of staging a
  // change starts from a row already in ``companies``).
  //
  // ``preserveStaged`` — an existing anchorConnectState.staged object to
  // carry forward into the freshly-rendered state instead of starting empty
  // — used by refreshLabelList() after a row's own × (a plain self-tag
  // removal, unrelated to the anchor) so an in-progress, not-yet-confirmed
  // set of ticks on THIS card is never silently dropped by that refresh.
  function renderAnchorConnectRows(field, preserveStaged) {
    var seq = modalSeq;
    var anchor = activeAnchor;
    get(CFG.labelCompaniesUrl, { label: field }).then(function (data) {
      if (seq !== modalSeq || !data.ok || activeAnchor !== anchor) { return; }
      var companies = data.companies || [];
      var cachedIds = connectedIdsForField(anchor, field);
      if (cachedIds) {
        startAnchorConnectState(field, anchor, companies, cachedIds, preserveStaged);
        return;
      }
      // lastQueryData belongs to a different client than the anchor's own
      // (e.g. Quick Inquiry or the admin "us" case list ran more recently,
      // after the anchor was set) — fall back to a direct fetch of the
      // anchor's own report, the same client_connections call
      // setActiveAnchor()'s own runInquiryForClient already makes, so this
      // card's starting ticks are still correct.
      get(CFG.clientConnectionsUrl, withCaseId({ client_id: anchor.client.id })).then(function (connData) {
        if (seq !== modalSeq || activeAnchor !== anchor) { return; }
        var ids = {};
        if (connData && connData.ok) {
          var entry = null;
          (connData.labels || []).forEach(function (l) { if (l.label === field) { entry = l; } });
          if (entry) {
            (entry.connected || []).forEach(function (c) { if (c.id !== anchor.client.id) { ids[c.id] = c.name; } });
          }
        }
        startAnchorConnectState(field, anchor, companies, ids, preserveStaged);
      });
    });
  }

  // The anchor's own current connections under ``field`` — {clientId: name}
  // (the NAME, not just a bare ``true`` — a connection can point at a client
  // that carries no ClientLabel of its own for ``field`` at all, so B's own
  // real company list, below, may never mention it; renderAnchorConnectListBody's
  // own "extra rows" block reads the name straight off this map to still show
  // and tick that row, rather than being unable to render it at all — see
  // that block's own comment) — read straight off lastQueryData (the last
  // runInquiryForClient answer) WHEN that answer is still the anchor's own
  // client's own report; returns null (not an empty object — a real "nothing
  // found" answer IS a valid, truthy {}) to signal "stale, go fetch fresh"
  // whenever it belongs to some OTHER client instead.
  function connectedIdsForField(anchor, field) {
    if (!lastQueryData || lastQueryData.client.id !== anchor.client.id) { return null; }
    var entry = null;
    (lastQueryData.labelsRaw || []).forEach(function (l) { if (l.label === field) { entry = l; } });
    var ids = {};
    if (entry) {
      (entry.connected || []).forEach(function (c) { if (c.id !== anchor.client.id) { ids[c.id] = c.name; } });
    }
    return ids;
  }

  function startAnchorConnectState(field, anchor, companies, alreadyConnectedIds, preserveStaged) {
    currentLabelCompanies = companies;
    anchorConnectState = { field: field, anchor: anchor, alreadyConnectedIds: alreadyConnectedIds, staged: preserveStaged || {} };
    renderAnchorConnectListBody();
  }

  // Renders the currently-open connect-mode card's own list from
  // anchorConnectState — called on every open, on every search keystroke
  // (searchInput's own listener above), and after every local staging change
  // (a tick/untick, a "+ Add company" pick) — never itself re-fetches
  // anything, so a tick never has to wait on the network to show.
  function renderAnchorConnectListBody() {
    var st = anchorConnectState;
    if (!st) { return; }
    listEl.innerHTML = '';
    var companies = filterCompanies(searchInput.value);
    var realIds = {};
    currentLabelCompanies.forEach(function (c) { realIds[c.id] = true; });
    var shown = 0;

    companies.forEach(function (company) {
      // The active anchor can never connect to itself.
      if (company.id === st.anchor.client.id) { return; }
      shown += 1;
      var checked = Object.prototype.hasOwnProperty.call(st.staged, company.id)
        ? st.staged[company.id].add
        : !!st.alreadyConnectedIds[company.id];
      var connectCtx = {
        checked: checked,
        readOnly: !CAN_EDIT,
        onToggle: function (nowChecked) {
          if (anchorConnectState !== st) { return; } // a different card's connect state took over while this row's modal stayed open
          var initial = !!st.alreadyConnectedIds[company.id];
          // "Back to matching the anchor's own current state" is not a real
          // change — dropped rather than kept as a no-op stage, same rule
          // the old attach wizard already enforced for its own staged edits.
          if (nowChecked === initial) { delete st.staged[company.id]; }
          else { st.staged[company.id] = { name: company.name, add: nowChecked }; }
          updateAnchorConnectConfirmState();
        }
      };
      listEl.appendChild(buildLabelRow(company, connectCtx));
    });

    // Extra rows: one per id that is genuinely CONNECTED to the anchor under
    // THIS field (``st.alreadyConnectedIds``), or freshly staged as a new
    // connection this session via the "+ Add company" panel's own
    // connect-mode branch (addCompanyConfirmBtn below), that is NOT itself a
    // genuine member of B's own real company list (``realIds``) — a
    // Connection can point at a client that carries no ClientLabel of its
    // own for ``field`` at all (see the Connection model's own docstring),
    // so B's plain directory (companies_for_label, behind ``companies``
    // above) never mentions it even though the anchor really is connected to
    // it — the reader must still see that fact, ticked, and be able to
    // untick it, exactly like any other row here. Reuses buildLabelRow/
    // connectCtx exactly as the real rows above do — never its own bespoke
    // row shape — fed a synthesized company-shaped object with no manual tag
    // of its own here (so it never grows a "×"; canRemove reads false for
    // it), plus a small badge marking it as connection-only so it reads
    // differently from a row that also carries its own real tag.
    var extraIds = {};
    Object.keys(st.alreadyConnectedIds).forEach(function (id) { if (!realIds[id]) { extraIds[id] = true; } });
    Object.keys(st.staged).forEach(function (id) { if (!realIds[id]) { extraIds[id] = true; } });
    var q = (searchInput.value || '').trim().toLowerCase();
    Object.keys(extraIds).forEach(function (id) {
      var staged = st.staged[id];
      var name = staged ? staged.name : st.alreadyConnectedIds[id];
      if (!name || (q && name.toLowerCase().indexOf(q) === -1)) { return; }
      shown += 1;
      var checked = staged ? staged.add : true;
      var pseudo = { id: Number(id), name: name, source: null, also_manual: false, removable: false };
      var connectCtx = {
        checked: checked,
        readOnly: !CAN_EDIT,
        onToggle: function (nowChecked) {
          if (anchorConnectState !== st) { return; } // a different card's connect state took over while this row's modal stayed open
          var initial = Object.prototype.hasOwnProperty.call(st.alreadyConnectedIds, id);
          // Same "back to matching the anchor's own current state is not a
          // real change" rule the real rows' own onToggle enforces above.
          if (nowChecked === initial) { delete st.staged[id]; }
          else { st.staged[id] = { name: name, add: nowChecked }; }
          updateAnchorConnectConfirmState();
        }
      };
      var extraRow = buildLabelRow(pseudo, connectCtx);
      var tag = document.createElement('span');
      tag.className = 'rc-row-badge';
      tag.textContent = 'connection only';
      // pseudo.source is null, so buildLabelRow never gives this row a pivot
      // control (see its own comment) — nothing trails the name any more,
      // so a plain append is the row's true last child, unlike a real row
      // where this badge would need to land before a pivot arrow.
      extraRow.appendChild(tag);
      listEl.appendChild(extraRow);
    });

    if (!shown) {
      var empty = document.createElement('div');
      empty.className = 'rc-row-empty';
      empty.textContent = 'No companies tagged yet.';
      listEl.appendChild(empty);
    }
    updateAnchorConnectConfirmState();
  }

  function updateAnchorConnectConfirmState() {
    if (!confirmCardBtn) { return; }
    confirmCardBtn.disabled = !CAN_EDIT || !anchorConnectState || !Object.keys(anchorConnectState.staged).length;
  }

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
    // Inquiry and "set the active anchor" are the exact same action now —
    // see setActiveAnchor()'s own comment.
    setActiveAnchor(client, originField, {});
  });

  // Commits every staged tick/untick on THIS card as a create_connection/
  // remove_connection call, anchored on the ACTIVE ANCHOR throughout — never
  // on any other card's own client, so two non-anchor cards can never be
  // connected to each other from here (the owner's own explicit
  // constraint). One fetch per staged change, run in sequence so a slow
  // request never races the next one for the same company. The anchor's own
  // case id is snapshotted into ``caseId`` up front (NOT read live via
  // withCaseId() inside the chain) so that even if a pivot fires from one of
  // this very card's own rows while an earlier request in the chain is still
  // in flight — the modal stays open, and its rows stay clickable, until
  // every request has landed — every write in THIS chain still lands against
  // the anchor it was actually staged against.
  confirmCardBtn.addEventListener('click', function () {
    if (modalMode !== 'connect' || !anchorConnectState || !CAN_EDIT) { closeModal(); return; }
    var st = anchorConnectState;
    var anchor = st.anchor;
    var field = st.field;
    var caseId = anchor.caseId || null;
    var ids = Object.keys(st.staged);
    if (!ids.length) { closeModal(); return; }
    confirmCardBtn.disabled = true;
    var chain = Promise.resolve();
    ids.forEach(function (targetId) {
      var info = st.staged[targetId];
      chain = chain.then(function () {
        var params = {
          anchor_client_id: anchor.client.id, anchor_role: anchor.field,
          target_client_id: targetId, target_role: field,
          add: info.add ? 1 : 0
        };
        if (caseId) { params.case_id = caseId; }
        return post(CFG.connectionToggleUrl, params);
      });
    });
    chain.then(function () {
      closeModal();
      // Re-run the anchor's own query so the chart's own highlighted/lit
      // state and every card's own growth reflect what was just committed —
      // skipped if a pivot (or Deactivate) already replaced/cleared the
      // anchor while these writes were in flight.
      if (activeAnchor === anchor) { runInquiryForClient(anchor.client, anchor.field); }
    });
  });

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
    var quickRoleInput = document.getElementById('rcQuickRoleInput');
    var quickRoleList = document.getElementById('rcQuickRoleList');
    var quickAddRoleToggle = document.getElementById('rcQuickAddRoleToggle');
    var quickNewRoleWrap = document.getElementById('rcQuickNewRoleWrap');
    var quickNewRoleSelect = document.getElementById('rcQuickNewRoleSelect');
    var quickNewRoleAdd = document.getElementById('rcQuickNewRoleAdd');
    var quickConfirmBtn = document.getElementById('rcQuickConfirm');

    if (quickCompanyInput && quickCompanyList && quickRoleInput && quickRoleList && quickAddRoleToggle &&
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
      function quickShowRoleDropdown(show) {
        if (quickRoleInput.disabled) { return; }
        quickRoleList.hidden = !show;
      }

      function quickUpdateConfirmState() {
        quickConfirmBtn.disabled = !quickCompany || !quickRolePicker.selectedField();
      }

      function quickResetRoleField(placeholderText) {
        quickRolePicker.reset();
        quickRoleInput.placeholder = placeholderText;
        quickRoleInput.disabled = true;
        quickShowRoleDropdown(false);
        quickAddRoleToggle.hidden = true;
        quickNewRoleWrap.hidden = true;
        quickUpdateConfirmState();
      }

      // Loads quickRoles (the company's own current labels) into the role
      // picker, optionally leaving a specific field pre-selected — used
      // right after a brand-new role is added, so the reader lands on it
      // rather than back on an empty field.
      function quickPopulateRoleField(selectField) {
        var items = quickRoles.map(function (l) { return { field: l.label, label_fa: l.label_fa }; });
        quickRolePicker.setItems(items);
        quickRoleInput.value = '';
        quickRoleInput.disabled = !quickRoles.length;
        quickRoleInput.placeholder = quickRoles.length ? 'Choose a role…' : 'No roles yet';
        quickRolePicker.render('');
        if (selectField) { quickRolePicker.selectByField(selectField); }
        quickShowRoleDropdown(false);
        // Adding a role from here is the same mutation the "+ Add company"
        // panel already makes elsewhere on the chart (CFG.labelToggleUrl) —
        // gated the same way, on CAN_EDIT, everywhere else in this file
        // already gates a write.
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

      // The Role field's own picker — see createRolePicker's own comment
      // further up this file for why it is not just a second
      // createCompanyPicker instance (this one filters a local array, never
      // fetches). onSelect only ever needs to keep the Confirm button's
      // enabled state current — the picked field itself is read straight
      // off quickRolePicker.selectedField() wherever it's needed below.
      var quickRolePicker = createRolePicker(quickRoleList, quickRoleInput, function () {
        quickUpdateConfirmState();
      });

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

      quickRoleInput.addEventListener('focus', function () {
        if (quickRoleInput.disabled) { return; }
        quickShowRoleDropdown(true);
        if (!quickRoleList.childElementCount) { quickRolePicker.render(quickRoleInput.value); }
      });
      // Closes either dropdown on any click outside the whole card — picking
      // a row already closes its own (quickShowDropdown/quickShowRoleDropdown
      // above), so this is only for "the reader clicked away without
      // choosing".
      document.addEventListener('click', function (ev) {
        if (!quickPanel.contains(ev.target)) {
          quickShowDropdown(false);
          quickShowRoleDropdown(false);
        }
      });

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
      // every other flow in this file already calls (the "+ Add company"
      // panel, a label row's own ×) — nothing new added to
      // marketing/views.py for this.
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
        var field = quickRolePicker.selectedField();
        if (!quickCompany || !field) { return; }
        runInquiryForClient(quickCompany, field);
        chartRoot.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });

      quickResetRoleField('Pick a company first…');
    }
  }

  // ------------------------------------------------------------------------
  // "Case mode" — home.html's own second small header card, rcCaseModeWidget,
  // sitting beside Quick Inquiry above. One field: search cases (doc no. or
  // client name) via CFG.allCasesSearchUrl — the exact same endpoint and row
  // shape the admin/GM "us" card search already established (renderAllCasesRows
  // above), reused here rather than a second endpoint (see
  // marketing:all_cases_search's own docstring for why an ordinary
  // Marketing Expert/Supervisor may call it now too). Picking a row IS the
  // confirm — there is no second field/button to press, unlike Quick
  // Inquiry's own company-then-role shape — because a case, unlike a
  // company, has exactly one thing case mode ever does with it.
  //
  // Same self-guard convention as the Quick Inquiry block above: every
  // element looked up independently, and this whole block does nothing when
  // any is missing (e.g. the Companies tab, where none of this is rendered).
  // ------------------------------------------------------------------------
  var caseModeWidget = document.getElementById('rcCaseModeWidget');
  var caseModeInput = document.getElementById('rcCaseModeInput');
  var caseModeList = document.getElementById('rcCaseModeList');
  var caseModeBanner = document.getElementById('rcCaseModeBanner');
  var caseModeBannerText = document.getElementById('rcCaseModeBannerText');
  var caseModeLeaveBtn = document.getElementById('rcCaseModeLeaveBtn');

  if (caseModeWidget && caseModeInput && caseModeList && caseModeBanner &&
      caseModeBannerText && caseModeLeaveBtn) {

    var caseModeSeq = 0;
    var caseModeDebounce = null;

    function caseModeShowDropdown(show) { caseModeList.hidden = !show; }

    function buildCaseModeRow(c) {
      var row = document.createElement('div');
      row.className = 'rc-row';
      // Doc number ONLY — no " — <client_name>" suffix (the owner's own
      // complaint: that combination wrapped awkwardly in this narrow
      // floating dropdown). The client's name is still shown once the case
      // is picked, in the case-mode banner (updateCaseModeBannerText,
      // above) — nothing is lost, just not duplicated here too.
      var name = document.createElement('span');
      name.className = 'rc-row-name';
      name.textContent = c.doc_no;
      name.setAttribute('dir', 'auto');
      row.appendChild(name);
      if (c.label_fa) {
        var badge = document.createElement('span');
        badge.className = 'rc-row-badge';
        badge.textContent = c.label_fa;
        badge.setAttribute('dir', 'auto');
        row.appendChild(badge);
      }
      function activate() { caseModeShowDropdown(false); enterCaseMode(c); }
      row.addEventListener('click', activate);
      row.tabIndex = 0;
      row.setAttribute('role', 'option');
      row.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); activate(); }
      });
      return row;
    }

    function renderCaseModeRows(cases, query) {
      caseModeList.innerHTML = '';
      cases.forEach(function (c) { caseModeList.appendChild(buildCaseModeRow(c)); });
      if (!cases.length) {
        var empty = document.createElement('div');
        empty.className = 'rc-row-empty';
        var q = (query || '').trim();
        empty.textContent = q ? 'No cases match "' + q + '".' : 'No cases yet.';
        caseModeList.appendChild(empty);
      }
    }

    function caseModeSearch(query) {
      var mySeq = ++caseModeSeq;
      get(CFG.allCasesSearchUrl, { q: query || '' }).then(function (data) {
        if (mySeq !== caseModeSeq || !data.ok) { return; }
        renderCaseModeRows(data.cases, query);
      });
    }

    // Deliberately ALWAYS re-fetches on focus, unlike createCompanyPicker's
    // own focus handler (which skips the fetch when a result list is
    // already showing) — a stale row here is not just cosmetic: clicking one
    // re-anchors the whole chart on whatever ``label`` it carries, and that
    // has to be this case's CURRENT effective role, not whatever it was the
    // last time this dropdown happened to be open (see the goal's own
    // re-anchoring requirement — no cached effective-role value may survive
    // a fresh case-mode entry).
    caseModeInput.addEventListener('focus', function () {
      caseModeShowDropdown(true);
      caseModeSearch(caseModeInput.value);
    });
    caseModeInput.addEventListener('input', function () {
      caseModeShowDropdown(true);
      var q = caseModeInput.value;
      window.clearTimeout(caseModeDebounce);
      caseModeDebounce = window.setTimeout(function () { caseModeSearch(q); }, US_SEARCH_DEBOUNCE_MS);
    });
    document.addEventListener('click', function (ev) {
      if (!caseModeWidget.contains(ev.target)) { caseModeShowDropdown(false); }
    });

    // Confirming a row: sets caseMeta (the banner's own text — see its own
    // comment near the top of this file) and calls setActiveAnchor() with an
    // explicit caseId, the SAME mechanism a plain Inquiry uses — for THIS
    // case's own real client, under whatever role ``c.label``
    // (search_all_cases' own ``_effective_label`` read) says it currently
    // plays — marked as this case's own anchor (isCaseAnchor: true) rather
    // than a plain focus card. ``c`` is exactly the row the fresh
    // caseModeSearch() fetch above just handed in — never a value held over
    // from an earlier open — so re-picking the same case after its
    // marketing_label changed elsewhere always re-anchors on the NEW role.
    function enterCaseMode(c) {
      caseMeta = { caseId: c.case_id, docNo: c.doc_no, clientName: c.client_name };
      setActiveAnchor({ id: c.client_id, name: c.client_name }, c.label, { caseId: c.case_id, isCaseAnchor: true });
      updateCaseModeBanner();
      caseModeInput.value = c.doc_no;
      caseModeList.innerHTML = '';
      chartRoot.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    // "Leave case mode" is now just the shared anchor teardown — fully
    // clears the anchor, the banner, and every query-mode visual, the same
    // as Deactivate/Clear query — rather than only forgetting the case while
    // leaving the chart's own highlighted state sitting there stale.
    caseModeLeaveBtn.addEventListener('click', deactivateAnchor);
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
  // ever opens for this path. allCasesSearchUrl?case_id= now answers any
  // viewer who can open this page at all (see marketing/views.py's
  // all_cases_search — widened from admin/GM-only alongside this round's own
  // case-mode search widget); a stale/deleted case id still comes back with
  // zero cases either way — do nothing, no error shown, since ``get()``
  // resolves on any JSON body regardless of status code.
  if (typeof CFG.deepLinkCaseId === 'number') {
    get(CFG.allCasesSearchUrl, { case_id: CFG.deepLinkCaseId }).then(function (data) {
      if (!data || !data.ok || !data.cases || data.cases.length !== 1) { return; }
      var c = data.cases[0];
      runInquiryForClient({ id: c.client_id, name: c.client_name }, 'us');
    }).catch(function () {});
  }
})();
