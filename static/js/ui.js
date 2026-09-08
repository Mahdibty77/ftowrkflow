/* ===========================================================================
   Shared UI behaviours used across the workflow pages.
     1. data-combo  -> turns a <select> into a searchable, type-to-filter combo
     2. data-filter-table -> Excel-style instant multi-column filtering
     3. .confirm-action    -> reveals an inline confirm + optional comment box
                              before a routing action is actually submitted
   All three are progressive enhancements: if JS is off, the plain controls
   still work.
   =========================================================================== */
(function () {
  "use strict";

  // Persian/Arabic letter-variant folding for search, mirroring
  // core.persian_text.normalize_persian on the server. Same reasoning as
  // foldDigits below: this file is served without a charset, so nothing it
  // EXECUTES may depend on the file being decoded as UTF-8 -- done by code
  // point (\uXXXX escapes, pure ASCII in the source bytes) rather than
  // by a table of the letters themselves.
  //
  // Two steps, in the same order as the Python twin: (1) fold the Arabic
  // letter-variants to their Persian form, then (2) strip EVERY whitespace
  // character entirely (not collapsed to one space - removed), so spacing
  // differences (extra spaces, a missing space, tabs) can never make two
  // names compare as different. The result is comparison-only -- it is no
  // longer word-shaped, so it must never be displayed or re-inserted
  // anywhere, only compared against another normalizePersian()/
  // normalize_persian() result. Keep this in sync with
  // core/persian_text.py::normalize_persian.
  function normalizePersian(s) {
    return (s || "")
      .replace(/[\u0622\u0623\u0625\u0671]/g, "\u0627")
      .replace(/\u0643/g, "\u06a9")
      .replace(/[\u064a\u0649]/g, "\u06cc")
      .replace(/\s+/g, "");
  }
  /* ---------------------------------------------------------------- combobox */
  function buildCombo(select) {
    // Already upgraded: just refresh its options from the current <select>.
    // This lets callers repopulate the underlying <select> (e.g. the offer
    // builder's "Its value" list) and have the combo reflect the new options
    // instead of keeping the empty snapshot taken at first build.
    if (select.dataset.comboReady === "1") {
      if (typeof select._ftRefreshCombo === "function") select._ftRefreshCombo();
      return;
    }
    select.dataset.comboReady = "1";

    var wrap = document.createElement("div");
    wrap.className = "combo";
    var input = document.createElement("input");
    input.type = "text";
    input.className = "combo-input";
    input.autocomplete = "off";
    input.placeholder = select.getAttribute("data-placeholder") || "Type to search…";
    var list = document.createElement("div");
    list.className = "combo-list";

    select.style.display = "none";
    select.parentNode.insertBefore(wrap, select);
    wrap.appendChild(input);
    wrap.appendChild(list);
    wrap.appendChild(select);

    var isFilter = select.hasAttribute("data-filter-for");
    // Options are re-read from the live <select> so the combo can be refreshed
    // after its options change. Filter combos never list an empty "All …" row —
    // clearing the field (Delete/Backspace) means "no filter".
    var options = [];
    function readOptions() {
      options = Array.prototype.map.call(select.options, function (o) {
        return { value: o.value, label: o.textContent.trim(), code: o.getAttribute("data-code") || "" };
      }).filter(function (o) { return o.value !== ""; });
    }
    readOptions();

    function setFromValue() {
      var cur = options.filter(function (o) { return o.value === select.value; })[0];
      // Empty selection → blank input so the placeholder shows (no "All …" text).
      input.value = (cur && cur.value !== "") ? cur.label : "";
    }
    // A filter combo shows the filter that is IN FORCE. The archive answers its
    // filters on the server now and re-renders the page with the chosen <option>
    // marked selected, so a <select> that arrives carrying a value means "the
    // rows you are looking at are already narrowed by this". That value used to
    // be thrown away here, on the reasoning that a filter field starts empty —
    // true only while the browser did the filtering, and it cost two things once
    // the server took over: the field read "no filter" over a table that was
    // filtered (and the clear X stayed hidden), and, because emptying a field
    // that is already empty changes nothing, backspacing it away left the last
    // window on screen instead of fetching the whole list back. Keeping the
    // value is what makes clearing it mean something: setFromValue() prints its
    // label, the X lights, and clearFilterValue() has something to clear.
    setFromValue();

    // Public refresh hook: re-read the <select> options and repaint the field.
    select._ftRefreshCombo = function () {
      readOptions();
      setFromValue();
      if (list.classList.contains("open")) render(input.value);
    };

    function clearFilterValue() {
      if (!isFilter) return;
      var had = (select.value || "") !== "" || !!(input.value || "").trim();
      select.value = "";
      input.value = "";
      if (had) select.dispatchEvent(new Event("change", { bubbles: true }));
      syncClearBtn();
    }

    function syncClearBtn() {
      if (!clearBtn) return;
      var on = !!(input.value || "").trim() || !!(select.value || "").trim();
      clearBtn.hidden = !on;
    }

    var clearBtn = null;
    if (isFilter) {
      wrap.classList.add("combo-has-clear");
      clearBtn = document.createElement("button");
      clearBtn.type = "button";
      clearBtn.className = "filter-clear-x";
      clearBtn.title = "Clear filter";
      clearBtn.setAttribute("aria-label", "Clear filter");
      clearBtn.innerHTML = "&times;";
      clearBtn.hidden = true;
      clearBtn.addEventListener("mousedown", function (e) {
        e.preventDefault();
        e.stopPropagation();
      });
      clearBtn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        clearFilterValue();
        list.classList.remove("open");
        // Do not focus the input — focus would reopen the dropdown.
      });
      wrap.appendChild(clearBtn);
      syncClearBtn();
    }

    function render(filter) {
      var f = normalizePersian((filter || "").toLowerCase());
      list.innerHTML = "";
      var matches = options.filter(function (o) {
        return !f || normalizePersian(o.label.toLowerCase()).indexOf(f) !== -1 || (o.code && normalizePersian(o.code.toLowerCase()).indexOf(f) !== -1);
      });
      if (!matches.length) {
        var empty = document.createElement("div");
        empty.className = "combo-empty";
        empty.textContent = "No matches";
        list.appendChild(empty);
        return;
      }
      matches.forEach(function (o) {
        var row = document.createElement("div");
        row.className = "combo-opt";
        var name = document.createElement("span");
        name.textContent = o.label;
        row.appendChild(name);
        if (o.code) {
          var code = document.createElement("span");
          code.className = "code";
          code.textContent = o.code;
          row.appendChild(code);
        }
        row.addEventListener("mousedown", function (e) {
          e.preventDefault();
          select.value = o.value;
          input.value = o.label;
          list.classList.remove("open");
          select.dispatchEvent(new Event("change", { bubbles: true }));
          syncClearBtn();
        });
        list.appendChild(row);
      });
    }

    input.addEventListener("focus", function () { render(""); list.classList.add("open"); });
    input.addEventListener("input", function () {
      if (isFilter && !(input.value || "").trim()) {
        // The box is empty again, however it got there — one backspace at a
        // time, select-all-and-delete, or the last character of the word. Empty
        // means "no filter": drop the value the field was carrying (which is
        // what re-requests the unfiltered list, since the archive answers its
        // filters on the server and only a change on the <select> reaches it),
        // and show every option, which is the state a first click gives.
        clearFilterValue();
        render("");
        list.classList.add("open");
        return;
      }
      render(input.value);
      list.classList.add("open");
      syncClearBtn();
    });
    input.addEventListener("keydown", function (e) {
      if (!isFilter) return;
      // Delete and Backspace are deliberately NOT handled here. They used to be:
      // either key cancelled itself, emptied the whole field and shut the menu,
      // so one backspace threw away everything that had been typed. The field is
      // an ordinary text box — let the browser remove the one character (from
      // the middle of the text as readily as from the end, and the whole of a
      // selection), and the "input" handler above re-filters the menu against
      // whatever is left, or clears the filter once nothing is left.
      if (e.key === "Escape") {
        clearFilterValue();
        list.classList.remove("open");
      }
    });
    input.addEventListener("blur", function () {
      setTimeout(function () {
        list.classList.remove("open");
        if (isFilter && !(input.value || "").trim()) {
          clearFilterValue();
        } else {
          setFromValue();
          syncClearBtn();
        }
      }, 150);
    });

    // Keep clear button in sync when external code clears the select.
    var _prevRefresh = select._ftRefreshCombo;
    select._ftRefreshCombo = function () {
      if (typeof _prevRefresh === "function") _prevRefresh();
      else {
        readOptions();
        setFromValue();
      }
      syncClearBtn();
    };
  }

  document.querySelectorAll("select[data-combo]").forEach(buildCombo);
  // Expose so dynamically-created selects (e.g. the offer builder) can be
  // upgraded to the same searchable combo after they are inserted.
  window.FTBuildCombo = buildCombo;

  /* ----------------------------------------------------------- table filters */
  // A Jalali stamp reduced to the YYYY.MM.DD head that two of them compare on.
  // THE MIRROR of cases.services._archive_date_key — the two are a matched pair
  // and must be changed together. The server filters the rows and this pass
  // hides rows in the rendered table; whenever the two predicates disagree the
  // reader sees the difference as an empty table.
  //
  // Both sides of a date range arrive as text written by different hands: the
  // picker writes "1404-06-15 09:00" with hyphens, the Created cell prints
  // "1404.06.15 09:00" with dots. The comparison is lexicographic, so the
  // separator alone used to decide it ("." sorts after "-") — within one Jalali
  // year From matched every row and To matched none, which is the empty table.
  // Reducing both sides to the same shape is what makes it about the date.
  //
  // Only the DATE survives, never the clock: a To of "1404-06-15 09:00" still
  // has to keep that day's 21:30 rows, and dropping the time on both sides is
  // what keeps a one-day range meaning the whole day. Anything that is not
  // three numbers is handed back as-is, so a cell printing an em dash still
  // compares as it did.
  //
  // The server reads its three numbers with Python's int(), which accepts
  // Persian and Arabic-Indic digits as readily as ASCII ones; folding them here
  // first is what stops a date written in Persian numerals (reachable through a
  // hand-built URL — the date boxes themselves are read-only pickers) from
  // being a date to the server and a meaningless string to the browser, which
  // is the empty table all over again. Done by code point rather than by a
  // table of the digits themselves: this file is served without a charset, so
  // nothing it EXECUTES may depend on the file being decoded as UTF-8.
  function foldDigits(s) {
    var out = "";
    for (var i = 0; i < s.length; i++) {
      var cp = s.charCodeAt(i);
      if (cp >= 0x0660 && cp <= 0x0669) out += String(cp - 0x0660);        // Arabic-Indic
      else if (cp >= 0x06F0 && cp <= 0x06F9) out += String(cp - 0x06F0);   // Persian
      else out += s.charAt(i);
    }
    return out;
  }
  function pad0(digits, width) {
    var s = String(parseInt(digits, 10));
    while (s.length < width) s = "0" + s;
    return s;
  }
  function dateKey(value) {
    var head = (value || "").trim().split(" ")[0].slice(0, 10);
    var parts = foldDigits(head).split(/[-/.]/).filter(Boolean);
    var numeric = parts.length === 3 && parts.every(function (p) { return /^[0-9]+$/.test(p); });
    if (!numeric) return head;   // not a date: compares exactly as it always did
    return pad0(parts[0], 4) + "." + pad0(parts[1], 2) + "." + pad0(parts[2], 2);
  }

  // A table's empty-state line ("No cases in the archive yet.", "Your inbox is
  // empty.", "No requests yet.") is one cell spanning every column, not a row
  // of data. It has no Created cell to compare and no Document No. to search,
  // so every predicate judges it on "" and hides the one line that explains
  // why the table is bare — which is what a date range matching nothing used
  // to look like: a blank table with nothing saying so. It is also not a match
  // and must never reach the "N of M" count. This pass simply does not own
  // that row: it neither hides it nor shows it, so a table whose own script
  // toggles its placeholder keeps control of it.
  function isPlaceholderRow(tr) {
    return tr.cells.length === 1 && tr.cells[0].hasAttribute("colspan");
  }

  // A row that carries no filterable data of its own and must simply track
  // whichever real data row immediately precedes it — e.g. My Tasks' own
  // Reports tab, where a report that closed out a reminder gets a second,
  // decorative <tr> underneath it (a single <td colspan> strip showing that
  // reminder's Set/Due/Reported dates; see _my_tasks_reports.html). That
  // strip has the EXACT SAME single-cell-plus-colspan shape isPlaceholderRow
  // looks for, so without this it fell into that check and was silently
  // skipped by apply() below — never hidden, never shown, just left exactly
  // as it started (visible), even after its own data row above it was
  // filtered out. Opt in with data-follows-row so no EXISTING table (this is
  // a shared, global script) changes behaviour by accident; only a row that
  // deliberately asks to inherit its predecessor's visibility does.
  function isFollowerRow(tr) {
    return tr.hasAttribute("data-follows-row");
  }

  // <table data-filter-table> with inputs/selects carrying data-filter-col="N".
  document.querySelectorAll("[data-filter-table]").forEach(function (table) {
    var tbody = table.tBodies[0];
    if (!tbody) return;
    var controls = document.querySelectorAll('[data-filter-for="' + table.id + '"]');
    // Map header names -> column index so controls can target by name.
    var nameToCol = {};
    var headRow = table.tHead ? table.tHead.rows[0] : null;
    if (headRow) Array.prototype.forEach.call(headRow.cells, function (th, i) {
      nameToCol[(th.textContent || "").trim().toLowerCase()] = i;
    });
    function colOf(c) {
      var byName = c.getAttribute("data-filter-colname");
      if (byName != null && nameToCol[byName.trim().toLowerCase()] != null) return nameToCol[byName.trim().toLowerCase()];
      return parseInt(c.getAttribute("data-filter-col"), 10);
    }

    function apply() {
      var terms = [];
      controls.forEach(function (c) {
        var raw = (c.value || "").trim().toLowerCase();
        if (!raw) return;
        terms.push({ col: colOf(c), mode: c.getAttribute("data-filter-mode") || "contains", raw: raw,
                     parts: raw.split(",").map(function (s) { return s.trim(); }).filter(Boolean) });
      });
      // Tracks the last REAL data row's own shown/hidden verdict, purely so
      // a follower row (see isFollowerRow above) right after it can copy the
      // same verdict — checked BEFORE isPlaceholderRow below, since a
      // follower row and the table's own genuine empty-state row share the
      // identical single-cell-plus-colspan shape and must not be confused.
      var lastRowShown = true;
      Array.prototype.forEach.call(tbody.rows, function (tr) {
        if (isFollowerRow(tr)) {
          tr.style.display = lastRowShown ? "" : "none";
          return;
        }
        if (isPlaceholderRow(tr)) return;
        var show = terms.every(function (t) {
          var cell = tr.cells[t.col];
          var text = cell ? ((cell.getAttribute("data-fval") || cell.textContent) || "").trim().toLowerCase() : "";
          // Dates compare as STRINGS (Jalali Y.m.d sorts lexicographically) —
          // but only once dateKey() has put both sides in the same shape.
          if (t.mode === "gte") return dateKey(text) >= dateKey(t.raw);
          if (t.mode === "lte") return dateKey(text) <= dateKey(t.raw);
          if (t.mode === "equals") return text === t.raw;
          return t.parts.some(function (p) { return text.indexOf(p) !== -1; });
        });
        tr.style.display = show ? "" : "none";
        lastRowShown = show;
      });
      var counter = document.querySelector('[data-filter-count="' + table.id + '"]');
      if (counter) counter.textContent = Array.prototype.filter.call(tbody.rows, function (r) {
        return !isPlaceholderRow(r) && r.style.display !== "none";
      }).length;
    }
    function clearAll() {
      controls.forEach(function (c) {
        // Archive status tabs own this control — Clear filters must not reset it.
        if (c.getAttribute("data-archive-status-filter")) return;
        clearControl(c);
      });
      apply();
    }

    function clearControl(c) {
      if (c.tagName === "SELECT") {
        c.value = "";
        if (typeof c._ftRefreshCombo === "function") c._ftRefreshCombo();
        else {
          var comboInp = c.parentNode && c.parentNode.querySelector
            ? c.parentNode.querySelector(".combo-input") : null;
          if (comboInp) comboInp.value = "";
          var x = c.parentNode && c.parentNode.querySelector
            ? c.parentNode.querySelector(".filter-clear-x") : null;
          if (x) x.hidden = true;
        }
      } else {
        c.value = "";
        syncInputClearBtn(c);
      }
      try { c.dispatchEvent(new Event("input", { bubbles: true })); } catch (_e) {}
      try { c.dispatchEvent(new Event("change", { bubbles: true })); } catch (_e2) {}
    }

    function syncInputClearBtn(c) {
      var wrap = c.closest && c.closest(".filter-input-wrap");
      if (!wrap) return;
      var btn = wrap.querySelector(".filter-clear-x");
      if (btn) btn.hidden = !(c.value || "").trim();
    }

    function attachInputClear(c) {
      if (c.tagName === "SELECT") return; // combo handles its own clear X
      if (c.dataset.filterClearReady === "1") return;
      c.dataset.filterClearReady = "1";
      var wrap = document.createElement("div");
      wrap.className = "filter-input-wrap";
      c.parentNode.insertBefore(wrap, c);
      wrap.appendChild(c);
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "filter-clear-x";
      btn.title = "Clear filter";
      btn.setAttribute("aria-label", "Clear filter");
      btn.innerHTML = "&times;";
      btn.hidden = !(c.value || "").trim();
      btn.addEventListener("mousedown", function (e) { e.preventDefault(); });
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        clearControl(c);
        apply();
      });
      wrap.appendChild(btn);

      c.addEventListener("input", function () { syncInputClearBtn(c); });
      c.addEventListener("change", function () { syncInputClearBtn(c); });
      c.addEventListener("keydown", function (e) {
        if (e.key !== "Delete" && e.key !== "Backspace") return;
        if (!(c.value || "").trim()) return;
        e.preventDefault();
        clearControl(c);
        apply();
      });
    }

    controls.forEach(function (c) {
      attachInputClear(c);
      c.addEventListener("input", apply);
      c.addEventListener("change", apply);
    });
    document.querySelectorAll('[data-filter-clear="' + table.id + '"]').forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        clearAll();
      });
    });
    // Re-run the hiding pass on demand. A table whose rows all exist by the time
    // this runs never needs it, but the archive appends rows as the reader
    // scrolls and a row that arrived after the last pass has never been judged
    // against the filters — it would show through a filter that excludes it.
    table.ftApplyFilters = apply;
    apply();
  });

  /* --------------------------------------------------------- inline confirm */
  // A form with class "confirm-action" hides its real submit behind a small
  // confirm panel (with an optional comment), so even the first send is a
  // two-step action that never navigates to a separate page first.

  // ONE CONFIRMED ACTION AT A TIME.
  //
  // Confirm used to stay live for the whole time its POST was in flight, and
  // nothing else on the page moved either, so there was no sign at all that the
  // click had been taken: three clicks on one Confirm sent three POSTs, and on a
  // case sitting at CLOSED that wrote the same transition into the timeline
  // three times over.
  //
  // The server is what actually decides now — cases.views.transition takes the
  // case row's lock, re-reads it and re-asks the same permission rules before it
  // writes, so a second POST meets the first one's result and is turned away.
  // This guard is the other half of that: it keeps the page from sending the
  // second POST at all, so the reader gets a page that answers their click
  // instead of a refusal they never asked for. The lock is for the page and not
  // just for one button — while any confirmed action is unresolved, no confirmed
  // action may be started — because two DIFFERENT panels sent together (Burn and
  // Finalize) are the pair that produced "it came out of loading already final
  // approved". It costs nothing in ordinary use: the answer to that POST
  // replaces this page anyway.
  //
  // A SLOW SERVER IS THE CASE THIS EXISTS FOR, so waiting is never a reason to
  // let go. The first version of this guard handed the buttons back after 20
  // seconds "in case the submit never landed" — but a POST still in flight and a
  // POST that died look identical from here, and the owner's own report is a
  // server slow enough that the screen sits in loading. Re-arming on a timer
  // therefore re-opened exactly the gap it was added to close, in the one
  // situation that produced the complaint.
  //
  // So nothing here re-arms a button on its own. The two ways out are both real
  // evidence that the request is over:
  //   * the browser restored this page from the bfcache (Back) — the submit
  //     either finished or was abandoned, and this page is being reused;
  //   * the reader decides. After a long wait the panel says so and offers a
  //     reload, which throws this page away and shows what the case actually
  //     did — the honest answer, and one that cannot double-send anything.
  var confirmButtons = [];
  var confirmSending = false;
  var confirmStall = null;
  var confirmNote = null;

  function clearNote() {
    if (confirmStall) {
      clearTimeout(confirmStall);
      confirmStall = null;
    }
    if (confirmNote && confirmNote.parentNode) {
      confirmNote.parentNode.removeChild(confirmNote);
    }
    confirmNote = null;
  }

  function stallNote(active) {
    if (confirmNote || !active) return;
    var host = active.closest(".confirm-panel") || active.parentNode;
    if (!host) return;
    var note = document.createElement("div");
    note.className = "muted";
    note.setAttribute("role", "status");
    note.style.cssText = "font-size:.78rem;margin-top:.4rem;";
    note.innerHTML =
      "The server has not answered yet. Your action may still be going " +
      "through — do not send it again. " +
      '<button type="button" class="btn btn-sm btn-ghost" ' +
      'data-confirm-reload style="margin-top:.3rem">Reload this page</button>';
    note.querySelector("[data-confirm-reload]").addEventListener(
      "click", function () { window.location.reload(); });
    host.appendChild(note);
    confirmNote = note;
  }

  function confirmBusy(on, active) {
    confirmSending = on;
    confirmButtons.forEach(function (b) {
      b.disabled = on;
      b.innerHTML = (on && b === active)
        ? '<i class="fa-solid fa-circle-notch fa-spin"></i> Working…'
        : b._ftIdleLabel;
    });
    clearNote();
    // Still nothing after fifteen seconds: say so, rather than leaving a spinner
    // the reader has no way to read. The buttons stay disabled — this only adds
    // words and a way off the page.
    if (on) {
      confirmStall = setTimeout(function () { stallNote(active); }, 15000);
    }
  }

  // The Back button restores this page from the bfcache exactly as it was left,
  // mid-submit and disabled, whether or not that action actually happened.
  window.addEventListener("pageshow", function (ev) {
    if (ev.persisted && confirmSending) confirmBusy(false);
  });

  document.querySelectorAll("form.confirm-action").forEach(function (form) {
    var trigger = form.querySelector("[data-confirm-trigger]");
    if (!trigger) return;
    var panel = document.createElement("div");
    panel.className = "confirm-panel";
    panel.style.display = "none";
    panel.style.marginTop = ".5rem";
    var wantComment = form.hasAttribute("data-comment");
    var reqComment = form.hasAttribute("data-required");
    panel.innerHTML =
      (wantComment ? '<textarea name="comment" rows="2" placeholder="' + (reqComment ? "Reason (required)…" : "Optional note…") + '"' + (reqComment ? " required" : "") + ' style="margin-bottom:.4rem"></textarea>' : "") +
      '<div class="btn-row">' +
      '<button type="submit" class="btn btn-sm btn-primary">Confirm</button>' +
      '<button type="button" class="btn btn-sm btn-ghost" data-confirm-cancel>Cancel</button>' +
      '</div>';
    form.appendChild(panel);

    // The form is often a flex item: several of these sit side by side in a
    // .btn-row so their triggers line up as a row of buttons. A flex item is
    // sized by its content, so while the panel was open it stayed as narrow as
    // the button that opened it — a comment box about 148px wide inside a 350px
    // card, with the rest of the card empty beside it. Marking the form open
    // lets the stylesheet give it the whole row for as long as it is open, and
    // hand it straight back when it closes so the buttons line up again.
    function setOpen(open) {
      panel.style.display = open ? "block" : "none";
      form.classList.toggle("confirm-open", open);
      // Opening widens the form, so the text rewraps and the box needs fewer
      // lines than it would have at the narrow width. Re-fit it now rather than
      // leaving it at a height measured against a width it no longer has.
      var box = panel.querySelector("textarea");
      if (open && box && window.FTAutoGrow) window.FTAutoGrow(box);
    }

    trigger.addEventListener("click", function (e) {
      e.preventDefault();
      setOpen(panel.style.display === "none");
    });
    panel.querySelector("[data-confirm-cancel]").addEventListener("click", function () {
      setOpen(false);
    });

    var confirmBtn = panel.querySelector('button[type="submit"]');
    confirmBtn._ftIdleLabel = confirmBtn.innerHTML;
    confirmButtons.push(confirmBtn);
    // The guard hangs off "submit" and not off "click" on purpose: a panel whose
    // comment is required fails the browser's own validation before "submit"
    // ever fires, so a Confirm the browser refused to send is never disabled and
    // the user is never locked out of the form they still have to fill in.
    form.addEventListener("submit", function (e) {
      if (confirmSending) {
        e.preventDefault();
        return;
      }
      confirmBusy(true, confirmBtn);
    });
  });
})();

