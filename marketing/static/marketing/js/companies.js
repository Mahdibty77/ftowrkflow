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

  var panelOverlay = document.getElementById('mcPanelOverlay');
  var panelTitle = document.getElementById('mcPanelTitle');
  var panelClose = document.getElementById('mcPanelClose');
  var panelList = document.getElementById('mcPanelList');

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
      labelListCache = data.companies;
      renderRows(filterLocal(labelListCache, searchInput.value), searchInput.value);
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
    row.className = 'rc-row mc-row';

    var name = document.createElement('span');
    name.className = 'rc-row-name';
    name.textContent = company.name;
    name.setAttribute('dir', 'auto');
    row.appendChild(name);

    if (company.source) {
      // A label-tab row: the label itself is implied by the open tab, so
      // only its case badge(s) and its own remove control need rendering.
      if (company.source === 'case') {
        (company.case_numbers || []).forEach(function (docNo) {
          var badge = document.createElement('span');
          badge.className = 'rc-row-badge';
          badge.textContent = 'via case ' + docNo;
          row.appendChild(badge);
        });
      }
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
        row.appendChild(xBtn);
      }
    } else if (company.labels && company.labels.length) {
      // The "All" tab: no single label is implied here, so every label the
      // company currently holds gets its own small chip (name + case
      // badge(s) + its own independent remove control) — a company can
      // carry several at once, and each has to stay separately editable.
      company.labels.forEach(function (entry) {
        var chip = document.createElement('span');
        chip.className = 'mc-chip';
        chip.textContent = LABEL_TEXT[entry.label] || entry.label;
        row.appendChild(chip);
        if (entry.source === 'case') {
          (entry.case_numbers || []).forEach(function (docNo) {
            var badge = document.createElement('span');
            badge.className = 'rc-row-badge';
            badge.textContent = 'via case ' + docNo;
            row.appendChild(badge);
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
          row.appendChild(xBtn2);
        }
      });
    }

    row.addEventListener('click', function (ev) {
      if (ev.target.closest && ev.target.closest('.rc-row-remove')) { return; }
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
  // ------------------------------------------------------------------------
  var panelSeq = 0;
  var panelClient = null;

  function closePanel() {
    panelOverlay.hidden = true;
    panelClient = null;
  }
  panelClose.addEventListener('click', closePanel);
  panelOverlay.addEventListener('click', function (ev) { if (ev.target === panelOverlay) { closePanel(); } });
  document.addEventListener('keydown', function (ev) { if (ev.key === 'Escape' && !panelOverlay.hidden) { closePanel(); } });

  function openPanel(company) {
    panelSeq += 1;
    panelClient = { id: company.id, name: company.name, code: company.code };
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
    var byKey = {};
    activeLabels.forEach(function (l) { byKey[l.label] = l; });
    panelList.innerHTML = '';
    LABELS.forEach(function (labelDef) {
      panelList.appendChild(buildPanelRow(labelDef, byKey[labelDef.key]));
    });
  }

  function buildPanelRow(labelDef, entry) {
    var row = document.createElement('label');
    row.className = 'rc-row mc-panel-row';

    var checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = !!entry;
    // Three states only (the owner's own rule): a case-derived label is
    // always locked — it is never editable from here, only via Commercial —
    // regardless of whether a removable manual layer also sits under it; a
    // manual label this viewer does not own is locked too, the same
    // "cannot remove what you don't own" rule the row list's own × follows;
    // everything else (a manual label this viewer owns, or no label at all)
    // stays enabled.
    var locked;
    if (!entry) { locked = false; }
    else if (entry.source === 'case') { locked = true; }
    else { locked = !entry.removable; }
    checkbox.disabled = !CAN_EDIT || locked;
    row.appendChild(checkbox);

    var text = document.createElement('span');
    text.className = 'mc-panel-text';
    text.textContent = labelDef.label_fa;
    row.appendChild(text);

    if (entry && entry.source === 'case') {
      var note = document.createElement('span');
      note.className = 'rc-row-badge';
      note.textContent = 'via case ' + (entry.case_numbers || []).join(', ');
      row.appendChild(note);
    } else if (entry && !entry.removable) {
      var note2 = document.createElement('span');
      note2.className = 'rc-row-badge';
      note2.textContent = 'tagged by another user';
      row.appendChild(note2);
    }

    checkbox.addEventListener('change', function () {
      var add = checkbox.checked;
      var client = panelClient;
      var seq = panelSeq;
      checkbox.disabled = true;
      // Always re-ask the server for both views rather than hand-patch this
      // one checkbox's state locally — the same "never trust a
      // locally-guessed diff" rule chart_interact.js's own × handler follows
      // (it calls fetchLabelCompanies() again rather than removing the row
      // itself), and it means a failed toggle just shows back its own
      // unchanged truth instead of needing its own revert/error path.
      toggleLabel(client.id, labelDef.key, add, function () {
        if (seq !== panelSeq || panelClient !== client) { return; }
        refreshCurrentList();
        loadPanel();
      });
    });

    return row;
  }

  // ------------------------------------------------------------------------
  fetchAll('');
})();
