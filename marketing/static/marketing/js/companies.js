/* Marketing — the Companies tab: a flat, searchable/filterable directory of
 * every Client, each one showing its current label(s) as small tags and a
 * panel that edits a single company's full twelve-label set at a glance.
 *
 * Two list-fetching paths, on purpose, rather than one shared query.
 * client_search (the "All" tab) returns each company with a ``labels``
 * ARRAY — every label it currently holds, batched server-side in a constant
 * number of queries (marketing/services.py::labels_for_clients), not one
 * request per row. label_companies (a label tab) is already scoped to ONE
 * label and returns the single {source, also_manual, removable,
 * case_numbers} shape for THAT label directly on each company, since the
 * label itself is implied by which tab is open and does not need repeating
 * as a chip. buildRow() below renders whichever shape it is handed.
 *
 * The label-editing panel (point 4) always asks client_connections fresh
 * when it opens — the same "never trust a locally-guessed diff" discipline
 * chart_interact.js uses for its own modal — so it is never out of date with
 * whatever the row list last showed, even if that list is a few seconds
 * stale.
 */
(function () {
  'use strict';

  var CFG = window.FT_MARKETING_COMPANIES;
  var root = document.getElementById('mcRoot');
  if (!root || !CFG) { return; }

  var CAN_EDIT = !!CFG.canEdit;

  var labelsDataEl = document.getElementById('mcLabelsData');
  var LABELS = labelsDataEl ? JSON.parse(labelsDataEl.textContent) : [];
  var LABEL_TEXT = {};
  LABELS.forEach(function (l) { LABEL_TEXT[l.key] = l.label_fa; });

  var searchInput = document.getElementById('mcSearchInput');
  var tabsEl = document.getElementById('mcTabs');
  var listEl = document.getElementById('mcList');

  // The filter tab strip's own text is English-only now (LTR) — every OTHER
  // Persian spot in this tab (company names, the chips'/panel's own label
  // text via LABEL_TEXT above) stays exactly as RTL as it already was; this
  // is scoped to the tab strip alone. Short, unambiguous forms of each key's
  // own English abbreviation (see rolechart.py's SLOTS), not the long
  // abbreviation text itself — that stays each tab's title/tooltip, set by
  // the template — since a full "DESIGN CONSULTANT — FEED / DED" reads fine
  // as a tooltip but not as a tab label. The template still renders
  // label_fa as each tab's initial text (a plain no-JS fallback); this swaps
  // it for the short English form as soon as the script runs — which, since
  // this <script> tag sits after the tab markup with no defer/async, same as
  // every other DOM read in this file, happens before the page is first
  // shown to begin with.
  var SHORT_TAB_TEXT = {
    sponsor: 'Sponsor',
    owner: 'Owner',
    pmt: 'PMT',
    mc: 'MC',
    licensor: 'Licensor',
    design: 'Design',
    supervision: 'Supervision',
    c: 'Construction',
    p: 'Procurement',
    pc: 'Proc. + Constr.',
    epc: 'EPC',
    sub: 'Subcontractor'
  };
  // The visible label text now lives in the .ast-w span (change 3's reuse of
  // the archive page's own .archive-status-tab markup — .ast-label wraps one
  // .ast-w per word, and a tab title here is always one word) rather than
  // the old bespoke .mc-tab-label.
  Array.prototype.forEach.call(tabsEl.querySelectorAll('.mc-tab[data-label]'), function (btn) {
    var key = btn.getAttribute('data-label');
    if (!key) { return; } // "All" already reads "All"
    var span = btn.querySelector('.ast-w');
    if (span) { span.textContent = SHORT_TAB_TEXT[key] || LABEL_TEXT[key] || key; }
  });

  var panelOverlay = document.getElementById('mcPanelOverlay');
  var panelTitle = document.getElementById('mcPanelTitle');
  var panelClose = document.getElementById('mcPanelClose');
  var panelList = document.getElementById('mcPanelList');
  var panelActions = document.getElementById('mcPanelActions');
  var panelActionsCount = document.getElementById('mcPanelActionsCount');
  var panelConfirmBtn = document.getElementById('mcPanelConfirm');
  var panelCancelBtn = document.getElementById('mcPanelCancel');

  // ---- fetch helpers — identical shape to chart_interact.js's own -------- //
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
  function toggleLabel(clientId, label, add, done) {
    post(CFG.labelToggleUrl, { client_id: clientId, label: label, add: add ? 1 : 0 })
      .then(function (data) { if (done) { done(!!data.ok); } });
  }

  // ------------------------------------------------------------------------
  // List state. ``currentTab`` is '' for "All" (client_search-backed) or a
  // label key (label_companies-backed). ``labelListCache`` holds the label
  // tab's own last-fetched full list so the search box can filter it
  // locally, instantly, the same "no network round trip per keystroke" way
  // the chart modal's own search already works for a label's company list.
  // ------------------------------------------------------------------------
  var currentTab = '';
  var labelListCache = [];
  var listSeq = 0;          // drops a stale fetch response
  var searchDebounce = null;
  var SEARCH_DEBOUNCE_MS = 200;

  function fetchAll(query) {
    var seq = ++listSeq;
    get(CFG.clientSearchUrl, { q: query || '' }).then(function (data) {
      if (seq !== listSeq || !data.ok) { return; }
      renderRows(data.clients, query);
    });
  }

  function fetchLabel(label) {
    var seq = ++listSeq;
    get(CFG.labelCompaniesUrl, { label: label }).then(function (data) {
      if (seq !== listSeq || !data.ok) { return; }
      // A label tab's own list, unlike "All", is sorted by its bare case
      // count, most to least — see buildRow's label-tab branch, which shows
      // that same count in place of "All"'s per-label chips/badges — ties
      // broken by name so the order stays stable and predictable rather than
      // shuffling on every re-fetch.
      labelListCache = sortByCaseCountDesc(data.companies);
      renderRows(filterLocal(labelListCache, searchInput.value), searchInput.value);
    });
  }

  function sortByCaseCountDesc(companies) {
    return companies.slice().sort(function (a, b) {
      var ca = (a.case_numbers || []).length;
      var cb = (b.case_numbers || []).length;
      if (ca !== cb) { return cb - ca; }
      return a.name.localeCompare(b.name);
    });
  }

  function filterLocal(list, q) {
    q = (q || '').trim().toLowerCase();
    if (!q) { return list; }
    return list.filter(function (c) { return c.name.toLowerCase().indexOf(q) !== -1; });
  }

  function refreshCurrentList() {
    if (currentTab === '') { fetchAll(searchInput.value); }
    else { fetchLabel(currentTab); }
  }

  // ---- the row list -------------------------------------------------------
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

    // "+ Add" only when there is text to add and it does not already name a
    // row in view — same rule the marketing entity directory used
    // (directory.js's own ``exact`` check) before this tab replaced it, and
    // only offered to a viewer who could actually use it.
    if (CAN_EDIT && q) {
      var qLower = q.toLowerCase();
      var exact = companies.some(function (c) { return c.name.toLowerCase() === qLower; });
      if (!exact) { listEl.appendChild(buildAddRow(q)); }
    }
  }

  function buildRow(company) {
    var row = document.createElement('div');
    // The All tab (client_search's ``labels``-array shape) gets the real
    // two-column table (change 1): .mc-row-all switches the row from the
    // label tab's flex split to a CSS grid whose column widths are set once
    // on the row CLASS rather than derived per-row from that row's own
    // content, so the name column and tags column line up at the same x
    // across every row regardless of how many labels any one company holds.
    // A label tab's own row (company.source truthy) is a different, already-
    // correct layout and keeps the plain flex split untouched.
    var isAllTab = !company.source;
    row.className = 'rc-row mc-row' + (isAllTab ? ' mc-row-all' : '');

    // Two explicit zones, not one flowing line of chips/badges next to the
    // name (change 1): the company NAME (often Persian, RTL) gets its own
    // container on the right, its LABEL TAGS get their own container on the
    // left, laid out opposite ends by the CSS (.mc-row-name / .mc-row-tags)
    // rather than everything appended as flat siblings.
    var tags = document.createElement('div');
    tags.className = 'mc-row-tags';

    if (company.source) {
      // A label-tab row: the label itself is implied by the open tab, so
      // only a bare case count and its own remove control need rendering.
      // No more per-case "via case DOC_NO" badges here (change 4) — a plain
      // number (0 for a company that only holds a manual tag, no cases)
      // stands in for them; sorting that list by this same count, most to
      // least, is fetchLabel's job, not this row's.
      var count = document.createElement('span');
      count.className = 'mc-row-count';
      var caseCount = (company.case_numbers || []).length;
      count.textContent = String(caseCount);
      count.setAttribute('aria-label', caseCount + (caseCount === 1 ? ' case' : ' cases'));
      tags.appendChild(count);
      // Same rule chart_interact.js's buildLabelRow uses: a manual layer
      // (whether or not it happens to be the label's WINNING source right
      // now) gets an × only when THIS viewer actually owns it.
      var canRemove = (company.source === 'manual' || company.also_manual) && company.removable;
      if (CAN_EDIT && canRemove) {
        var xBtn = document.createElement('button');
        xBtn.type = 'button';
        xBtn.className = 'rc-row-remove';
        xBtn.setAttribute('aria-label', 'Remove ' + company.name);
        xBtn.textContent = '×';
        xBtn.addEventListener('click', function (ev) {
          ev.stopPropagation();
          toggleLabel(company.id, currentTab, 0, function (ok) { if (ok) { refreshCurrentList(); } });
        });
        tags.appendChild(xBtn);
      }
    } else if (company.labels && company.labels.length) {
      // The "All" tab: no single label is implied here, so every label the
      // company currently holds gets its own small chip (name + case
      // badge(s) + its own independent remove control) — a company can
      // carry several at once, and each has to stay separately editable.
      // (Unaffected by change 4 — that bare-count rule is for a single
      // label tab's own list only.)
      company.labels.forEach(function (entry) {
        var chip = document.createElement('span');
        chip.className = 'mc-chip';
        chip.textContent = LABEL_TEXT[entry.label] || entry.label;
        tags.appendChild(chip);
        if (entry.source === 'case') {
          (entry.case_numbers || []).forEach(function (docNo) {
            var badge = document.createElement('span');
            badge.className = 'rc-row-badge';
            badge.textContent = 'via case ' + docNo;
            tags.appendChild(badge);
          });
        }
        var entryRemovable = (entry.source === 'manual' || entry.also_manual) && entry.removable;
        if (CAN_EDIT && entryRemovable) {
          var xBtn2 = document.createElement('button');
          xBtn2.type = 'button';
          xBtn2.className = 'rc-row-remove';
          xBtn2.setAttribute('aria-label', 'Remove ' + chip.textContent + ' from ' + company.name);
          xBtn2.textContent = '×';
          xBtn2.addEventListener('click', function (ev) {
            ev.stopPropagation();
            toggleLabel(company.id, entry.label, 0, function (ok) { if (ok) { refreshCurrentList(); } });
          });
          tags.appendChild(xBtn2);
        }
      });
    }

    var nameWrap = document.createElement('div');
    nameWrap.className = 'mc-row-name';
    var name = document.createElement('span');
    name.className = 'rc-row-name';
    name.textContent = company.name;
    name.setAttribute('dir', 'auto');
    nameWrap.appendChild(name);

    // Tags first, name second — in a plain left-to-right flex row that puts
    // the first child on the left, this alone gives the tags zone the left
    // side and the name zone the right side (see this section's own CSS)
    // without needing any dir/rtl trickery on the row itself. The "All" tab's
    // own two-column grid (.mc-row-all) wants the OPPOSITE visual order —
    // name on the left, tags on the right, matching cases/client_list.html's
    // own Name-before-Labels column order — so that row reverses which
    // element it appends first instead of touching `dir`, which would also
    // flip the Persian name text's own reading direction. See .mc-row-all's
    // own comment in rolechart.css for the matching grid-column swap.
    if (isAllTab) {
      row.appendChild(nameWrap);
      row.appendChild(tags);
    } else {
      row.appendChild(tags);
      row.appendChild(nameWrap);
    }

    row.addEventListener('click', function (ev) {
      if (ev.target.closest && ev.target.closest('.rc-row-remove')) { return; }
      // Change 5: an admin/GM viewer never gets the label-editing panel from
      // this list — a click instead drops them straight into the case
      // archive, pre-filtered to this one company. Everyone else keeps
      // today's behaviour unchanged. The "Name (CODE)" format has to match
      // cases/views.py::archive's own f_clients option text character for
      // character, since that page filters by matching this string against
      // the options it built for its own dropdown, not by client id.
      if (CFG.isAdminTier) {
        window.location.href = CFG.archiveUrl + '?fclient=' +
          encodeURIComponent(company.name + ' (' + company.code + ')');
        return;
      }
      openPanel(company);
    });
    return row;
  }

  function buildAddRow(name) {
    var row = document.createElement('div');
    row.className = 'rc-row mc-row-add';
    row.textContent = '+ Add "' + name + '"';
    row.addEventListener('click', function () {
      post(CFG.clientCreateUrl, { name: name }).then(function (data) {
        if (!data.ok) { return; }
        searchInput.value = '';
        refreshCurrentList();
        openPanel(data.client);
      });
    });
    return row;
  }

  // ---- the search box ------------------------------------------------------
  searchInput.addEventListener('input', function () {
    var q = searchInput.value;
    if (currentTab === '') {
      window.clearTimeout(searchDebounce);
      searchDebounce = window.setTimeout(function () { fetchAll(q); }, SEARCH_DEBOUNCE_MS);
    } else {
      // No network round trip here — the label tab's own list is already in
      // hand (labelListCache); this is a plain, instant local filter, the
      // same behaviour the chart modal's own label search uses.
      renderRows(filterLocal(labelListCache, q), q);
    }
  });

  // ---- the filter tabs -----------------------------------------------------
  Array.prototype.forEach.call(tabsEl.querySelectorAll('.mc-tab'), function (btn) {
    btn.addEventListener('click', function () {
      var label = btn.getAttribute('data-label');
      if (label === currentTab) { return; }
      currentTab = label;
      Array.prototype.forEach.call(tabsEl.querySelectorAll('.mc-tab'), function (b) {
        var active = b === btn;
        b.classList.toggle('is-active', active);
        b.setAttribute('aria-selected', active ? 'true' : 'false');
      });
      window.clearTimeout(searchDebounce);
      if (currentTab === '') { fetchAll(searchInput.value); }
      else { labelListCache = []; fetchLabel(currentTab); }
    });
  });

  // ------------------------------------------------------------------------
  // The label-editing panel — one company, all twelve labels, at a glance.
  //
  // Change 4: clicking an editable row no longer commits straight to the
  // server — it only stages a local intent. ``panelStaged`` is that intent,
  // {labelKey: true|false} (the desired end state), keyed off this ONE
  // load/open cycle the same way chart_interact.js's own attach wizard keys
  // its own ``wizard.staged`` off one armed session: reset to {} whenever the
  // panel opens for a (possibly new) company, whenever it closes, and
  // whenever Cancel discards it — never carried across those boundaries.
  // ``panelActiveLabels`` is the last server answer loadPanel saw, kept
  // around only so Cancel can redraw the panel back to it without a second
  // round trip.
  // ------------------------------------------------------------------------
  var panelSeq = 0;
  var panelClient = null;
  var panelStaged = {};
  var panelActiveLabels = [];

  function updatePanelFooter() {
    var n = Object.keys(panelStaged).length;
    panelActions.hidden = n === 0;
    panelActionsCount.textContent = n + (n === 1 ? ' change' : ' changes');
  }

  function closePanel() {
    panelOverlay.hidden = true;
    panelClient = null;
    panelStaged = {};
  }
  panelClose.addEventListener('click', closePanel);
  panelOverlay.addEventListener('click', function (ev) { if (ev.target === panelOverlay) { closePanel(); } });
  document.addEventListener('keydown', function (ev) { if (ev.key === 'Escape' && !panelOverlay.hidden) { closePanel(); } });

  panelCancelBtn.addEventListener('click', function () {
    // Discard every staged intent and redraw from the last server answer —
    // nothing was ever sent, so there is nothing to undo server-side.
    panelStaged = {};
    updatePanelFooter();
    renderPanel(panelActiveLabels);
  });

  panelConfirmBtn.addEventListener('click', function () {
    var keys = Object.keys(panelStaged);
    if (!keys.length) { return; }
    var client = panelClient;
    var seq = panelSeq;
    var staged = panelStaged;
    panelConfirmBtn.disabled = true;
    panelCancelBtn.disabled = true;
    // One fetch per staged label, run concurrently — post() already returns
    // a promise (see its .then() usage in toggleLabel above) — then commit
    // exactly once every one of them has answered.
    var requests = keys.map(function (key) {
      return post(CFG.labelToggleUrl, { client_id: client.id, label: key, add: staged[key] ? 1 : 0 });
    });
    Promise.all(requests).then(function () {
      panelConfirmBtn.disabled = false;
      panelCancelBtn.disabled = false;
      if (seq !== panelSeq || panelClient !== client) { return; }
      // Same refresh toggleLabel's own instant-commit callback always did —
      // then, only once the server matches what was just staged, drop the
      // staged tracking for this load cycle.
      refreshCurrentList();
      loadPanel();
      panelStaged = {};
      updatePanelFooter();
    });
  });

  function openPanel(company) {
    panelSeq += 1;
    panelClient = { id: company.id, name: company.name, code: company.code };
    panelStaged = {};
    updatePanelFooter();
    panelTitle.textContent = company.name;
    panelTitle.setAttribute('dir', 'auto');
    panelList.innerHTML = '';
    panelOverlay.hidden = false;
    loadPanel();
  }

  function loadPanel() {
    var seq = panelSeq;
    var client = panelClient;
    get(CFG.clientConnectionsUrl, { client_id: client.id }).then(function (data) {
      if (seq !== panelSeq || !data.ok || panelClient !== client) { return; }
      renderPanel(data.labels);
    });
  }

  function renderPanel(activeLabels) {
    panelActiveLabels = activeLabels;
    var byKey = {};
    activeLabels.forEach(function (l) { byKey[l.label] = l; });
    panelList.innerHTML = '';
    LABELS.forEach(function (labelDef) {
      panelList.appendChild(buildPanelRow(labelDef, byKey[labelDef.key]));
    });
  }

  function buildPanelRow(labelDef, entry) {
    var checked = !!entry;
    // Three states only (the owner's own rule): a case-derived label is
    // always locked — it is never editable from here, only via Commercial —
    // regardless of whether a removable manual layer also sits under it; a
    // manual label this viewer does not own is locked too, the same
    // "cannot remove what you don't own" rule the row list's own × follows;
    // everything else (a manual label this viewer owns, or no label at all)
    // stays clickable. Only the INPUT ELEMENT and the visual language of
    // "checked" changed here (a tick mark + a tinted row instead of a native
    // checkbox) — which of these three states is togglable did not.
    var locked;
    if (!entry) { locked = false; }
    else if (entry.source === 'case') { locked = true; }
    else { locked = !entry.removable; }
    var clickable = CAN_EDIT && !locked;

    // No more <input type="checkbox"> — the row itself is the clickable
    // target (a plain div now, not a <label>, since there is no native
    // control left for a <label> to address) and carries its own
    // is-checked/is-locked look instead of a native checked/disabled one.
    var row = document.createElement('div');
    row.className = 'rc-row mc-panel-row';
    row.classList.toggle('is-checked', checked);
    row.classList.toggle('is-locked', !clickable);
    row.setAttribute('role', 'checkbox');
    row.setAttribute('aria-checked', checked ? 'true' : 'false');
    if (clickable) { row.tabIndex = 0; } else { row.setAttribute('aria-disabled', 'true'); }

    var check = document.createElement('span');
    check.className = 'mc-panel-check';
    check.setAttribute('aria-hidden', 'true');
    check.textContent = checked ? '✓' : '';
    row.appendChild(check);

    var text = document.createElement('span');
    text.className = 'mc-panel-text';
    text.textContent = labelDef.label_fa;
    row.appendChild(text);

    // No case-NUMBER text in this panel any more (change 2) — the row's own
    // is-locked/is-checked look (a tinted row + tick glyph, both above)
    // already communicates "fixed, not editable here" for a case-derived
    // label on its own; the actual case number belonged to Commercial's own
    // view of the case, not this popover.
    if (entry && entry.source !== 'case' && !entry.removable) {
      var note = document.createElement('span');
      note.className = 'rc-row-badge';
      note.textContent = 'tagged by another user';
      row.appendChild(note);
    }

    if (clickable) {
      // Change 4: a click only flips this row's own LOCAL tick state and
      // records the resulting intent on panelStaged — nothing reaches the
      // server until Confirm changes fires (see panelConfirmBtn above). If
      // the click lands back on the server's own current state (an add then
      // an un-add, say), the entry is dropped from panelStaged entirely
      // rather than kept as a no-op stage, the same rule chart_interact.js's
      // wizard.staged already follows for its own picking-mode checkbox —
      // so the footer's own change count only ever reflects real changes.
      var toggle = function () {
        var already = Object.prototype.hasOwnProperty.call(panelStaged, labelDef.key);
        var effectiveChecked = already ? panelStaged[labelDef.key] : checked;
        var next = !effectiveChecked;
        if (next === checked) { delete panelStaged[labelDef.key]; }
        else { panelStaged[labelDef.key] = next; }
        var stagedNow = Object.prototype.hasOwnProperty.call(panelStaged, labelDef.key);
        var nowChecked = stagedNow ? panelStaged[labelDef.key] : checked;
        row.classList.toggle('is-checked', nowChecked);
        row.classList.toggle('is-pending', stagedNow);
        row.setAttribute('aria-checked', nowChecked ? 'true' : 'false');
        check.textContent = nowChecked ? '✓' : '';
        updatePanelFooter();
      };
      row.addEventListener('click', toggle);
      row.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter' || ev.key === ' ') {
          ev.preventDefault();
          toggle();
        }
      });
    }

    return row;
  }

  // ------------------------------------------------------------------------
  fetchAll('');
})();