/* ------------------------------------------------------ version chips */
(function () {
  "use strict";
  // Point every export link in a panel at a specific form version so a downloaded
  // Excel/PDF/print view always matches the version the user is looking at (not
  // just "the latest"). Keeps side= and other params intact.
  function retargetExports(scope, version) {
    if (!scope) return;
    scope.querySelectorAll("a.js-export-link").forEach(function (a) {
      try {
        var u = new URL(a.getAttribute("href"), window.location.origin);
        if (version === null || version === undefined || version === "") u.searchParams.delete("v");
        else u.searchParams.set("v", version);
        a.setAttribute("href", u.pathname + (u.search || ""));
      } catch (err) { /* leave the link as-is on any parse issue */ }
    });
  }

  // Sync a panel's export links to its currently-active version chip.
  function syncPanelExports(panel) {
    if (!panel) return;
    var active = panel.querySelector(".vchip.active") ||
      panel.querySelector(".vchip");
    retargetExports(panel, active ? active.getAttribute("data-version") : "");
  }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".vchip");
    if (!btn) return;
    var group = btn.getAttribute("data-vgroup");
    var target = btn.getAttribute("data-vtarget");
    document.querySelectorAll('.vchip[data-vgroup="' + group + '"]').forEach(function (b) {
      b.classList.toggle("active", b === btn);
    });
    document.querySelectorAll('.vbody[id^="' + group + '-"]').forEach(function (body) {
      body.style.display = (body.id === target) ? "" : "none";
    });
    // Exports follow the selected version. Scope to the containing panel so the
    // TO chips only retarget TO exports (and PI chips only PI exports).
    retargetExports(btn.closest(".tab-panel") || document,
                    btn.getAttribute("data-version"));
  });

  // On load, align every panel's export links with its default (latest) chip.
  function initExports() {
    document.querySelectorAll(".tab-panel").forEach(syncPanelExports);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initExports);
  } else {
    initExports();
  }
})();

