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
 *   b) "Case mode" (rcCaseModeWidget plus the shared #rcModeBanner, both in
 *      home.html) — entering
 *      it sets the anchor to the case's own real client, playing whatever
 *      role its ``marketing_label`` currently resolves to, WITH a case id
 *      attached (see ``caseMeta``/``withCaseId`` below) so every connection
 *      made while it is active is recorded as belonging to that case — see
 *      ``enterCaseMode`` near the bottom of this file.
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
  // re-implementing its own search/create/debounce logic. Renders
  // CFG.clientSearchUrl's row shape, and suppresses the "+ Add ..." create
  // row whenever an exact-name match is already among the results — both
  // call sites end up with visually identical rows
  // because both go through buildRow/buildCreateRow here, never their own.
  //
  // Deliberately dumb about WHEN a search fires or WHERE the results are
  // shown — search(query) is exposed for a caller to invoke on whatever
  // trigger fits its own UI (a panel opening, an input gaining focus,
  // etc.); the debounced live-as-you-type search on the input itself is the
  // one behaviour every caller wants unconditionally, so that alone is
  // wired here.
  //
  // ``opts.multi`` turns the SAME picker into a MULTI-SELECT one: rows become
  // tick rows (the .mc-panel-check glyph plus .rc-row.is-checked — the exact
  // tick-row language this file's own "connect" mode already uses, not a
  // second one), a click toggles instead of replacing, and the picked set
  // survives both a new search and a "+ Add" create, so a reader can tick one
  // company, search for another, tick that too, and press Add once. Only the
  // "+ Add company" panel asks for it (the owner's own request: ticking
  // several and adding them in one go); the Quick Inquiry company field stays
  // single-select, where "at most one company" is the whole point.
  // ------------------------------------------------------------------------
  function createCompanyPicker(listEl, inputEl, onSelect, opts) {
    var multi = !!(opts && opts.multi);
    var selected = null;
    // multi mode only — {clientId: {id, name, code}}, insertion order is what
    // Add commits in. Kept across searches on purpose (see above).
    var picked = {};
    var seq = 0;
    var debounce = null;

    function pickedList() {
      return Object.keys(picked).map(function (id) { return picked[id]; });
    }

    // Single mode reports the one client (or null); multi mode reports the
    // whole ticked list, so one ``onSelect`` contract covers both and the
    // caller's own "is the Add button live?" check is the same shape either
    // way.
    function notify() {
      onSelect(multi ? pickedList() : selected);
    }

    function select(client) {
      selected = client;
      notify();
    }

    function toggle(client, nowChecked) {
      if (nowChecked) { picked[client.id] = { id: client.id, name: client.name, code: client.code }; }
      else { delete picked[client.id]; }
      notify();
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
      var checked = multi
        ? Object.prototype.hasOwnProperty.call(picked, company.id)
        : !!(selected && selected.id === company.id);
      var check = null;
      if (multi) {
        // The same tick glyph + is-checked tint "connect" mode's own rows use
        // (buildLabelRow's connectCtx branch, further down this file) — one
        // tick-row language on this chart, not two.
        check = document.createElement('span');
        check.className = 'mc-panel-check';
        check.setAttribute('aria-hidden', 'true');
        check.textContent = checked ? '✓' : '';
        row.appendChild(check);
        row.classList.toggle('is-checked', checked);
        row.setAttribute('role', 'checkbox');
        row.setAttribute('aria-checked', checked ? 'true' : 'false');
      } else if (checked) {
        row.classList.add('is-selected');
      }
      var name = document.createElement('span');
      name.className = 'rc-row-name';
      name.textContent = company.name;
      name.setAttribute('dir', 'auto');
      row.appendChild(name);
      function activate() {
        if (multi) {
          var nowChecked = !row.classList.contains('is-checked');
          row.classList.toggle('is-checked', nowChecked);
          row.setAttribute('aria-checked', nowChecked ? 'true' : 'false');
          check.textContent = nowChecked ? '✓' : '';
          toggle({ id: company.id, name: company.name, code: company.code }, nowChecked);
          return;
        }
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
          // among the real results too. In multi mode it joins the ticked set
          // instead of replacing it, so a just-created company can be added
          // alongside the ones already ticked in one press of Add.
          var created = { id: data.client.id, name: data.client.name, code: data.client.code };
          if (multi) { toggle(created, true); } else { select(created); }
          inputEl.value = '';
          search('');
        });
      }
      row.addEventListener('click', activate);
      activateOnKey(row, activate);
      return row;
    }

    inputEl.addEventListener('input', function () {
      var q = inputEl.value;
      // Typing drops the single-mode pick (it no longer matches what is being
      // searched for) but NEVER the multi-mode ticked set — searching for the
      // second company to tick must not silently untick the first.
      if (!multi && selected) { select(null); }
      // Same debounce timing as this file's own admin "us" search — reused
      // here rather than a second value (US_SEARCH_DEBOUNCE_MS is declared
      // further down this file, but
      // already has its value by the time this callback ever actually runs).
      window.clearTimeout(debounce);
      debounce = window.setTimeout(function () { search(q); }, US_SEARCH_DEBOUNCE_MS);
    });

    return {
      search: search,
      reset: function () {
        inputEl.value = '';
        picked = {};
        if (multi) { notify(); } else if (selected) { select(null); }
        listEl.innerHTML = '';
        window.clearTimeout(debounce);
      },
      selected: function () { return selected; },
      // Multi mode only — every currently ticked company, in the order they
      // were ticked. Empty (never null) when nothing is ticked.
      selectedMany: function () { return pickedList(); }
    };
  }

  // ------------------------------------------------------------------------
  // The Quick Inquiry widget's Role field(s) — a small parallel to
  // createCompanyPicker just above, built specifically for a LOCAL,
  // already-in-hand list rather than a server search: a Role field only
  // ever filters some already-fetched array of {field, label_fa} roles, so
  // there is nothing to debounce or fetch here — every keystroke just
  // re-filters ``items`` in place. Deliberately mirrors createCompanyPicker's
  // own markup/interaction (.rc-row/.rc-row-name/.rc-row-empty rows,
  // is-selected highlight, Enter/Space keyboard activation) so every Role
  // field reads and behaves identically to the Company field beside it, per
  // the owner's own request.
  //
  // TWO INSTANCES share this one component, not two copies of it — the
  // Quick Inquiry block below builds one against the company's own CURRENT
  // roles (quickRolePicker, feeding the Confirm button) and a second against
  // the roles that company does NOT yet hold (quickNewRolePicker, feeding
  // "+ Add a new role"'s own Add button) — the same "filter a local array,
  // report a selection" job against two different arrays, so neither needs
  // a "+ Add ..." create row of its own the way createCompanyPicker's does.
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
  // in the chart's ONE persistent banner (#rcModeBanner, home.html — the same
  // banner case mode uses, see updateModeBanner below), which survives the
  // modal opening and closing across as many cards as the user visits; only
  // Deactivate (or the query-clear control, or leaving case mode — all three
  // are now literally the same button, sharing one teardown,
  // deactivateAnchor()) ever clears it.
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
  // THE ONE BANNER. Every element of it is templated in marketing/home.html
  // (#rcModeBanner) — nothing is built here — and every one of them is
  // written by updateModeBanner() below and by nothing else. The small
  // floating bar these used to be (#rcAnchorBar, in the chart canvas's own
  // top-left corner) is gone outright, markup and CSS with it.
  var modeBanner = document.getElementById('rcModeBanner');
  var modeBannerLabel = document.getElementById('rcModeBannerLabel');
  var modeBannerText = document.getElementById('rcModeBannerText');
  var modeBannerRole = document.getElementById('rcModeBannerRole');
  var modeBannerNote = document.getElementById('rcModeBannerNote');
  var modeBannerBtn = document.getElementById('rcModeBannerBtn');

  // The banner is sticky (rolechart.css), and what it has to clear is
  // .main-sticky-head — the topbar plus, on a day with a work-shift warning,
  // that strip too (core/templates/base.html) — whose height is not a
  // constant a stylesheet can know. So it is measured here and written
  // straight onto the element's own `top`: once at load, and again on every
  // overlay reflow (see reflowOverlays), which is what a window resize, a
  // browser zoom or the app's own sidebar collapsing already goes through.
  function syncBannerStickyTop() {
    if (!modeBanner) { return; }
    var head = document.querySelector('.main-sticky-head');
    var h = head ? head.getBoundingClientRect().height : 0;
    modeBanner.style.top = h + 'px';
  }
  syncBannerStickyTop();

  var activeAnchor = null;

  // WHAT THE CHART IS CURRENTLY SHOWING — {clientId, field, name, role}, set
  // right where the query itself is run and cleared by clearQueryMarks() like
  // every other query visual. The chart used to carry a second indicator for
  // exactly this (the floating .rc-query-pill, in the very same corner), which
  // the owner asked to have removed outright; but three real flows run an
  // Inquiry WITHOUT setting an anchor — Quick Inquiry, the admin "us" card's
  // own all-cases list, and the ?case= deep link — so the surviving bar has to
  // cover a query that set no anchor too.
  //
  // ``clientId``/``field`` exist purely so updateModeBanner() can tell whether
  // what is lit right now IS the active anchor's own query or some OTHER
  // company's — they are never used to fetch anything. ``clientId`` is null
  // for the one query with no Client behind it at all (runInquiryForUs).
  var queryStatus = null;

  // The one piece of "case mode" that does NOT fold into activeAnchor
  // itself — the case's own doc number and its own real client's name, kept
  // purely for the banner's own text (updateModeBanner, above) so a
  // later pivot (which moves activeAnchor.client to some OTHER company) does
  // not erase which case the reader is still "inside". Set only by
  // enterCaseMode; cleared only by deactivateAnchor() — never touched by a
  // pivot. See withCaseId(): the actual write-scoping uses
  // activeAnchor.caseId, not this.
  var caseMeta = null;

  function roleTextFor(field) {
    var node = field ? nodesByField[field] : null;
    return node ? node.getAttribute('data-role') : (field || '');
  }

  // Writes the banner's primary line: one main fact, plus (for a company) the
  // Persian role it is active as, in its own span so it keeps the rtl/muted
  // treatment every other role name on this chart has. Passing no role hides
  // that span rather than leaving an empty gap in the flex row.
  function setBannerText(main, role) {
    modeBannerText.textContent = main;
    modeBannerRole.textContent = role || '';
    modeBannerRole.hidden = !role;
  }

  // THE CHART'S ONE INDICATOR, AND THE TWO FACTS IT HAS TO KEEP STRAIGHT.
  //
  // ONE BANNER FOR BOTH KINDS OF ACTIVATION. There used to be two indicators:
  // this wide banner for case mode, and a small floating pill over the chart
  // canvas for an activated company. The owner asked for the banner treatment
  // to be the single one — a company activation now fills this same banner,
  // in the same place, with that company's own name/role and its Deactivate
  // control — and was explicit that the two must never both be on screen:
  // either a company-with-a-role is active or a case is. That is why the
  // caseMeta branch below comes FIRST and returns: with a case active there
  // is no company-style state left to render, because there is only one
  // element to render it into.
  //
  // NOW THE TWO FACTS. They are usually — but NOT always — the same fact:
  //
  //   1. WHICH COMPANY THE LINES ON THE CHART BELONG TO (``queryStatus``);
  //   2. WHICH COMPANY AN EDIT WOULD ATTACH TO (``activeAnchor`` — every
  //      other label card opens in "connect" mode against it, and every write
  //      it makes carries its own case scope through withCaseId()).
  //
  // Setting an anchor runs its own query, so normally both name the same
  // company and one line says everything. But three flows run an Inquiry
  // WITHOUT touching the anchor — Quick Inquiry, the admin "us" card's own
  // all-cases list, and the ?case= deep link — so with an anchor set for A,
  // a Quick Inquiry for B repaints the whole chart for B while A is still
  // what an edit would attach to. An earlier version of this function
  // returned early inside its ``if (activeAnchor)`` branch, which made the
  // indicator read "Active — A" over a chart drawn entirely for B: a reader
  // could misread which company the lines belonged to, which is the one thing
  // this indicator must never allow. Folding the case banner in here does not
  // get to reintroduce that bug, so the caseMeta branch carries the same
  // discipline: in case mode the primary text is the CASE (that is what the
  // owner asked to keep unchanged), and anything true beside it — the chart
  // showing some other company, edits attaching somewhere other than the
  // case's own client after a pivot — is stated in the note rather than
  // dropped.
  //
  // WHY BOTH FACTS ARE SHOWN, rather than having a plain Inquiry supersede
  // (clear) the anchor: superseding fixes the lie about the lines by telling
  // a second one. The anchor would be gone, but a reader who then opens any
  // label card would find plain browsing where connect mode was, and — in
  // case mode — every subsequent write silently unscoped from the case,
  // because deactivating drops ``caseId``/``caseMeta`` too. A Quick Inquiry
  // is a look-up gesture; it must not silently destroy the editing context
  // the reader built, and it must not quietly change where writes land. So
  // outside case mode the banner shows the QUERY as its primary text (it is
  // what the lines on the chart mean) and the anchor as a visibly subordinate
  // note beside it, and the one control clears both — labelled "Clear all" in
  // exactly that state so it does not read as clearing only the query.
  function updateModeBanner() {
    if (!modeBanner) { return; }
    // The anchor's OWN query — same company AND same role — is one fact, not
    // two, and reads exactly as it always did.
    var queryIsAnchor = !!(activeAnchor && queryStatus &&
      queryStatus.clientId === activeAnchor.client.id &&
      queryStatus.field === activeAnchor.field);
    var anchorNote = activeAnchor
      ? 'edits attach to ' + activeAnchor.client.name + ' — ' + roleTextFor(activeAnchor.field)
      : '';
    var showingNote = queryStatus
      ? 'chart is showing ' + queryStatus.name + (queryStatus.role ? ' — ' + queryStatus.role : '')
      : '';
    modeBannerNote.hidden = true;
    modeBannerNote.textContent = '';

    // 1. CASE MODE — the one state that wins outright, wording unchanged.
    if (caseMeta) {
      modeBanner.hidden = false;
      modeBannerLabel.textContent = 'Case mode';
      setBannerText('Editing chart for case ' + caseMeta.docNo + ' — ' + caseMeta.clientName, '');
      // Everything that is ALSO true and cannot be read off that line: a
      // query painted for someone else (Quick Inquiry over case mode), and/or
      // an anchor a pivot has moved off the case's own client.
      var notes = [];
      if (queryStatus && !queryIsAnchor) { notes.push(showingNote); }
      if (activeAnchor && activeAnchor.client.id !== caseMeta.clientId) { notes.push(anchorNote); }
      if (notes.length) {
        modeBannerNote.textContent = notes.join(' · ');
        modeBannerNote.hidden = false;
      }
      modeBannerBtn.textContent = 'Leave case mode';
      return;
    }

    // 2. A COMPANY IS ACTIVE, and the chart is showing its own query.
    if (activeAnchor && (!queryStatus || queryIsAnchor)) {
      modeBanner.hidden = false;
      modeBannerLabel.textContent = 'Active';
      setBannerText(activeAnchor.client.name, roleTextFor(activeAnchor.field));
      modeBannerBtn.textContent = 'Deactivate';
      return;
    }

    // 3. Two distinct facts. The chart is showing one company; edits still go
    //    to another.
    if (activeAnchor && queryStatus) {
      modeBanner.hidden = false;
      modeBannerLabel.textContent = 'Showing';
      setBannerText(queryStatus.name, queryStatus.role || '');
      modeBannerNote.textContent = anchorNote;
      modeBannerNote.hidden = false;
      modeBannerBtn.textContent = 'Clear all';
      return;
    }

    // 4. A plain query that set no anchor at all.
    if (queryStatus) {
      modeBanner.hidden = false;
      modeBannerLabel.textContent = 'Query';
      setBannerText(queryStatus.name, queryStatus.role || '');
      modeBannerBtn.textContent = 'Clear';
      return;
    }

    // 5. Nothing active — no banner at all.
    modeBanner.hidden = true;
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
    updateModeBanner();
    runInquiryForClient(client, field, !!opts.isCaseAnchor);
  }

  // The one shared teardown every "clear" control now goes through — this
  // banner's own Deactivate/Clear/Leave-case-mode button (every one of those
  // wordings is the same single control now — see updateModeBanner) — rather
  // than separate mechanisms. Fully clears the anchor and the banner alongside
  // the same visual state clearQueryMarks() already tears down for an
  // ordinary Clear, since the active anchor is always exactly what the
  // current query is showing.
  function deactivateAnchor() {
    activeAnchor = null;
    caseMeta = null;
    chartRoot.classList.remove('is-query');
    clearQueryMarks();
    updateModeBanner();
    if (caseModeInput) { caseModeInput.value = ''; }
    if (caseModeList) { caseModeList.innerHTML = ''; }
  }

  // THE banner's one control, in every one of its wordings — Deactivate,
  // Clear, Clear all, Leave case mode. There is no second clear control on
  // this chart any more: the case banner's own "Leave case mode" button IS
  // this button, relabelled by updateModeBanner().
  if (modeBannerBtn) { modeBannerBtn.addEventListener('click', deactivateAnchor); }

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
  // A node's box in the SVG's own viewBox coordinates, INCLUDING whatever
  // vertical shift its row currently carries (see the layout model below) and
  // whatever height it has currently grown to. Read off the <rect>'s own
  // attributes rather than getBBox(): getBBox() reports an element's box in
  // its own user space and knows nothing about the translate() sitting on its
  // parent row group, so mid-query it would answer with the row's ORIGINAL
  // position and every query line drawn from it would point at where that
  // card used to be. Everything drawn into #rcQueryLines lives OUTSIDE the
  // row groups, so it needs these absolute coordinates; the one thing that
  // does not is the case-anchor badge, which is appended INSIDE the node's
  // own group and therefore inherits the transform — see rawNodeRect().
  function nodeRect(field) {
    var r = rawNodeRect(field);
    if (!r) { return null; }
    var off = rowOffsetForField(field);
    r.y += off;
    r.cy += off;
    return r;
  }

  // The same box WITHOUT the row shift — for anything drawn as a child of the
  // node's own <g>, which already inherits that shift.
  function rawNodeRect(field) {
    var g = nodesByField[field];
    if (!g) { return null; }
    var box = g.querySelector('.rc-box');
    if (!box) { return null; }
    var x = parseFloat(box.getAttribute('x'));
    var y = parseFloat(box.getAttribute('y'));
    var w = parseFloat(box.getAttribute('width'));
    var h = parseFloat(box.getAttribute('height'));
    return { x: x, y: y, w: w, h: h, cx: x + w / 2, cy: y + h / 2 };
  }

  // THE FOCUS CARD'S OWN EXTRA MARK on top of plain is-focus — see
  // runInquiryForClient below, and rolechart.css's ``.is-case-anchor`` rules
  // for the actual look (a distinctly-coloured ring/glow plus this small
  // corner checkmark, never a per-field colour). This used to be drawn ONLY
  // for case mode's own automatic entry Inquiry (hence the name — every
  // class/id/function here still says "case anchor", unrenamed on purpose:
  // see runInquiryForClient's own note on why). The owner's own later
  // complaint was that this made a case-driven activation look reddish and a
  // plain name+role activation look green, which read as two different
  // MEANINGS rather than one look applied twice — so this mark is now drawn
  // for every focus card a query reaches, regardless of how it got there;
  // the underlying ``isCaseAnchor`` distinction still exists (case mode's own
  // ``caseMeta``, read by the mode banner) for the one place that genuinely
  // still needs to know "is this actually a case" — this mark itself no
  // longer means that. Built as real SVG child elements of the anchor node's
  // own <g> — the same "small clickable card is an SVG group" shape every
  // other per-node decoration on this chart already is (compare
  // ``.rc-badge``/``.rc-badge-text``, drawn once in rolechart.py) — rather
  // than an HTML overlay like ``.rc-card-names``, since this sits INSIDE the
  // box itself at a fixed corner and moves with the box when its row shifts.
  // ``caseAnchorField`` remembers which node currently carries it so
  // clearQueryMarks() can always remove it again, whether or not that field
  // is still lit at the time.
  var caseAnchorField = null;

  function addCaseAnchorBadge(field) {
    // rawNodeRect, not nodeRect: this badge is appended INSIDE the node's own
    // <g>, so it already inherits its row's own shift — using the shifted
    // coordinates here would apply that shift twice.
    var rect = rawNodeRect(field);
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
    queryStatus = null;
    queryLinesG.innerHTML = '';
    hideUsCasesPanel();
    // Puts every row, every box height, every role title, every edge and the
    // viewBox itself back exactly where the server drew them — see
    // applyLayout({})'s own "restore" path.
    applyLayout({});
    removeCaseAnchorBadge();
    updateModeBanner();
  }

  // ========================================================================
  // THE LAYOUT MODEL, IN-CARD GROWTH, AND THE REAL RELAYOUT THAT PAYS FOR IT
  // ========================================================================
  // While an Inquiry is active every TOUCHED card (the focus card AND every
  // lit card it connects to) has to show the queried company's own
  // "connected" name(s) — see marketing/services.py::connections_of_client's
  // ``connected`` field and runInquiryForClient's own namesByField below.
  // Two earlier shapes for that were rejected outright by the owner: a
  // separate floating box stacked below the card, then an HTML slab flush
  // against the card's own bottom edge. What is built here instead is what
  // was actually asked for:
  //
  //   * THE CARD'S OWN SVG BOX GROWS TALLER (its <rect> height attribute);
  //   * its Persian role title moves to the TOP of the grown box;
  //   * its English abbreviation is removed while grown (.is-grown, see
  //     rolechart.css) — there is no room for a second heading;
  //   * the connected names are listed INSIDE the box, with room for five
  //     before the list scrolls internally.
  //
  // And because a card really does get taller, this does a REAL RELAYOUT
  // rather than growing into a gap and hoping it fits:
  //
  //   * each ROW's extra height is the tallest growth among its own cards;
  //   * every row below is shifted down by the cumulative extra above it —
  //     one transform on that row's own <g class="rc-row-group"> (added to
  //     _role_chart.html for this, keyed off the row_index rolechart.py
  //     already emits), so a row moves as one unit: boxes, titles, badges and
  //     any decoration inside them together;
  //   * every edge between two shifted rows is re-routed from the recipe
  //     marketing/rolechart.py::build()'s own docstring publishes (each edge
  //     carries its seg/x1/x2 and its two original row anchors as data-*
  //     attributes now — see the template);
  //   * the svg's viewBox height grows by the total added, so the chart
  //     itself gets taller — which the owner explicitly accepted.
  //
  // Clearing the query calls applyLayout({}) and every one of those is put
  // back to the exact value it was read from at load: row transforms removed,
  // box heights and role baselines restored, each edge's own original ``d``
  // string reinstated (not recomputed — reinstated), viewBox reset.
  //
  // ONE EXCEPTION, and it is deliberate: the "us" card (Foolad Tabar) never
  // lists names and therefore never grows. Only its CASES are shown for it,
  // in the separate cases panel below. It is still highlighted as touched
  // like any other card.
  // ------------------------------------------------------------------------

  // ---- what the server drew, read once at load ---------------------------
  var VIEW_W0 = parseFloat(svg.getAttribute('data-view-w')) ||
    parseFloat(svg.getAttribute('viewBox').split(' ')[2]);
  var VIEW_H0 = parseFloat(svg.getAttribute('data-view-h')) ||
    parseFloat(svg.getAttribute('viewBox').split(' ')[3]);

  // One entry per row of rolechart.py's own ``rows``, in the same order:
  // {index, y (ORIGINAL top y — the baseline every shift is measured from, so
  // repeated growth never accumulates drift), el (the row's <g>), fields}.
  var rowGroups = [];
  var rowIndexByField = {};
  Array.prototype.forEach.call(svg.querySelectorAll('.rc-row-group'), function (g) {
    var index = parseInt(g.getAttribute('data-row-index'), 10);
    var fields = [];
    Array.prototype.forEach.call(g.querySelectorAll('.rc-node'), function (n) {
      var f = n.getAttribute('data-field');
      fields.push(f);
      rowIndexByField[f] = index;
    });
    rowGroups[index] = { index: index, y: parseFloat(g.getAttribute('data-row-y')), el: g, fields: fields };
  });

  // Each card's own original geometry, straight off the markup — the only
  // record of "what this looked like before any growth", so a restore is a
  // copy-back rather than a re-derivation.
  var nodeBase = {};
  Object.keys(nodesByField).forEach(function (field) {
    var g = nodesByField[field];
    var box = g.querySelector('.rc-box');
    var role = g.querySelector('.rc-role');
    if (!box) { return; }
    nodeBase[field] = {
      y: parseFloat(box.getAttribute('y')),
      h: parseFloat(box.getAttribute('height')),
      roleY: role ? parseFloat(role.getAttribute('y')) : null
    };
  });
  // The chart's own uniform card height (rolechart.py's NODE_H) — read off
  // the markup, never retyped here.
  var NODE_H0 = (function () {
    var keys = Object.keys(nodeBase);
    return keys.length ? nodeBase[keys[0]].h : 62;
  })();

  // Every drawn edge, with the four things build() publishes for recomputing
  // it plus its own original path string.
  var edgeCache = [];
  Array.prototype.forEach.call(svg.querySelectorAll('.rc-edges .rc-edge'), function (p) {
    edgeCache.push({
      el: p,
      d0: p.getAttribute('d'),
      seg: p.getAttribute('data-seg') || 'direct',
      x1: parseFloat(p.getAttribute('data-x1')),
      x2: parseFloat(p.getAttribute('data-x2')),
      pRow: parseInt(p.getAttribute('data-parent-row'), 10),
      cRow: parseInt(p.getAttribute('data-child-row'), 10)
    });
  });

  // ---- the current layout ------------------------------------------------
  var cardExtra = {};   // field -> extra height, in viewBox units
  var rowExtra = [];    // row index -> the tallest cardExtra in that row
  var rowOffset = [];   // row index -> how far down that row currently sits
  var totalExtra = 0;   // the sum of every rowExtra — how much taller the chart is

  function resetLayoutState() {
    cardExtra = {};
    rowExtra = rowGroups.map(function () { return 0; });
    rowOffset = rowGroups.map(function () { return 0; });
    totalExtra = 0;
  }
  resetLayoutState();

  function rowOffsetForField(field) {
    var i = rowIndexByField[field];
    return (i === undefined || !rowOffset[i]) ? 0 : rowOffset[i];
  }
  function currentRowTop(i) { return rowGroups[i].y + (rowOffset[i] || 0); }
  function currentRowBottom(i) { return currentRowTop(i) + NODE_H0 + (rowExtra[i] || 0); }

  // ---- growth sizing, in viewBox units -----------------------------------
  var GROW_ROLE_BAND = 30;      // the band the role title occupies at the top of a grown box
  var GROW_ROLE_BASELINE = 20;  // where that title's own baseline sits inside it
  var GROW_NAME_H = 17;         // one name row
  var GROW_PAD_BOTTOM = 8;      // breathing room under the last visible name
  var GROW_VISIBLE_NAMES = 5;   // "room for at least five names before an internal scroll"

  function grownHeight(nameCount) {
    var visible = Math.min(nameCount, GROW_VISIBLE_NAMES);
    return Math.max(NODE_H0, GROW_ROLE_BAND + visible * GROW_NAME_H + GROW_PAD_BOTTOM);
  }

  // {field: {names, h}} for every touched field that should grow. "us" is the
  // one carve-out (see the section comment); a field with nothing to list, or
  // one this chart does not draw, simply is not in the result.
  function growthForFields(fields) {
    var growth = {};
    if (!lastQueryData) { return growth; }
    fields.forEach(function (field) {
      if (field === 'us') { return; }
      if (!nodeBase[field]) { return; }
      var names = lastQueryData.namesByField[field];
      if (!names || !names.length) { return; }
      growth[field] = { names: names, h: grownHeight(names.length) };
    });
    return growth;
  }

  // ---- rolechart.py::_route, mirrored verbatim ---------------------------
  // Straight when the two columns line up, otherwise an S-elbow with rounded
  // corners at its midpoint. Copied from the JS transcription in
  // marketing/rolechart.py::build()'s own docstring, which exists precisely
  // so this file does not have to reinvent (or drift from) that shape.
  function route(x1, y1, x2, y2, r) {
    r = (r === undefined) ? 16 : r;
    if (x1 === x2) { return 'M' + x1 + ' ' + y1 + ' L' + x2 + ' ' + y2; }
    var ymid = (y1 + y2) / 2;
    var rr = Math.max(1, Math.min(r, Math.abs(ymid - y1), Math.abs(y2 - ymid), Math.abs(x2 - x1) / 2));
    var sx = x2 > x1 ? 1 : -1;
    var sy = y2 > y1 ? 1 : -1;
    return 'M' + x1 + ' ' + y1 +
      ' L' + x1 + ' ' + (ymid - rr * sy) +
      ' Q' + x1 + ' ' + ymid + ' ' + (x1 + rr * sx) + ' ' + ymid +
      ' L' + (x2 - rr * sx) + ' ' + ymid +
      ' Q' + x2 + ' ' + ymid + ' ' + x2 + ' ' + (ymid + rr * sy) +
      ' L' + x2 + ' ' + y2;
  }

  // The bottom edge an edge leaving row ``i`` at column ``x`` should start
  // from: that particular CARD's own bottom when one sits on that column,
  // falling back to the row's own (tallest) bottom otherwise. build()'s
  // recipe describes the row-level value, which is the right answer for the
  // shared many-to-many waist below; using it for the leg itself would leave
  // an edge starting in mid-air under a card that did not grow while its
  // row-mate did.
  function parentBottomAt(i, x) {
    var found = null;
    rowGroups[i].fields.forEach(function (field) {
      var base = nodeBase[field];
      var raw = rawNodeRect(field);
      if (!base || !raw) { return; }
      if (Math.abs(raw.cx - x) < 0.5) { found = field; }
    });
    if (found === null) { return currentRowBottom(i); }
    return currentRowTop(i) + nodeBase[found].h + (cardExtra[found] || 0);
  }

  function redrawEdges(restore) {
    edgeCache.forEach(function (e) {
      if (restore) { e.el.setAttribute('d', e.d0); return; }
      var pBottomRow = currentRowBottom(e.pRow);
      var cTop = currentRowTop(e.cRow);
      var waist = (pBottomRow + cTop) / 2;
      var d;
      if (e.seg === 'to_waist') {
        d = route(e.x1, parentBottomAt(e.pRow, e.x1), e.x2, waist);
      } else if (e.seg === 'from_waist') {
        d = route(e.x1, waist, e.x2, cTop);
      } else {
        d = route(e.x1, parentBottomAt(e.pRow, e.x1), e.x2, cTop);
      }
      e.el.setAttribute('d', d);
    });
  }

  // The growth map the chart is laid out for RIGHT NOW — the same object
  // applyLayout() was last handed, kept so a reflow (a resize; see
  // observeChartResize below) can re-place the pixel-space overlays from the
  // very same numbers without re-deriving them and without re-running the
  // layout itself. {} whenever nothing is grown.
  var currentGrowth = {};

  // The whole relayout, in one place. ``growth`` is growthForFields()'s own
  // map; an EMPTY one is the restore path (and is what clearQueryMarks()
  // calls), putting every value back exactly as the server drew it.
  function applyLayout(growth) {
    growth = growth || {};
    currentGrowth = growth;
    var fields = Object.keys(growth);
    var growing = fields.length > 0;

    resetLayoutState();
    fields.forEach(function (field) {
      cardExtra[field] = Math.max(0, growth[field].h - nodeBase[field].h);
    });
    rowGroups.forEach(function (row, i) {
      var most = 0;
      row.fields.forEach(function (f) { most = Math.max(most, cardExtra[f] || 0); });
      rowExtra[i] = most;
    });
    var acc = 0;
    rowGroups.forEach(function (row, i) {
      rowOffset[i] = acc;
      acc += rowExtra[i];
    });
    totalExtra = acc;

    rowGroups.forEach(function (row, i) {
      if (rowOffset[i]) { row.el.setAttribute('transform', 'translate(0 ' + rowOffset[i] + ')'); }
      else { row.el.removeAttribute('transform'); }
    });

    Object.keys(nodeBase).forEach(function (field) {
      var base = nodeBase[field];
      var g = nodesByField[field];
      var box = g.querySelector('.rc-box');
      var role = g.querySelector('.rc-role');
      var grown = Object.prototype.hasOwnProperty.call(growth, field);
      box.setAttribute('height', grown ? growth[field].h : base.h);
      if (role && base.roleY !== null) {
        role.setAttribute('y', grown ? (base.y + GROW_ROLE_BASELINE) : base.roleY);
      }
      g.classList.toggle('is-grown', grown);
    });

    svg.setAttribute('viewBox', '0 0 ' + VIEW_W0 + ' ' + (VIEW_H0 + totalExtra));
    redrawEdges(!growing);
    placeCardNames(growth);
  }

  // ---- the name lists themselves -----------------------------------------
  // Still plain HTML over the SVG — SVG has neither text wrapping nor
  // scrollable overflow — but transparent and positioned INSIDE the grown
  // box's own interior, so what the reader sees is the card's own fill with
  // its names on it, not a second surface. Geometry AND type size are scaled
  // by the SVG's own current on-screen ratio, because this chart is
  // responsive: one viewBox unit is not one screen pixel.
  var cardNameEls = {};

  function clearCardNames() {
    Object.keys(cardNameEls).forEach(function (field) {
      var el = cardNameEls[field];
      if (el && el.parentNode) { el.parentNode.removeChild(el); }
    });
    cardNameEls = {};
  }

  function placeCardNames(growth) {
    clearCardNames();
    var fields = Object.keys(growth || {});
    if (!fields.length || !rcCanvas) { return; }
    var svgBox = svg.getBoundingClientRect();
    var canvasBox = rcCanvas.getBoundingClientRect();
    if (!svgBox.width || !canvasBox.width) { return; }
    var scale = svgBox.width / VIEW_W0;   // screen px per viewBox unit
    fields.forEach(function (field) {
      var g = nodesByField[field];
      var box = g && g.querySelector('.rc-box');
      if (!box) { return; }
      var r = box.getBoundingClientRect();
      if (!r.width || !r.height) { return; }
      var el = document.createElement('div');
      el.className = 'rc-card-names';
      el.style.lineHeight = (GROW_NAME_H * scale) + 'px';
      el.style.fontSize = (11.5 * scale) + 'px';
      el.style.left = (r.left - canvasBox.left + 8 * scale) + 'px';
      el.style.width = Math.max(10, r.width - 16 * scale) + 'px';
      el.style.top = (r.top - canvasBox.top + GROW_ROLE_BAND * scale) + 'px';
      el.style.height = Math.max(GROW_NAME_H * scale,
        (growth[field].h - GROW_ROLE_BAND - GROW_PAD_BOTTOM) * scale) + 'px';
      growth[field].names.forEach(function (name) {
        var row = document.createElement('div');
        row.className = 'rc-card-names-name';
        row.textContent = name;
        row.title = name;
        row.setAttribute('dir', 'auto');
        el.appendChild(row);
      });
      // The list covers part of a card that is itself a control, so a click
      // on it opens that card exactly as a click on the card would — the
      // grown card stays ONE thing, not a card with a dead patch on it.
      el.addEventListener('click', function () {
        openModalForField(field, g.getAttribute('data-role'), g.getAttribute('data-abbr'), g.getAttribute('data-kind'));
      });
      rcCanvas.appendChild(el);
      cardNameEls[field] = el;
    });
  }

  // Grows every touched card and relayouts the chart around them. ``fields``
  // is the whole touched set (focus + lit), exactly as runInquiryForClient
  // already assembles it.
  function showCardGrowth(fields) {
    if (!lastQueryData) { return; }
    applyLayout(growthForFields(fields));
  }

  // ------------------------------------------------------------------------
  // The "us" cases panel — only while an Inquiry's connections include "us"
  // (the focused company has at least one case). Built once here and
  // reused across opens; hidden (and its own connector line cleared, since
  // that line lives in queryLinesG same as every other query line) by the
  // very same clearQueryMarks() every other query-mode visual already goes
  // through — see the call above.
  //
  // Positioned `position:absolute` inside `#rcCanvas` (see rolechart.css:
  // `.rc-canvas` is `position:relative`), in a place that is MEASURED, not
  // assumed — see reserveUsCasesArea() below. It used to be pinned to the
  // canvas's right edge and merely centred on a nominal y, which is how it
  // ended up sitting on top of real cards whenever a company had many cases;
  // the owner sent a screenshot of exactly that.
  //
  // It also has a fixed HEAD now, above the one scrolling region: the panel's
  // title, the three status counts for the company this query is about
  // (approved / cancelled / no result — services.case_status_counts, via the
  // JSON endpoint named by CFG.caseStatusCountsUrl), and, for the admin/GM
  // tier, a link straight into the case archive filtered to that same
  // company.
  // ------------------------------------------------------------------------
  var rcCanvas = document.getElementById('rcCanvas');
  var usCasesPanel = document.createElement('div');
  usCasesPanel.className = 'rc-us-cases-panel';
  usCasesPanel.id = 'rcUsCasesPanel';
  usCasesPanel.hidden = true;

  var usCasesHead = document.createElement('div');
  usCasesHead.className = 'rc-us-cases-head';
  var usCasesTitle = document.createElement('div');
  usCasesTitle.className = 'rc-us-cases-title';
  usCasesTitle.textContent = 'Cases connected to Us';
  usCasesHead.appendChild(usCasesTitle);
  var usCasesCounts = document.createElement('div');
  usCasesCounts.className = 'rc-us-cases-counts';
  usCasesCounts.hidden = true;
  usCasesHead.appendChild(usCasesCounts);
  var usCasesArchiveLink = document.createElement('a');
  usCasesArchiveLink.className = 'rc-us-cases-archive';
  usCasesArchiveLink.hidden = true;
  usCasesHead.appendChild(usCasesArchiveLink);
  usCasesPanel.appendChild(usCasesHead);

  // The one scrollable region — it takes whatever height the panel's own
  // measured box leaves after the head above it (flex:1/min-height:0 in the
  // CSS), so a case-heavy company scrolls INSIDE the reserved area instead of
  // growing the panel out of it. Built once per open, from the array already
  // in hand (no virtual scrolling, no re-render on scroll), and capped at
  // US_CASES_MAX_ROWS rows with a visible "showing N of M" line underneath
  // rather than a silent truncation.
  var usCasesList = document.createElement('div');
  usCasesList.className = 'rc-us-cases-list';
  usCasesPanel.appendChild(usCasesList);
  var usCasesMore = document.createElement('div');
  usCasesMore.className = 'rc-us-cases-more';
  usCasesMore.hidden = true;
  usCasesPanel.appendChild(usCasesMore);
  if (rcCanvas) { rcCanvas.appendChild(usCasesPanel); }

  // How many case rows are ever rendered at once. A company with hundreds of
  // cases is real, and building hundreds of rows (plus a branch line per
  // group) on every query is paid for on every single Inquiry — so the list
  // stops at a few dozen and says so.
  var US_CASES_MAX_ROWS = 40;

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
  // Viewers who may open a case (CFG.canOpenCases) additionally get the row
  // itself clickable, navigating to that case's own detail page via
  // CFG.caseDetailUrlBase — see home.html for how that URL base is built.
  //
  // CFG.canOpenCases, not CFG.isAdminTier: the rows in this array are already
  // scoped server-side to the cases this viewer may see
  // (marketing/access.py::case_access_for, applied by the client_connections /
  // us endpoints that fill the panel), so "there is a row here" and "you may
  // open it" are now the same statement and a per-row test would have nothing
  // left to decide. That is what makes the whole panel clickable for a
  // Marketing expert who also holds a Commercial seat — for their OWN cases,
  // which are the only ones they are sent. A viewer with no case access is sent
  // no rows at all, so no row can lead to a permission wall.
  //
  // CFG.caseOpenPrefix is how a dual-seat viewer gets there: they are sitting
  // in their Marketing seat, and the case page resolves participation against
  // the seat being worked, so the link goes through people:activate_role's
  // ?next= to switch them into their Commercial seat first. Empty for the
  // admin/GM tier, who go straight at the case.
  function renderUsCasesPanelContent(cases) {
    usCasesList.innerHTML = '';
    var total = cases.length;
    // The cap bites BEFORE grouping, so the groups shown are groups of rows
    // that are actually rendered and each heading's own "(n)" still counts
    // exactly the rows underneath it.
    var shownCases = total > US_CASES_MAX_ROWS ? cases.slice(0, US_CASES_MAX_ROWS) : cases;
    usCasesMore.hidden = shownCases.length >= total;
    usCasesMore.textContent = 'Showing ' + shownCases.length + ' of ' + total + ' cases';
    cases = shownCases;
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
        if (CFG.canOpenCases && CFG.caseDetailUrlBase && c.case_id != null) {
          var target = CFG.caseDetailUrlBase + c.case_id + '/';
          if (CFG.caseOpenPrefix) {
            target = CFG.caseOpenPrefix + encodeURIComponent(target);
          }
          row.classList.add('is-clickable');
          row.addEventListener('click', function () {
            window.location.href = target;
          });
        }
        group.appendChild(row);
      });
      usCasesList.appendChild(group);
    });
  }

  // THE PANEL'S RESERVED AREA — the chart's own bottom-right corner, worked
  // out from the cards that are really there rather than from a constant.
  //
  // The corner itself is reserved by the chart's own layout: rolechart.py's
  // _ROWS deliberately leaves COL[3] empty on its last rows ("a later phase
  // grows a panel there", in that module's own words). What is NOT safe to
  // assume is where that corner starts on screen: the chart scales with the
  // page's width, and — as of this round — rows shift and cards grow while an
  // Inquiry is active, which moves the very cards this panel must stay clear
  // of. So every number below is measured, in canvas-relative pixels:
  //
  //   * the panel claims a right-hand strip inside the canvas's own margin;
  //   * its TOP is the lowest bottom edge of any card whose own rect overlaps
  //     that strip horizontally (plus real clearance) — measured off each
  //     card's live getBoundingClientRect(), so a grown card pushes the panel
  //     down exactly as much as it actually grew;
  //   * its HEIGHT is whatever is left down to the canvas's own bottom
  //     margin, so the outer box is inside the chart by construction, and the
  //     list inside it scrolls.
  //
  // The one degenerate case — a canvas so short that the reserved strip
  // cannot fit a usable panel at all — is handled by falling back to a
  // minimum height pinned to the canvas's own bottom margin: still fully
  // inside the chart, at the cost of the clearance this function otherwise
  // guarantees. Returns null when the canvas has no size yet.
  var US_PANEL_MARGIN = 16;   // breathing room from the canvas's own edges
  var US_PANEL_CLEAR = 18;    // real clearance between the panel and any card
  var US_PANEL_MIN_H = 150;
  var US_PANEL_MAX_W = 440;

  function reserveUsCasesArea() {
    if (!rcCanvas) { return null; }
    var canvasBox = rcCanvas.getBoundingClientRect();
    var canvasW = canvasBox.width;
    var canvasH = canvasBox.height;
    if (!canvasW || !canvasH) { return null; }

    var width = Math.min(US_PANEL_MAX_W, Math.max(220, canvasW * 0.34));
    width = Math.min(width, Math.max(120, canvasW - 2 * US_PANEL_MARGIN));
    var left = canvasW - US_PANEL_MARGIN - width;
    var right = left + width;

    var top = US_PANEL_MARGIN;
    Object.keys(nodesByField).forEach(function (field) {
      var box = nodesByField[field].querySelector('.rc-box');
      if (!box) { return; }
      var r = box.getBoundingClientRect();
      var cardLeft = r.left - canvasBox.left;
      var cardRight = r.right - canvasBox.left;
      // Only a card sharing this strip's horizontal band can be in the way —
      // one three columns to the left never is, however low it sits.
      if (cardRight <= left - US_PANEL_CLEAR || cardLeft >= right + US_PANEL_CLEAR) { return; }
      top = Math.max(top, r.bottom - canvasBox.top + US_PANEL_CLEAR);
    });

    var height = canvasH - US_PANEL_MARGIN - top;
    if (height < US_PANEL_MIN_H) {
      height = Math.min(US_PANEL_MIN_H, Math.max(60, canvasH - 2 * US_PANEL_MARGIN));
      top = Math.max(US_PANEL_MARGIN, canvasH - US_PANEL_MARGIN - height);
    }
    return { left: left, top: top, width: width, height: height };
  }

  // The three status counts for the company this query is about — approved /
  // cancelled / no result, the same three buckets services.py::_status_fa
  // already puts every case in (see services.case_status_counts, and
  // marketing/views.py's client_case_counts endpoint behind
  // CFG.caseStatusCountsUrl). Fetched per open and rendered into the panel's
  // own fixed head; a viewer whose deployment does not expose the endpoint,
  // or a request that fails, simply gets no counts row rather than an error —
  // the case list underneath is the panel's real content and stands alone.
  function renderUsCasesCounts(client) {
    usCasesCounts.hidden = true;
    usCasesCounts.innerHTML = '';
    if (!CFG.caseStatusCountsUrl || !client || client.id == null) { return; }
    var mySeq = ++usCasesCountsSeq;
    get(CFG.caseStatusCountsUrl, { client_id: client.id }).then(function (data) {
      if (mySeq !== usCasesCountsSeq || !data || !data.ok || !data.counts) { return; }
      var rows = [
        { cls: 'is-approved', text: 'Approved', n: data.counts.approved },
        { cls: 'is-cancelled', text: 'Cancelled', n: data.counts.cancelled },
        { cls: 'is-pending', text: 'No result', n: data.counts.pending }
      ];
      usCasesCounts.innerHTML = '';
      rows.forEach(function (row) {
        var pill = document.createElement('span');
        pill.className = 'rc-us-cases-count ' + row.cls;
        var b = document.createElement('b');
        b.textContent = String(row.n || 0);
        pill.appendChild(b);
        pill.appendChild(document.createTextNode(row.text));
        usCasesCounts.appendChild(pill);
      });
      usCasesCounts.hidden = false;
    }).catch(function () {});
  }
  var usCasesCountsSeq = 0;

  // "Open in the case archive, filtered to this company." The URL shape is
  // the one that already works elsewhere in this app — the archive URL plus
  // ?fclient=<Name (CODE)>, URL-encoded — and it has to stay character for
  // character identical to the option text cases/views.py::archive builds for
  // its own f_clients dropdown, because that page filters by matching this
  // string against those options, not by client id.
  //
  // GATED ON CFG.canOpenCases, exactly like the per-case rows underneath it
  // (renderUsCasesPanelContent) — not on CFG.isAdminTier. It used to be the
  // narrower test, justified by "cases/views.py::archive redirects a Marketing
  // profile straight back to this page anyway, so this would be a round trip
  // to nowhere". The narrow test was wrong on its own terms: the link is the
  // panel's own header for the rows underneath it, and a viewer who may open
  // any of those rows may certainly see the list they came from. It also went
  // out WITHOUT the seat-switch prefix the case rows already use, so the one
  // mechanism that could carry a dual-seat viewer into their Commercial seat
  // on the way there was simply missing. It is applied here now: same prefix
  // (CFG.caseOpenPrefix — people:activate_role's own "?next="), same
  // encodeURIComponent, same destination discipline as a case row, with the
  // ?fclient= query riding along inside the encoded ``next`` untouched.
  //
  // WHAT THE PREFIX DOES AND DOES NOT FIX, measured rather than assumed (the
  // earlier wording here claimed apply_role_to_profile "moves profile.unit
  // with the seat"; it does not, and the claim is what made a broken link look
  // finished):
  //
  //   * admin/GM tier — no prefix, straight to /cases/archive/?fclient=…,
  //     which renders with that company preselected in its own filter. Works.
  //   * dual seat whose LOGIN profile is the Commercial one (the Marketing
  //     seat is the secondary account) — the archive never bounced them, and
  //     the prefix is harmless. Works.
  //   * dual seat whose LOGIN profile is the MARKETING one — still bounces.
  //     people/seats.py::apply_role_to_profile deliberately does NOT rewrite
  //     unit/role on the login profile for a SECONDARY seat (it would clash
  //     with the seat_code/unit/role UniqueConstraint), so activating the
  //     Commercial PersonRole leaves profile.unit == Unit.MARKETING, and
  //     cases/views.py::archive keys its "redirect a Marketing profile to
  //     marketing:home" test on exactly that field. The case DETAIL page has
  //     no such test — it resolves participation through the active seat — so
  //     a case row opens for this viewer while the archive does not.
  //
  // That last one is a backend gate this file cannot reach, and cannot even
  // detect: CFG carries nothing that separates the two dual-seat flavours (a
  // non-empty caseOpenPrefix describes both). The link is therefore offered to
  // everyone who may open cases, as the panel's rows already are; making the
  // third case land needs archive() to ask the ACTIVE SEAT (people.role_nav's
  // work_context, the way case_detail does) instead of the login profile's
  // unit.
  function renderUsCasesArchiveLink(client) {
    usCasesArchiveLink.hidden = true;
    if (!CFG.canOpenCases || !CFG.archiveUrl || !client || !client.name || !client.code) { return; }
    var target = CFG.archiveUrl + '?fclient=' +
      encodeURIComponent(client.name + ' (' + client.code + ')');
    if (CFG.caseOpenPrefix) { target = CFG.caseOpenPrefix + encodeURIComponent(target); }
    usCasesArchiveLink.href = target;
    usCasesArchiveLink.textContent = 'Open ' + client.name + ' in the case archive →';
    usCasesArchiveLink.hidden = false;
  }

  // JUST the measured geometry — reserveUsCasesArea()'s own answer written
  // onto the panel. Split out of openUsCasesPanel() so a reflow after a
  // resize can re-run exactly this (the panel's place is measured in screen
  // pixels off cards that just changed size) without re-rendering its content
  // or re-firing the counts fetch behind its head. Returns whether it could
  // place the panel at all.
  function positionUsCasesPanel() {
    var area = reserveUsCasesArea();
    if (!area) { return false; }
    // `right` is what the stylesheet's own no-JS fallback pins the panel by;
    // clearing it here is what lets the measured left/width take over.
    usCasesPanel.style.right = 'auto';
    usCasesPanel.style.left = area.left + 'px';
    usCasesPanel.style.top = area.top + 'px';
    usCasesPanel.style.width = area.width + 'px';
    usCasesPanel.style.height = area.height + 'px';
    return true;
  }

  // What the panel is currently showing — {cases, client}, kept for exactly
  // one reader: the resize reflow, which re-measures the panel's place (and
  // redraws its connector) but must not re-fetch or re-render anything. Set
  // on every open, cleared by hideUsCasesPanel().
  var usCasesPanelState = null;

  function openUsCasesPanel(cases, client) {
    if (!reserveUsCasesArea()) { return; }
    renderUsCasesCounts(client);
    renderUsCasesArchiveLink(client);
    renderUsCasesPanelContent(cases);
    positionUsCasesPanel();
    usCasesPanel.hidden = false;
    usCasesPanelState = { cases: cases, client: client };
  }

  function hideUsCasesPanel() {
    usCasesPanel.hidden = true;
    usCasesPanelState = null;
    usCasesList.innerHTML = '';
    usCasesCounts.innerHTML = '';
    usCasesCounts.hidden = true;
    usCasesArchiveLink.hidden = true;
    usCasesMore.hidden = true;
    usCasesCountsSeq += 1; // a counts fetch still in flight must not paint into a closed panel
  }

  // The trunk: the "us" node's own right edge to the panel's left edge, at
  // whatever y the panel was actually placed at (see reserveUsCasesArea/
  // openUsCasesPanel above — no longer "us"'s own centre-y, now the chart's
  // own measured bottom-right corner) — a small elbow rather than the old flat horizontal line, since
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
  //
  // ``animate`` is optional and defaults to true — the one caller that passes
  // false is the resize reflow, where re-playing the fill-in every time the
  // window changes width would read as a twitch rather than an answer. Both
  // the trunk and its branches go into ONE <g class="rc-us-cases-connector">
  // inside queryLinesG (styling is class-based on the paths themselves, so
  // the extra group changes nothing visually) purely so a redraw can drop the
  // previous connector without touching the query lines beside it — those are
  // drawn in pure viewBox units and never need re-drawing on a resize.
  function drawUsCasesConnector(animate) {
    animate = (animate === undefined) ? true : !!animate;
    var stale = queryLinesG.querySelector('.rc-us-cases-connector');
    if (stale && stale.parentNode) { stale.parentNode.removeChild(stale); }
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
    var connG = document.createElementNS(ns, 'g');
    connG.setAttribute('class', 'rc-us-cases-connector');
    queryLinesG.appendChild(connG);

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
    connG.appendChild(trunk);
    if (animate) { animateFill(trunk); }

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
      connG.appendChild(branch);
      if (animate) { animateFill(branch); }
    });
  }

  // ========================================================================
  // REFLOW — KEEPING THE PIXEL-SPACE OVERLAYS ON THEIR CARDS WHEN THE CHART
  // CHANGES SIZE
  // ========================================================================
  // Almost everything this file draws lives in the SVG's own viewBox units
  // and is therefore resolution-independent for free: the row transforms, the
  // grown box heights, every edge, every query line. They scale with the SVG
  // and need no attention here.
  //
  // Three things do NOT. The in-card name lists (.rc-card-names) are plain
  // HTML positioned in absolute screen pixels measured off each card's live
  // rect, with their type size scaled from the SVG's current on-screen width;
  // the "us" cases panel's reserved corner is measured the same way; and the
  // panel's own connector converts between the two spaces. The chart is
  // deliberately fluid (.rc-canvas svg{width:100%}), so every one of those
  // numbers goes stale the moment the canvas changes width — which left every
  // grown card's name list, and the panel with its connector, stranded at
  // their old positions and sizes until the query was cleared and re-run.
  //
  // A ResizeObserver ON THE CANVAS, not a window resize listener: the canvas
  // is what every one of those measurements is actually relative to, and it
  // changes size for reasons a window resize never fires for — the app's
  // sidebar collapsing or expanding beside it being the obvious one, a
  // scrollbar appearing beside it another. It also still covers plain window
  // resizes and browser zoom, both of which change the canvas's own CSS width.
  // (window.addEventListener('resize') remains the fallback for a browser
  // without ResizeObserver, which is the only thing it would be better at.)
  //
  // NOTHING HERE CAN ACCUMULATE DRIFT. The reflow does not re-run the layout
  // and never writes back a baseline: applyLayout()'s own inputs — VIEW_W0/
  // VIEW_H0, rowGroups[].y, nodeBase, each edge's d0 — are still read once at
  // load and only ever read. This re-runs the two placement passes against
  // the SAME growth map the layout was built from (currentGrowth) and the
  // cards' CURRENT live rects, so the answer after ten resizes is the answer
  // one resize to the same width would have given.
  var RESIZE_REFLOW_MS = 120;   // debounce — never reposition mid-drag
  var reflowTimer = null;

  function reflowOverlays() {
    // The sticky banner's own offset is measured off .main-sticky-head, whose
    // height can change with the window's width (its contents wrap) — so it
    // is re-measured here, BEFORE the early return below, since it goes stale
    // whether or not anything is currently painted over the chart.
    syncBannerStickyTop();
    // Nothing is painted over the chart — no grown cards, no panel — so
    // there is nothing whose position could have gone stale.
    if (!Object.keys(currentGrowth).length && !usCasesPanelState) { return; }
    placeCardNames(currentGrowth);
    if (usCasesPanelState) {
      positionUsCasesPanel();
      drawUsCasesConnector(false);  // re-place it, but do not re-play its fill
    }
  }

  function scheduleOverlayReflow() {
    window.clearTimeout(reflowTimer);
    reflowTimer = window.setTimeout(reflowOverlays, RESIZE_REFLOW_MS);
  }

  (function observeChartResize() {
    if (!rcCanvas) { return; }
    if (!window.ResizeObserver) {
      window.addEventListener('resize', scheduleOverlayReflow);
      return;
    }
    // Both overlays are position:absolute inside the canvas (see
    // rolechart.css), so re-placing them cannot change the canvas's own box
    // and this observer cannot feed itself. The size check below is belt and
    // braces for that, and also swallows the one notification every
    // ResizeObserver delivers on observe() before anything has changed.
    var lastW = rcCanvas.getBoundingClientRect().width;
    var lastH = rcCanvas.getBoundingClientRect().height;
    var ro = new ResizeObserver(function () {
      var r = rcCanvas.getBoundingClientRect();
      if (Math.abs(r.width - lastW) < 0.5 && Math.abs(r.height - lastH) < 0.5) { return; }
      lastW = r.width;
      lastH = r.height;
      scheduleOverlayReflow();
    });
    ro.observe(rcCanvas);
  })();

  // The chart's own vertical centre lane — the middle of the viewBox, which
  // is rolechart.py's own CEN (650 of 1300) without this file having to
  // retype either number. This used to read "project"'s own centre-x on the
  // grounds that rolechart.py always placed that card on the lane; it does
  // not any more (project was re-paired onto PAIR[0] = 500 when its row was
  // merged with phase), so that reading silently became "the x of a column
  // full of cards" — and every query line drawn down it ran underneath them.
  function centerLaneX() {
    return VIEW_W0 / 2;
  }

  // ---- routing a query line so it never crosses a card it is not touching --
  // The gutter between two rows is empty across the chart's whole width by
  // construction (rolechart.py's _GAP is bigger than a card is tall, and this
  // file's own relayout shifts whole rows, so the gap between one row's
  // TALLEST bottom and the next row's top is preserved exactly). So a
  // horizontal run placed in a gutter can never cross a card, and the only
  // question left is where to put the VERTICAL run between two gutters.
  var LANE_GUTTER_PAD = 14;  // how far into the gutter a horizontal run sits
  var LANE_CLEAR = 8;        // how far a vertical lane must stay off a card's side

  function gutterBelowRow(i) { return currentRowBottom(i) + LANE_GUTTER_PAD; }
  function gutterAboveRow(i) { return currentRowTop(i) - LANE_GUTTER_PAD; }

  // The x for the vertical run between ``yTop`` and ``yBottom``: the chart's
  // own centre lane whenever nothing sits on it over that span, otherwise the
  // nearest x to the centre that clears every card the span passes. The
  // candidates are the SIDES of the cards actually in the way (plus
  // clearance) — so the lane hugs the corridor between two columns rather
  // than being picked from a hardcoded list of "probably empty" x values.
  function pickLaneX(yTop, yBottom) {
    var centre = centerLaneX();
    var blocked = [];
    Object.keys(nodesByField).forEach(function (field) {
      var r = nodeRect(field);
      if (!r) { return; }
      if (r.y + r.h <= yTop || r.y >= yBottom) { return; }   // not in this span at all
      blocked.push([r.x - LANE_CLEAR, r.x + r.w + LANE_CLEAR]);
    });
    function isFree(x) {
      for (var i = 0; i < blocked.length; i++) {
        if (x > blocked[i][0] && x < blocked[i][1]) { return false; }
      }
      return true;
    }
    if (isFree(centre)) { return centre; }
    var candidates = [];
    blocked.forEach(function (b) { candidates.push(b[0], b[1]); });
    candidates = candidates.filter(function (x) {
      return x > LANE_CLEAR && x < VIEW_W0 - LANE_CLEAR && isFree(x);
    });
    if (!candidates.length) { return centre; }
    candidates.sort(function (a, b) { return Math.abs(a - centre) - Math.abs(b - centre); });
    return candidates[0];
  }

  // The full point list for one query line, from the focus card's own edge to
  // the target card's own edge: out of the card vertically, along the gutter
  // beside its row, down (or up) the lane, along the target row's own gutter,
  // and into the target card's edge. Every leg is either inside one card's
  // own column or inside an empty gutter or on a cleared lane, so no leg can
  // cross a card this line is not connecting.
  function queryLinePoints(fromField, toField) {
    var from = nodeRect(fromField);
    var to = nodeRect(toField);
    var fromRow = rowIndexByField[fromField];
    var toRow = rowIndexByField[toField];
    if (!from || !to || fromRow === undefined || toRow === undefined) { return null; }
    if (fromRow === toRow) {
      // Two cards side by side: drop into the gutter under their shared row,
      // run across it, and come back up. (The lane is irrelevant here — the
      // gutter run IS the whole horizontal move.)
      var gy = gutterBelowRow(fromRow);
      return [
        { x: from.cx, y: from.y + from.h },
        { x: from.cx, y: gy },
        { x: to.cx, y: gy },
        { x: to.cx, y: to.y + to.h }
      ];
    }
    var down = toRow > fromRow;
    var gFrom = down ? gutterBelowRow(fromRow) : gutterAboveRow(fromRow);
    var gTo = down ? gutterAboveRow(toRow) : gutterBelowRow(toRow);
    var lane = pickLaneX(Math.min(gFrom, gTo), Math.max(gFrom, gTo));
    return [
      { x: from.cx, y: down ? from.y + from.h : from.y },
      { x: from.cx, y: gFrom },
      { x: lane, y: gFrom },
      { x: lane, y: gTo },
      { x: to.cx, y: gTo },
      { x: to.cx, y: down ? to.y : to.y + to.h }
    ];
  }

  // laneEdgePoint() used to live here — "the point on a node's own SIDE
  // closest to the centre lane, at the node's own centre-y". It is gone with
  // the routing that needed it: leaving a card sideways at its own centre-y
  // is precisely what sent a query line straight through whatever cards stood
  // between that card and the lane. Query lines now leave a card through its
  // TOP or BOTTOM edge into the empty gutter beside its row — see
  // queryLinePoints() above.

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
  //
  // CROSSING LINES READ AS A CLEAN "HOP OVER", NOT A JAGGED CUT, because
  // every line is painted with a wider, background-coloured HALO directly
  // underneath its own coloured stroke (see buildHaloPath below and
  // .rc-query-line-halo in rolechart.css). There is no crossing-avoidance or
  // lane-sharing logic anywhere on this chart — pickLaneX only ever keeps a
  // lane clear of CARDS, never of other query lines — so whenever two lines
  // active at once happen to share a corridor (the owner's own report: a
  // focus card connected both toward a Supervision-Consultant-type card and
  // toward "us" at once), whichever line is inserted into the DOM later
  // simply painted fully OVER the earlier one at the crossing, which read as
  // the earlier line being cut rather than passing under. An opaque halo
  // fixes that with no routing changes at all: painted first (below this
  // line's own colour) and solid (no dash, no fade-in, no opacity), a LATER
  // line's halo still paints over an EARLIER line's full stroke — halo
  // included — at their crossing, but now that "paint over" is a same-width
  // gap in the earlier line's own background colour rather than a hard edge
  // in its accent colour, exactly like a road passing under a bridge on a
  // map. Deliberately subtle everywhere else: --rc-ground is this chart's
  // own canvas floor colour (.rc-canvas's own background, already flips with
  // the theme), so away from any crossing the halo just blends into the
  // empty gutter/lane a line already runs through and a solitary,
  // non-crossing line reads exactly as it did before this change.
  function buildHaloPath(ns, d) {
    var halo = document.createElementNS(ns, 'path');
    halo.setAttribute('class', 'rc-query-line-halo');
    halo.setAttribute('d', d);
    return halo;
  }

  function drawQueryLines(focusField, litFields, annotations) {
    annotations = annotations || {};
    queryLinesG.innerHTML = '';
    if (!nodeRect(focusField)) { return; }
    var ns = 'http://www.w3.org/2000/svg';
    litFields.forEach(function (field) {
      // Out of the focus card, along its row's own gutter, down the chart's
      // centre lane (or the nearest clear corridor to it), along the target
      // row's gutter and into the target card — see queryLinePoints(). The
      // old route ran from one card's SIDE straight across to the lane at
      // that card's own centre-y, which is exactly a line drawn underneath
      // whatever cards sat between the two.
      var raw = queryLinePoints(focusField, field);
      if (!raw) { return; }
      var pts = dedupePoints(raw);
      if (pts.length < 2) { return; }
      var d = elbowPath(pts, ELBOW_R);
      // The halo is appended FIRST (so it paints below this line's own
      // colour) and carries no animation of its own — it must be fully
      // opaque and in place from the very first frame, or an early crossing
      // would show through it while the fill-in animation is still running.
      queryLinesG.appendChild(buildHaloPath(ns, d));
      var path = document.createElementNS(ns, 'path');
      path.setAttribute('class', 'rc-query-line');
      path.setAttribute('d', d);
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
  // own real client under its own effective role. IT NO LONGER GATES THE
  // FOCUS CARD'S LOOK. It used to: only this one caller's focus card got the
  // extra .is-case-anchor treatment (a distinctly-coloured ring/glow plus a
  // checkmark), so a case activation read visibly different from a plain
  // name+role one (green vs. a warm/"reddish" colour) — which the owner
  // flagged as the wrong signal: it read as two different MEANINGS ("this is
  // a case" vs. "this is just active") when both are really the same fact,
  // "this card is what the chart is showing you", just reached two different
  // ways. So every focus card now gets the same mark (see below) whether it
  // came from case mode, a plain in-chart Inquiry, Quick Inquiry, a
  // connect-mode card's own post-Confirm re-query, or a pivot — the parameter
  // is kept (rather than deleted outright) purely so a future caller that
  // genuinely needs to know "is this specifically a case activation" has
  // somewhere to plug in without a second signature change; the one place
  // that distinction still has to be READ from is ``caseMeta``, which the
  // mode banner (updateModeBanner) already keys off instead of this.
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
          // See this function's own ``isCaseAnchor`` comment above — this
          // mark is now unconditional for every focus card, EXCEPT "us",
          // which keeps its own categorical black/white plate (rolechart.css)
          // no matter how it became the focus field. "us" CAN become
          // ``focusField`` here — the admin "us" card's own all-cases list
          // runs this same function with originField 'us' (see
          // fetchAllCasesSearch/renderAllCasesRows further down), and 'us' is
          // always among ``allFields`` whenever this client has any case at
          // all — so without this guard a click through that list would
          // paint the warm ring/fill and a checkmark straight over the "us"
          // card's own deliberately-different plate.
          if (focusField !== 'us') {
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
      // growthForFields()/showCardGrowth() above for where this is read back
      // out.
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
        // ``code`` comes from the server's own answer (``data.client``, built
        // by views._client_json), not from the caller's own {id, name} — the
        // cases panel's archive link needs the exact "Name (CODE)" string the
        // archive page's own filter options carry, and most callers here have
        // only a name in hand.
        client: { id: client.id, name: client.name, code: data.client ? data.client.code : null },
        byField: byField, namesByField: namesByField,
        labelsRaw: data.labels
      };
      window.requestAnimationFrame(function () {
        // ORDER MATTERS HERE. The card growth is what relayouts the chart —
        // rows shift, boxes get taller, the viewBox grows — so it runs FIRST
        // and everything that reads a coordinate afterwards reads the new
        // one: the query lines are routed against the shifted rows, and the
        // cases panel measures its reserved corner against cards that have
        // already moved. Doing it the other way round is exactly how a line
        // ends up pointing at where a card used to be.
        //
        // Every touched field — focus and lit alike — grows; allFields
        // already IS that whole set (focusField plus litFields, and "us" too
        // when showUsPanel pushed it above; growth itself skips "us" — see
        // growthForFields).
        showCardGrowth(allFields);
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
          openUsCasesPanel(data.cases, lastQueryData.client);
          drawUsCasesConnector();
        }
      });
      // The chart's one indicator (the old floating query pill and the old
      // floating anchor bar are both gone — see updateModeBanner): whatever
      // this query is showing names itself here, WHETHER OR NOT it also set
      // the anchor. The id/field pair is what lets the banner tell "this is
      // the anchor's own query" from "this is some other company painted over
      // an anchor that is still active".
      queryStatus = {
        clientId: client.id,
        field: focusField,
        name: client.name,
        role: focusField && nodesByField[focusField]
          ? nodesByField[focusField].getAttribute('data-role') : ''
      };
      updateModeBanner();
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
    // clientId null: "us" is the one query with no Client row behind it, so
    // it can never be mistaken for the active anchor's own query — which is
    // right, since this repaints the chart for "us" and leaves any anchor
    // exactly where it was.
    queryStatus = {
      clientId: null, field: 'us', name: 'Us',
      role: focusG ? focusG.getAttribute('data-role') : ''
    };
    updateModeBanner();
  }

  // The old query pill's own Clear button was wired here; it is gone with the
  // pill. Its job (clear a query that set no anchor) is now the same
  // Deactivate/Clear button on the one mode banner, wired further up —
  // deactivateAnchor() was always the shared teardown for both.

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
  // top of the file, toggle hidden/visible on demand" convention as the "us"
  // cases panel above; opening it just widens the SAME modal
  // (.rc-modal.is-wide) — never a second window.
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

  // Reuses .rc-modal-list/.rc-row/.rc-row-empty/.mc-row-add exactly as this
  // file's own label list already does. (.mc-row-add keeps its prefix from
  // the deleted Companies tab it was written for — see rolechart.css's own
  // note at the bottom of that file; the chart is its only owner now.)
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

  // SEVERAL companies at a time, ticked off the search results and/or just
  // registered via the create-new row — the two are functionally identical
  // from here on, since client_create's own near-duplicate check
  // (services.get_or_create_client/normalize_persian) may hand back an
  // EXISTING client for a near-duplicate name instead of a fresh one — see
  // createCompanyPicker's own buildCreateRow, which relies on that
  // server-side check rather than any separate client-side one. The picker
  // itself is the one shared helper further up this file, in its ``multi``
  // mode (the owner's own request: tick several, press Add once) — the ticked
  // set survives a new search, so a reader can tick one company, search for
  // the next, tick that too, and commit both together. This is just that
  // helper's own confirm-button wiring, specific to THIS panel.
  var addCompanyPicker = createCompanyPicker(addCompanyList, addCompanySearchInput, function (clients) {
    addCompanyConfirmBtn.disabled = !(clients && clients.length);
  }, { multi: true });

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

  // Commits EVERY ticked company to the CURRENTLY OPEN card's own field, in
  // one press. The two modes keep exactly the behaviour each already had —
  // only the count changed, from one company to all of the ticked ones:
  //
  //   * CONNECT MODE stages, it does not commit. The found/created company
  //     plays modalField (B)'s role, CONNECTED to the active anchor rather
  //     than tagged itself — the exact same fact a tick on one of B's
  //     already-real rows stages (see renderAnchorConnectRows's own
  //     connectCtx), just reached through search-or-create instead of an
  //     existing row, so it lands in the SAME anchorConnectState.staged
  //     bucket. This card's own Confirm is what actually calls
  //     create_connection, same as every other staged tick.
  //   * DEFAULT BROWSING MODE commits immediately, as a plain manual tag —
  //     now one labelToggle per ticked company, CHAINED rather than fired in
  //     parallel, the same discipline confirmCardBtn's own staged writes
  //     already follow so a slow request never races the next one.
  //
  // Neither branch closes the panel — refreshLabelList() redraws the card's
  // own main/left list (or, in connect mode, its ticked connect-row view) to
  // show the just-added companies THERE, exactly as any other staged/attached
  // addition already would, while this panel stays open beside it for the
  // next batch.
  addCompanyConfirmBtn.addEventListener('click', function () {
    var clients = addCompanyPicker.selectedMany();
    if (!clients.length || modalKind !== 'label' || !modalField) { return; }
    var field = modalField;
    if (modalMode === 'connect' && anchorConnectState) {
      clients.forEach(function (client) {
        anchorConnectState.staged[client.id] = { name: client.name, add: true };
      });
      resetAddCompanyPickerForNextPick();
      renderAnchorConnectListBody();
      return;
    }
    addCompanyConfirmBtn.disabled = true;
    var chain = Promise.resolve();
    clients.forEach(function (client) {
      chain = chain.then(function () {
        return post(CFG.labelToggleUrl, { client_id: client.id, label: field, add: 1 });
      });
    });
    chain.then(function () {
      // The card that was open when this batch started may have been closed
      // or swapped for another mid-flight; in that case the writes still
      // landed (they were always meant for ``field``), there is just nothing
      // left on screen to refresh.
      if (modalField !== field) { addCompanyConfirmBtn.disabled = false; return; }
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
  // The admin/GM "us" card search's own debounce handle — this search is a
  // server round trip, so it is debounced; the label card's own search stays
  // a plain instant local filter over an already-fetched list and needs no
  // debounce of its own.
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
      // is debounced — see usSearchDebounce/US_SEARCH_DEBOUNCE_MS above.
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
    // renderContextualQueryRow, which this does not touch). A
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
  // comment / the `if (!chartRoot || !CFG)` guard above) — the card is
  // templated by marketing/home.html only, so its absence on any other page
  // that loads this script is expected, not an error, and this block simply
  // does nothing there.
  // ------------------------------------------------------------------------
  var quickPanel = document.getElementById('rcQuickInquiry');
  if (quickPanel) {
    var quickCompanyInput = document.getElementById('rcQuickCompanyInput');
    var quickCompanyList = document.getElementById('rcQuickCompanyList');
    var quickRoleInput = document.getElementById('rcQuickRoleInput');
    var quickRoleList = document.getElementById('rcQuickRoleList');
    var quickAddRoleToggle = document.getElementById('rcQuickAddRoleToggle');
    var quickNewRoleWrap = document.getElementById('rcQuickNewRoleWrap');
    var quickNewRoleInput = document.getElementById('rcQuickNewRoleInput');
    var quickNewRoleList = document.getElementById('rcQuickNewRoleList');
    var quickNewRoleAdd = document.getElementById('rcQuickNewRoleAdd');
    var quickConfirmBtn = document.getElementById('rcQuickConfirm');

    if (quickCompanyInput && quickCompanyList && quickRoleInput && quickRoleList && quickAddRoleToggle &&
        quickNewRoleWrap && quickNewRoleInput && quickNewRoleList && quickNewRoleAdd && quickConfirmBtn) {

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
        // A new company means a clean slate for "add a new role" too — the
        // available-roles list it would otherwise still be showing belongs
        // to whichever company was picked before.
        quickNewRolePicker.reset();
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

      // "Add a new role"'s own field — a SECOND createRolePicker instance,
      // not a second component: it is exactly the same "filter a small local
      // array" job quickRolePicker above already does, just against a
      // different local array (the roles this company does NOT already
      // hold — built fresh every time quickAddRoleToggle is pressed, see
      // below) and feeding a different Add button instead of the Confirm
      // button. onSelect only has to keep THIS Add button's enabled state
      // current, mirroring quickUpdateConfirmState's own shape.
      var quickNewRolePicker = createRolePicker(quickNewRoleList, quickNewRoleInput, function (item) {
        quickNewRoleAdd.disabled = !item;
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
      // "Add a new role"'s own dropdown — same focus-opens/populate-if-empty
      // shape quickRoleInput's own listener just above already uses, since
      // this field is now the same component, just a second instance
      // (quickNewRolePicker) filtering a different local array.
      quickNewRoleInput.addEventListener('focus', function () {
        if (quickNewRoleInput.disabled) { return; }
        quickNewRoleList.hidden = false;
        if (!quickNewRoleList.childElementCount) { quickNewRolePicker.render(quickNewRoleInput.value); }
      });
      // Closes every dropdown on any click outside the whole card — picking
      // a row already closes its own (quickShowDropdown/quickShowRoleDropdown/
      // quickNewRoleList.hidden above), so this is only for "the reader
      // clicked away without choosing".
      document.addEventListener('click', function (ev) {
        if (!quickPanel.contains(ev.target)) {
          quickShowDropdown(false);
          quickShowRoleDropdown(false);
          quickNewRoleList.hidden = true;
        }
      });

      // Builds the "does not already hold" role list fresh every time this
      // toggle is pressed (the company's own roles can have changed since it
      // was last opened) and hands it to quickNewRolePicker exactly the way
      // quickPopulateRoleField above hands quickRoles to quickRolePicker —
      // same {field, label_fa} shape, so both pickers' own buildRow reads it
      // identically.
      quickAddRoleToggle.addEventListener('click', function () {
        if (!quickCompany) { return; }
        var have = {};
        quickRoles.forEach(function (l) { have[l.label] = true; });
        var available = quickAllLabels.filter(function (l) { return !have[l.field]; });
        quickNewRolePicker.setItems(available.map(function (l) {
          return { field: l.field, label_fa: l.role_fa };
        }));
        quickNewRoleInput.value = '';
        quickNewRoleInput.disabled = !available.length;
        quickNewRoleInput.placeholder = available.length ? 'Choose a role to add…' : 'Already has every role';
        quickNewRoleAdd.disabled = true;
        quickNewRoleList.innerHTML = '';
        quickNewRoleList.hidden = true;
        quickNewRoleWrap.hidden = false;
      });

      // Reuses CFG.labelToggleUrl — the exact same "assign a role" endpoint
      // every other flow in this file already calls (the "+ Add company"
      // panel, a label row's own ×) — nothing new added to
      // marketing/views.py for this. Unchanged registration flow: this
      // still only calls labelToggleUrl and repopulates the "existing role"
      // field — it does NOT run an Inquiry, exactly as the old native
      // <select> version never did (see the module's own AUDIT note on
      // "Add" vs. "Run inquiry" staying two separate steps).
      quickNewRoleAdd.addEventListener('click', function () {
        var field = quickNewRolePicker.selectedField();
        if (!quickCompany || !field) { return; }
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
  // any is missing (any page other than marketing/home.html, where none of
  // this is rendered).
  // ------------------------------------------------------------------------
  var caseModeWidget = document.getElementById('rcCaseModeWidget');
  var caseModeInput = document.getElementById('rcCaseModeInput');
  var caseModeList = document.getElementById('rcCaseModeList');
  // Declared out here (rather than as a plain ``function enterCaseMode(c)``
  // statement inside the ``if`` below) so the deep-link block further down
  // this file — which is NOT inside that ``if`` and, under this file's own
  // 'use strict' pragma, would otherwise get a block-scoped binding it can
  // never see — can call the exact same entry point a dropdown pick uses.
  // The ``if`` below still only ASSIGNS this on pages that actually render
  // the case-mode widget, so a page without it leaves this ``undefined``,
  // exactly like ``caseModeWidget`` itself.
  var enterCaseMode;
  // No banner elements of its own any more: the banner this mode shows is the
  // chart's ONE banner (#rcModeBanner, looked up near the top of this file),
  // shared with an ordinary company activation and owned by
  // updateModeBanner(). This block only searches cases and enters the mode.
  if (caseModeWidget && caseModeInput && caseModeList) {

    var caseModeSeq = 0;
    var caseModeDebounce = null;

    function caseModeShowDropdown(show) { caseModeList.hidden = !show; }

    function buildCaseModeRow(c) {
      var row = document.createElement('div');
      row.className = 'rc-row';
      // Doc number ONLY — no " — <client_name>" suffix (the owner's own
      // complaint: that combination wrapped awkwardly in this narrow
      // floating dropdown). The client's name is still shown once the case
      // is picked, in the mode banner (updateModeBanner, above) — nothing is
      // lost, just not duplicated here too.
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
    enterCaseMode = function (c) {
      // ``clientId`` is carried alongside the banner's own text so
      // updateModeBanner() can tell a later PIVOT (which moves activeAnchor to
      // some other company while case mode stays on) from the ordinary case
      // where the anchor still is the case's own client — and say so.
      caseMeta = { caseId: c.case_id, clientId: c.client_id, docNo: c.doc_no, clientName: c.client_name };
      setActiveAnchor({ id: c.client_id, name: c.client_name }, c.label, { caseId: c.case_id, isCaseAnchor: true });
      updateModeBanner();
      caseModeInput.value = c.doc_no;
      caseModeList.innerHTML = '';
      chartRoot.scrollIntoView({ behavior: 'smooth', block: 'start' });
    };

    // "Leave case mode" is not a control of its own any more: it is the one
    // mode banner's own button, wired to the same shared teardown
    // (deactivateAnchor) near the top of this file and merely RELABELLED
    // "Leave case mode" while a case is active — see updateModeBanner().
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
  //
  // This has to go through the SAME door the case-mode search widget's own
  // row click uses (enterCaseMode, defined above) rather than call
  // runInquiryForClient() directly — a plain Inquiry call is not case mode:
  // it never sets activeAnchor.caseId/caseMeta, so no mode banner would ever
  // appear and the "cases connected to Us" panel's own case narrowing (which
  // reads ``case=`` off that same activeAnchor via withCaseId(), and which
  // services.connections_of_client applies on the backend — see its own
  // docstring) would have nothing to narrow by. Calling enterCaseMode(c)
  // with the fetched row reproduces exactly what picking that case out of
  // the case-mode dropdown does: anchored on the case's own real effective
  // role (``c.label``, not a hard-coded 'us'), caseMeta set, banner updated,
  // and the narrowing ``case_id`` flowing on every subsequent fetch this
  // anchor makes.
  if (typeof CFG.deepLinkCaseId === 'number') {
    get(CFG.allCasesSearchUrl, { case_id: CFG.deepLinkCaseId }).then(function (data) {
      if (!data || !data.ok || !data.cases || data.cases.length !== 1) { return; }
      if (typeof enterCaseMode !== 'function') { return; }
      enterCaseMode(data.cases[0]);
    }).catch(function () {});
  }
})();
