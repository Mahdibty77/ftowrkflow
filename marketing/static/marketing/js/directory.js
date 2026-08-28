/* Marketing — the entity directory: search/create per field, and the
 * "light up" report a selection produces on every other field.
 *
 * State lives entirely in this closure (`current`, the focused entity) —
 * every other view of it is a re-render from the server's own answer to
 * "what is connected to this," never a locally-guessed diff. That is what
 * keeps a rapid sequence of clicks (focus, link, unlink, refocus) from ever
 * showing a card lit for a connection that no longer exists.
 */
(function () {
  'use strict';

  var CFG = window.FT_MARKETING_DIR;
  var root = document.getElementById('dirRoot');
  if (!root || !CFG) { return; }

  var fieldsDataEl = document.getElementById('dirFieldsData');
  var FIELDS = fieldsDataEl ? JSON.parse(fieldsDataEl.textContent) : [];
  var FIELD_LABEL = {};
  FIELDS.forEach(function (f) { FIELD_LABEL[f.key] = f.label; });

  var grid = document.getElementById('dirGrid');
  var linesSvg = document.getElementById('dirLines');
  var focusBanner = document.getElementById('dirFocus');
  var focusFieldEl = focusBanner.querySelector('[data-focus-field]');
  var focusNameEl = focusBanner.querySelector('[data-focus-name]');
  var focusClearBtn = focusBanner.querySelector('[data-focus-clear]');

  var cards = {};
  Array.prototype.forEach.call(grid.querySelectorAll('.dir-card'), function (card) {
    cards[card.dataset.field] = card;
  });

  var current = null;   // {id, field, name} — the focused entity, or null
  var connSeq = 0;       // drops a stale connections response

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

  // ---- per-card search / create ------------------------------------------ //
  function wireCard(field, card) {
    var input = card.querySelector('[data-field-input]');
    var dropdown = card.querySelector('[data-dropdown]');
    var debounce = null;
    var mySeq = 0;

    function close() {
      dropdown.hidden = true;
      dropdown.innerHTML = '';
    }

    function renderOptions(query, entities) {
      dropdown.innerHTML = '';
      var exact = entities.some(function (e) { return e.name === query; });

      entities.forEach(function (e) {
        var opt = document.createElement('div');
        opt.className = 'dir-opt';
        opt.textContent = e.name;
        opt.setAttribute('dir', 'auto');
        opt.addEventListener('mousedown', function (ev) {
          ev.preventDefault();
          input.value = '';
          close();
          choose(field, e);
        });
        dropdown.appendChild(opt);
      });

      if (query && !exact) {
        var add = document.createElement('div');
        add.className = 'dir-opt is-new';
        add.textContent = '+ Add "' + query + '"';
        add.addEventListener('mousedown', function (ev) {
          ev.preventDefault();
          input.value = '';
          close();
          createAndChoose(field, query);
        });
        dropdown.appendChild(add);
      }

      if (!entities.length && !query) {
        var empty = document.createElement('div');
        empty.className = 'dir-opt-empty';
        empty.textContent = 'No values registered yet — type a name to add one.';
        dropdown.appendChild(empty);
      }

      dropdown.hidden = dropdown.children.length === 0;
    }

    function search(query) {
      var seq = ++mySeq;
      get(CFG.searchUrl, { field: field, q: query }).then(function (data) {
        if (seq !== mySeq || !data.ok) { return; }
        renderOptions(query, data.entities);
      });
    }

    input.addEventListener('input', function () {
      var query = input.value.trim();
      window.clearTimeout(debounce);
      debounce = window.setTimeout(function () { search(query); }, 200);
    });
    input.addEventListener('focus', function () { search(input.value.trim()); });
    input.addEventListener('blur', function () { window.setTimeout(close, 150); });
    input.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape') { close(); input.blur(); }
    });
  }

  Object.keys(cards).forEach(function (field) { wireCard(field, cards[field]); });

  function bumpCount(field, delta) {
    var el = cards[field] && cards[field].querySelector('[data-count]');
    if (!el) { return; }
    var n = (parseInt(el.textContent, 10) || 0) + delta;
    el.textContent = String(Math.max(0, n));
  }

  function createAndChoose(field, name) {
    post(CFG.createUrl, { field: field, name: name }).then(function (data) {
      if (!data.ok) { return; }
      bumpCount(field, 1);
      choose(field, data.entity);
    });
  }

  // A selection means one of two things, depending on whether a focus is
  // already held: with nothing focused it STARTS a browsing session; with a
  // focus already held, picking a value in a DIFFERENT field links it to that
  // focus immediately (the owner's own example — register an investor, then
  // pick a subcontractor to connect it to). Picking within the field that is
  // already the focus simply moves the focus to that new value.
  function choose(field, entity) {
    if (!current || field === current.field) {
      focus(entity);
      return;
    }
    post(CFG.linkUrl, { a_id: current.id, b_id: entity.id }).then(function (data) {
      if (data.ok) { refreshConnections(); }
    });
  }

  function focus(entity) {
    current = entity;
    focusFieldEl.textContent = FIELD_LABEL[entity.field] || entity.field;
    focusNameEl.textContent = entity.name;
    focusBanner.hidden = false;
    refreshConnections();
  }

  function clearFocus() {
    current = null;
    focusBanner.hidden = true;
    Object.keys(cards).forEach(function (field) {
      var card = cards[field];
      card.classList.remove('is-focused', 'is-lit', 'is-dim');
      var lit = card.querySelector('[data-lit]');
      lit.hidden = true;
      lit.innerHTML = '';
    });
    drawLines([], null);
  }
  focusClearBtn.addEventListener('click', clearFocus);

  function unlink(otherId) {
    if (!current) { return; }
    post(CFG.unlinkUrl, { a_id: current.id, b_id: otherId }).then(function (data) {
      if (data.ok) { refreshConnections(); }
    });
  }

  function refreshConnections() {
    if (!current) { return; }
    var seq = ++connSeq;
    get(CFG.connectionsUrl, { entity_id: current.id }).then(function (data) {
      if (seq !== connSeq || !data.ok) { return; }
      render(data);
    });
  }

  function render(data) {
    var litByField = {};
    data.connections.forEach(function (g) { litByField[g.field] = g; });

    Object.keys(cards).forEach(function (field) {
      var card = cards[field];
      var lit = card.querySelector('[data-lit]');
      card.classList.remove('is-focused', 'is-lit', 'is-dim');
      lit.hidden = true;
      lit.innerHTML = '';
    });

    var focusCard = cards[data.entity.field];
    if (focusCard) { focusCard.classList.add('is-focused'); }

    Object.keys(cards).forEach(function (field) {
      if (field === data.entity.field) { return; }
      var card = cards[field];
      var group = litByField[field];
      if (!group) {
        card.classList.add('is-dim');
        return;
      }
      card.classList.add('is-lit');
      var lit = card.querySelector('[data-lit]');
      lit.hidden = false;

      var count = document.createElement('div');
      count.className = 'dir-lit-count';
      count.textContent = group.entities.length + (group.entities.length === 1 ? ' connection' : ' connections');
      lit.appendChild(count);

      var chips = document.createElement('div');
      chips.className = 'dir-lit-chips';
      group.entities.forEach(function (e) {
        chips.appendChild(buildChip(e));
      });
      lit.appendChild(chips);
    });

    window.requestAnimationFrame(function () {
      drawLines(data.connections.map(function (g) { return g.field; }), data.entity.field);
    });
  }

  function buildChip(entity) {
    var chip = document.createElement('span');
    chip.className = 'dir-chip';

    var name = document.createElement('span');
    name.className = 'dir-chip-name';
    name.textContent = entity.name;
    name.setAttribute('dir', 'auto');
    name.title = 'Focus on this value';
    name.addEventListener('click', function () { focus(entity); });
    chip.appendChild(name);

    var x = document.createElement('button');
    x.type = 'button';
    x.className = 'dir-chip-x';
    x.setAttribute('aria-label', 'Unlink');
    x.textContent = '×';
    x.addEventListener('click', function (ev) { ev.stopPropagation(); unlink(entity.id); });
    chip.appendChild(x);

    return chip;
  }

  // ---- the connecting lines ------------------------------------------------
  // A straight line reads as noise where it has to cross a row of cards
  // between the two it actually connects, so each line bows upward slightly
  // (a single quadratic curve) — enough to separate a line from the row of
  // card borders it passes over without turning the chart into a tangle.
  function drawLines(litFields, focusField) {
    linesSvg.innerHTML = '';
    if (!focusField || !litFields.length) { return; }
    var wrap = grid.parentElement;
    var wrapRect = wrap.getBoundingClientRect();
    linesSvg.setAttribute('width', wrapRect.width);
    linesSvg.setAttribute('height', wrapRect.height);
    linesSvg.setAttribute('viewBox', '0 0 ' + wrapRect.width + ' ' + wrapRect.height);

    var focusCard = cards[focusField];
    if (!focusCard) { return; }
    var fRect = focusCard.getBoundingClientRect();
    var fx = fRect.left - wrapRect.left + fRect.width / 2;
    var fy = fRect.top - wrapRect.top + fRect.height / 2;

    var ns = 'http://www.w3.org/2000/svg';
    litFields.forEach(function (field) {
      var card = cards[field];
      if (!card) { return; }
      var r = card.getBoundingClientRect();
      var x = r.left - wrapRect.left + r.width / 2;
      var y = r.top - wrapRect.top + r.height / 2;
      var mx = (fx + x) / 2;
      var my = (fy + y) / 2 - 22;
      var path = document.createElementNS(ns, 'path');
      path.setAttribute('d', 'M' + fx + ' ' + fy + ' Q' + mx + ' ' + my + ' ' + x + ' ' + y);
      path.setAttribute('class', 'dir-line');
      linesSvg.appendChild(path);
    });
  }

  window.addEventListener('resize', function () {
    if (!current) { return; }
    window.requestAnimationFrame(function () {
      var lit = Object.keys(cards).filter(function (f) { return cards[f].classList.contains('is-lit'); });
      drawLines(lit, current.field);
    });
  });
})();