/* --------------------------------------------- new inquiry version form */
(function () {
  "use strict";
  var btn = document.getElementById("newver-btn");
  var form = document.getElementById("newver-form");
  if (btn && form) {
    btn.addEventListener("click", function () {
      form.style.display = (form.style.display === "none" || !form.style.display) ? "block" : "none";
    });
    var cancel = document.getElementById("newver-cancel");
    if (cancel) cancel.addEventListener("click", function () { form.style.display = "none"; });
  }
  // Per-side (combined-case) new-version toggles.
  document.querySelectorAll("[data-newver-toggle]").forEach(function (b) {
    b.addEventListener("click", function () {
      var f = document.getElementById("newver-form-" + b.getAttribute("data-newver-toggle"));
      if (f) f.style.display = (f.style.display === "none" || !f.style.display) ? "block" : "none";
    });
  });
  document.querySelectorAll("[data-newver-cancel]").forEach(function (b) {
    b.addEventListener("click", function () {
      var f = document.getElementById("newver-form-" + b.getAttribute("data-newver-cancel"));
      if (f) f.style.display = "none";
    });
  });
  // New-version upgrade toggles: flip the state caption between the current
  // value (off) and the "Upgrade to … (two-stage)" value (on).
  document.querySelectorAll(".nv-switch input[type=checkbox]").forEach(function (cb) {
    var row = cb.closest(".nv-toggle-row");
    var state = row ? row.querySelector(".nv-toggle-state") : null;
    if (!state) return;
    function paint() {
      var on = cb.checked;
      state.textContent = on ? (state.getAttribute("data-on") || "")
                             : (state.getAttribute("data-off") || "");
      state.classList.toggle("is-on", on);
    }
    cb.addEventListener("change", paint);
    paint();
  });
  // Two-stage offer-type upgrade: the visible switch is UI-only; the actual value
  // travels in a hidden <input name="offer_type"> so it is ALWAYS submitted with
  // the right value (never lost to any checkbox-submission quirk). Keep them in
  // sync on change and, defensively, again right before the form is submitted.
  document.querySelectorAll("[data-nv-offer-toggle]").forEach(function (toggle) {
    var form = toggle.closest("form");
    var out = form ? form.querySelector("[data-nv-offer-out]") : null;
    if (!out) return;
    function sync() { out.value = toggle.checked ? "TO_PI" : ""; }
    toggle.addEventListener("change", sync);
    if (form) form.addEventListener("submit", sync);
    sync();
  });
})();

