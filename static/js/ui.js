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
    // Filter combos start empty (placeholder + Clear filter), never pre-selected.
    if (isFilter) {
      select.value = "";
    }
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
      var f = (filter || "").toLowerCase();
      list.innerHTML = "";
      var matches = options.filter(function (o) {
        return !f || o.label.toLowerCase().indexOf(f) !== -1 || (o.code && o.code.toLowerCase().indexOf(f) !== -1);
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
        clearFilterValue();
        list.classList.remove("open");
        return;
      }
      render(input.value);
      list.classList.add("open");
      syncClearBtn();
    });
    input.addEventListener("keydown", function (e) {
      if (!isFilter) return;
      // Delete / Backspace clears the whole filter and keeps the list closed.
      if (e.key === "Delete" || e.key === "Backspace") {
        if ((input.value || "").trim() || (select.value || "").trim()) {
          e.preventDefault();
          clearFilterValue();
          list.classList.remove("open");
        }
        return;
      }
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
      Array.prototype.forEach.call(tbody.rows, function (tr) {
        var show = terms.every(function (t) {
          var cell = tr.cells[t.col];
          var text = cell ? ((cell.getAttribute("data-fval") || cell.textContent) || "").trim().toLowerCase() : "";
          if (t.mode === "gte") return text.slice(0, 10) >= t.raw.slice(0, 10);  // Jalali Y-m-d sorts lexicographically
          if (t.mode === "lte") return text.slice(0, 10) <= t.raw.slice(0, 10);
          if (t.mode === "equals") return text === t.raw;
          return t.parts.some(function (p) { return text.indexOf(p) !== -1; });
        });
        tr.style.display = show ? "" : "none";
      });
      var counter = document.querySelector('[data-filter-count="' + table.id + '"]');
      if (counter) counter.textContent = Array.prototype.filter.call(tbody.rows, function (r) { return r.style.display !== "none"; }).length;
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
    apply();
  });

  /* --------------------------------------------------------- inline confirm */
  // A form with class "confirm-action" hides its real submit behind a small
  // confirm panel (with an optional comment), so even the first send is a
  // two-step action that never navigates to a separate page first.
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

    trigger.addEventListener("click", function (e) {
      e.preventDefault();
      panel.style.display = panel.style.display === "none" ? "block" : "none";
    });
    panel.querySelector("[data-confirm-cancel]").addEventListener("click", function () {
      panel.style.display = "none";
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