/* --------------------------------------------- copy-to-clipboard buttons */
(function () {
  "use strict";
  document.addEventListener("keydown", function (e) {
    if ((e.key === "Enter" || e.key === " ") && e.target.classList && e.target.classList.contains("copy-btn")) {
      e.preventDefault(); e.target.click();
    }
  });
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".copy-btn");
    if (!btn) return;
    var el = document.getElementById(btn.getAttribute("data-copy-target"));
    var text = el ? (el.textContent || "").trim() : "";
    if (!text) return;
    var done = function () {
      // Brief checkmark on the icon — no bottom toast.
      if (btn.dataset.copyFlash === "1") return;
      btn.dataset.copyFlash = "1";
      btn.classList.add("is-copied");
      btn.classList.remove("fa-regular", "fa-copy");
      btn.classList.add("fa-solid", "fa-check");
      var prevLabel = btn.getAttribute("aria-label") || "";
      btn.setAttribute("aria-label", "Copied");
      setTimeout(function () {
        btn.classList.remove("is-copied", "fa-solid", "fa-check");
        btn.classList.add("fa-regular", "fa-copy");
        if (prevLabel) btn.setAttribute("aria-label", prevLabel);
        delete btn.dataset.copyFlash;
      }, 2000);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { done(); });
    } else {
      var ta = document.createElement("textarea"); ta.value = text;
      document.body.appendChild(ta); ta.select();
      try { document.execCommand("copy"); } catch (err) {}
      ta.remove(); done();
    }
  });
})();

/* The auto-growing comment boxes used to live here. They moved to
   static/js/autogrow.js so the standalone item-coder pages, which do not extend
   base.html and so never load this file, can have them too without dragging the
   rest of this file along. base.html loads autogrow.js BEFORE this file, because
   the inline confirm panel above calls window.FTAutoGrow when it opens. */
