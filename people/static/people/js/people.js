/* People module — small behaviours, no framework, no CDN, no polling.
 *
 *  1. wireLiveFilter   the people list narrows as you type, with no Search
 *                      button and no full page reload
 *  2. wireConfirm      the in-page confirmation strip that replaces every
 *                      window.confirm() on these screens
 *  3. wireJobTitles    narrows "exact job title" to the chosen job family
 *  4. wireRows         add / remove a line in the repeating tables
 *  5. wireSeatPicker   filter and multi-select free seats
 *  6. wireJalali       date typing help
 *  7. wireReveal       show a masked national ID
 *
 * Everything degrades: with scripting off the filter form still submits, the
 * confirmation strips are simply visible, the job-title dropdown offers every
 * title, and the seat form still assigns whatever is ticked. Nothing here is
 * trusted server-side.
 *
 * ES5-compatible on purpose, matching the house style of the platform's other
 * scripts.
 */
(function () {
  "use strict";

  /* How long to wait after the last keystroke before asking the server. Short
   * enough to feel like the list is following you, long enough that typing a
   * name is one request rather than eight. */
  var FILTER_DEBOUNCE_MS = 350;

  function each(list, fn) { Array.prototype.forEach.call(list || [], fn); }

  function closest(node, selector) {
    return node && node.closest ? node.closest(selector) : null;
  }

  /* ----------------------------------------------------------- live filter */
  function wireLiveFilter() {
    var form = document.querySelector("[data-live-filter]");
    if (!form || !window.fetch || !window.history) return;

    var results = document.querySelector(form.getAttribute("data-results"));
    var base = form.getAttribute("data-url") || form.action;
    var busy = form.querySelector("[data-filter-busy]");
    var reset = form.querySelector("[data-filter-reset]");
    if (!results) return;

    var timer = null;
    /* Every request carries a number; only the newest one is allowed to write
     * to the page. Without this, a slow early request can land after a fast
     * later one and put the wrong list back on screen — the single most
     * confusing thing a type-ahead filter can do. */
    var ticket = 0;

    function query() {
      var data = new FormData(form), parts = [];
      data.forEach(function (value, key) {
        if (value !== "" && value != null) {
          parts.push(encodeURIComponent(key) + "=" + encodeURIComponent(value));
        }
      });
      return parts.join("&");
    }

    function run() {
      var qs = query();
      var mine = ++ticket;
      if (busy) busy.hidden = false;

      fetch(base + "?" + (qs ? qs + "&" : "") + "fragment=1", {
        headers: { "X-Requested-With": "XMLHttpRequest" },
        credentials: "same-origin"
      })
        .then(function (r) { return r.ok ? r.text() : null; })
        .then(function (html) {
          if (mine !== ticket) return;          // a newer keystroke already won
          if (busy) busy.hidden = true;
          if (html === null) return;            // leave the last good list up
          results.innerHTML = html;
          // The address bar follows the filter, so a refresh, a bookmark or a
          // link pasted to a colleague shows the same list. replaceState, not
          // pushState: a filter is not somewhere you navigated to, and one
          // history entry per keystroke makes the back button useless.
          window.history.replaceState(null, "", qs ? base + "?" + qs : base);
        })
        .catch(function () { if (busy) busy.hidden = true; });
    }

    function schedule(delay) {
      if (timer) clearTimeout(timer);
      timer = setTimeout(run, delay);
    }

    each(form.querySelectorAll("input[type='text'],input:not([type])"), function (input) {
      input.addEventListener("input", function () { schedule(FILTER_DEBOUNCE_MS); });
    });
    each(form.querySelectorAll("select"), function (select) {
      // A choice from a dropdown is finished the moment it is made; waiting is
      // only right for something still being typed.
      select.addEventListener("change", function () { schedule(0); });
    });
    form.addEventListener("submit", function (e) { e.preventDefault(); schedule(0); });

    if (reset) {
      reset.addEventListener("click", function (e) {
        e.preventDefault();
        form.reset();
        each(form.querySelectorAll("input,select"), function (f) {
          if (f.tagName === "SELECT") f.selectedIndex = 0; else f.value = "";
        });
        schedule(0);
      });
    }
  }

  /* -------------------------------------------------------------- confirms */
  function confirmBox(id) {
    var box = document.getElementById(id);
    return box && box.hasAttribute("data-confirm-box") ? box : null;
  }

  function closeConfirms(except) {
    each(document.querySelectorAll("[data-confirm-box]"), function (box) {
      if (box !== except) box.hidden = true;
    });
    each(document.querySelectorAll("[data-confirm-toggle]"), function (btn) {
      if (!except || btn.getAttribute("data-confirm-toggle") !== except.id) {
        btn.setAttribute("aria-expanded", "false");
      }
    });
  }

  function wireConfirm() {
    // Delegated, because the people list replaces its own rows as you type.
    document.addEventListener("click", function (e) {
      var toggle = closest(e.target, "[data-confirm-toggle]");
      if (toggle) {
        var box = confirmBox(toggle.getAttribute("data-confirm-toggle"));
        if (!box) return;
        e.preventDefault();
        var opening = box.hidden;
        closeConfirms(opening ? box : null);
        box.hidden = !opening;
        toggle.setAttribute("aria-expanded", opening ? "true" : "false");
        if (opening) {
          var first = box.querySelector("button,a,input");
          if (first && first.focus) first.focus();
        }
        return;
      }

      var cancel = closest(e.target, "[data-confirm-cancel]");
      if (cancel) {
        e.preventDefault();
        var open = closest(cancel, "[data-confirm-box]");
        if (open) {
          open.hidden = true;
          var owner = document.querySelector(
            '[data-confirm-toggle="' + open.id + '"]');
          if (owner) {
            owner.setAttribute("aria-expanded", "false");
            if (owner.focus) owner.focus();
          }
        }
      }
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeConfirms(null);
    });
  }

  /* ------------------------------------------------------------ job titles */
  function wireJobTitles() {
    var data = document.getElementById("ppl-job-titles");
    var family = document.getElementById("id_req_field");
    var title = document.getElementById("id_req_title");
    if (!data || !family || !title) return;

    var map;
    try { map = JSON.parse(data.textContent || "{}"); } catch (err) { return; }

    // Every title is in the page already, because the field validates against
    // its own choices server-side — a dropdown filled in only by scripting
    // would have the server reject whatever was picked. This narrows what is
    // offered; it never adds anything the server does not know about.
    var all = [];
    each(title.options, function (opt) {
      all.push({ value: opt.value, label: opt.textContent });
    });

    function narrow() {
      var allowed = map[family.value] || null;
      var keep = title.value;
      var found = false;
      title.innerHTML = "";
      all.forEach(function (opt) {
        if (opt.value !== "" && allowed && allowed.indexOf(opt.value) === -1) return;
        var node = document.createElement("option");
        node.value = opt.value;
        node.textContent = opt.label;
        if (opt.value === keep) { node.selected = true; found = true; }
        title.appendChild(node);
      });
      // A title that does not belong to the newly chosen family is cleared
      // rather than left showing something the family contradicts.
      if (!found) title.value = "";
      title.dispatchEvent(new Event("change", { bubbles: true }));
    }

    family.addEventListener("change", narrow);
    narrow();
  }

  /* ------------------------------------------------------------ row tables */
  function wireRows() {
    document.addEventListener("click", function (e) {
      var del = closest(e.target, "[data-del-row]");
      if (del) {
        var body = closest(del, "[data-rowbody]");
        var row = closest(del, ".ppl-row");
        // Never remove the last row: the table would become unusable with no
        // Add pressed, and an empty row costs nothing (blank rows are dropped
        // server-side).
        if (body && row && body.querySelectorAll(".ppl-row").length > 1) {
          row.remove();
        } else if (row) {
          each(row.querySelectorAll("input,select"), function (i) { i.value = ""; });
        }
        return;
      }
      var add = closest(e.target, "[data-add-row]");
      if (add) {
        var key = add.getAttribute("data-add-row");
        var wrap = document.querySelector('[data-rows="' + key + '"] [data-rowbody]');
        if (!wrap) return;
        var rows = wrap.querySelectorAll(".ppl-row");
        var last = rows[rows.length - 1];
        if (!last) return;
        var copy = last.cloneNode(true);
        each(copy.querySelectorAll("input,select"), function (i) { i.value = ""; });
        wrap.appendChild(copy);
        var first = copy.querySelector("input,select");
        if (first) first.focus();
      }
    });
  }

  /* ----------------------------------------------------------- seat picker */
  function wireSeatPicker() {
    var form = document.querySelector("[data-pick-form]");
    if (!form) return;

    var list = form.querySelector("[data-pick-list]");
    var filter = form.querySelector("[data-pick-filter]");
    var none = form.querySelector("[data-pick-none]");
    var submit = form.querySelector("[data-pick-submit]");
    var counters = form.querySelectorAll("[data-pick-count]");
    var picks = form.querySelectorAll("[data-pick]");
    if (!list) return;

    function syncSelected(pick) {
      var inp = pick.querySelector("[data-pick-input]");
      if (!inp) return;
      pick.classList.toggle("is-selected", !!inp.checked);
    }

    function count() {
      var n = form.querySelectorAll("[data-pick-input]:checked").length;
      each(counters, function (c) { c.textContent = String(n); });
      if (submit) submit.disabled = n === 0;
      return n;
    }

    function narrow() {
      var term = (filter ? filter.value : "").trim().toLowerCase();
      var shown = 0;
      each(picks, function (pick) {
        var hay = pick.getAttribute("data-search") || "";
        var checked = !!pick.querySelector("[data-pick-input]:checked");
        var match = checked || !term || hay.indexOf(term) !== -1;
        pick.hidden = !match;
        if (match) shown++;
      });
      if (none) none.hidden = shown !== 0;
    }

    each(picks, function (pick) {
      syncSelected(pick);
      pick.addEventListener("click", function (e) {
        if (e.target && e.target.closest && e.target.closest("a,button")) return;
        var inp = pick.querySelector("[data-pick-input]");
        if (!inp) return;
        inp.checked = !inp.checked;
        syncSelected(pick);
        count();
        narrow();
        if (confirmStrip) confirmStrip.hidden = true;
      });
      pick.addEventListener("keydown", function (e) {
        if (e.key !== " " && e.key !== "Enter") return;
        e.preventDefault();
        pick.click();
      });
    });

    if (filter) {
      filter.addEventListener("input", narrow);
      filter.addEventListener("keydown", function (e) {
        if (e.key === "Enter") e.preventDefault();
      });
    }
    var confirmStrip = form.querySelector("[data-confirm-box]");
    form.addEventListener("submit", function (e) {
      if (count() === 0) { e.preventDefault(); return; }
      if (confirmStrip && confirmStrip.hidden) {
        e.preventDefault();
        closeConfirms(confirmStrip);
        confirmStrip.hidden = false;
        var go = confirmStrip.querySelector("button[type='submit']");
        if (go && go.focus) go.focus();
      }
    });
    list.addEventListener("change", function () {
      each(picks, syncSelected);
      count();
      narrow();
      if (confirmStrip) confirmStrip.hidden = true;
    });

    count();
    narrow();
  }

  /* ------------------------------------------------------------- odds/ends */
  function wireJalali() {
    if (window.FTFormat && window.FTFormat.wireJalaliDots) {
      window.FTFormat.wireJalaliDots(document);
      return;
    }
    each(document.querySelectorAll(".ppl-jdate, input[name$='_date'], input[name$='_on']"),
      function (inp) {
        if (inp.type !== "text") return;
        inp.addEventListener("input", function () {
          var digits = String(inp.value || "").replace(/[^\d۰-۹٠-٩]/g, "");
          var v = digits;
          if (digits.length > 4) {
            v = digits.slice(0, 4) + "." + digits.slice(4, 6);
            if (digits.length > 6) v += "." + digits.slice(6, 8);
          }
          if (v !== inp.value) inp.value = v;
        });
      });
  }

  function wireReveal() {
    each(document.querySelectorAll("[data-sensitive-toggle]"), function (btn) {
      btn.addEventListener("click", function () {
        var wrap = closest(btn, ".ppl-sensitive");
        if (!wrap) return;
        var value = wrap.querySelector(".ppl-sensitive-value");
        if (!value) return;
        var shown = value.getAttribute("data-shown") === "1";
        value.textContent = shown ? "••••••••••" : (value.getAttribute("data-value") || "");
        value.setAttribute("data-shown", shown ? "0" : "1");
        btn.innerHTML = shown
          ? '<i class="fa-solid fa-eye"></i>' : '<i class="fa-solid fa-eye-slash"></i>';
      });
    });
  }

  function wireRialInputs() {
    if (window.FTFormat && window.FTFormat.wireMoneyInputs) {
      window.FTFormat.wireMoneyInputs(document);
    }
  }

  function wireGenderMilitary() {
    function sync() {
      var female = false;
      each(document.querySelectorAll("input[name='p_gender']"), function (r) {
        if (r.checked && r.value === "خانم") female = true;
      });
      each(document.querySelectorAll(".ppl-military-field"), function (box) {
        box.hidden = female;
        if (female) {
          each(box.querySelectorAll("select,input"), function (el) { el.value = ""; });
        }
      });
    }
    document.addEventListener("change", function (e) {
      if (e.target && e.target.name === "p_gender") sync();
    });
    sync();
  }

  function wireMarriedChildren() {
    function sync() {
      var married = false;
      each(document.querySelectorAll("input[name='p_marital']"), function (r) {
        if (r.checked && r.value === "متأهل") married = true;
      });
      each(document.querySelectorAll("[data-married-only]"), function (box) {
        box.hidden = !married;
      });
    }
    document.addEventListener("change", function (e) {
      if (e.target && e.target.name === "p_marital") sync();
    });
    sync();
  }

  function wireThemedSelects() {
    var selects = document.querySelectorAll(
      ".ppl-fa select, select.ppl-theme-select"
    );
    each(selects, function (sel) {
      if (sel.getAttribute("data-dd-wired") === "1") return;
      if (sel.disabled || sel.size > 1 || sel.multiple) return;
      sel.setAttribute("data-dd-wired", "1");

      var wrap = document.createElement("div");
      wrap.className = "ppl-dd";
      sel.parentNode.insertBefore(wrap, sel);
      wrap.appendChild(sel);
      sel.hidden = true;
      sel.tabIndex = -1;
      sel.classList.add("ppl-native-fallback");

      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ppl-dd-btn";
      btn.setAttribute("aria-haspopup", "listbox");
      var label = document.createElement("span");
      label.className = "ppl-dd-label";
      var chev = document.createElement("i");
      chev.className = "fa-solid fa-chevron-down ppl-dd-chev";
      btn.appendChild(label);
      btn.appendChild(chev);
      wrap.appendChild(btn);

      var panel = document.createElement("div");
      panel.className = "ppl-dd-panel";
      panel.setAttribute("role", "listbox");
      panel.hidden = true;
      wrap.appendChild(panel);

      function selectedText() {
        var opt = sel.options[sel.selectedIndex];
        return opt ? (opt.textContent || opt.value || "—") : "—";
      }

      function rebuild() {
        panel.innerHTML = "";
        each(sel.options, function (opt, idx) {
          if (opt.disabled && !opt.value) {
            /* keep placeholder rows selectable as empty */
          }
          var o = document.createElement("button");
          o.type = "button";
          o.className = "ppl-dd-opt";
          if (opt.selected) o.classList.add("is-selected");
          o.setAttribute("role", "option");
          o.setAttribute("data-index", String(idx));
          o.textContent = opt.textContent || opt.value || "—";
          o.addEventListener("click", function (e) {
            e.preventDefault();
            e.stopPropagation();
            sel.selectedIndex = idx;
            sel.dispatchEvent(new Event("change", { bubbles: true }));
            label.textContent = selectedText();
            close();
          });
          o.addEventListener("mouseenter", function () {
            each(panel.querySelectorAll(".ppl-dd-opt"), function (x) {
              x.classList.remove("is-active");
            });
            o.classList.add("is-active");
          });
          panel.appendChild(o);
        });
        label.textContent = selectedText();
      }

      function close() {
        wrap.classList.remove("is-open");
        panel.hidden = true;
        var card = closest(wrap, ".ppl-card") || closest(wrap, ".card");
        if (card) card.style.zIndex = "";
      }

      function open() {
        rebuild();
        wrap.classList.add("is-open");
        panel.hidden = false;
        // Always open downward (Work shift / Daily hours style).
        panel.style.top = "calc(100% + 4px)";
        panel.style.bottom = "auto";
        // Keep H:M panels wide enough for two digits (e.g. 08).
        if (closest(wrap, ".ppl-hm")) {
          var need = Math.max(wrap.offsetWidth || 0, 56);
          panel.style.minWidth = need + "px";
          panel.style.width = "max-content";
        }
        var card = closest(wrap, ".ppl-card") || closest(wrap, ".card");
        if (card) card.style.zIndex = "80";
        var selBtn = panel.querySelector(".ppl-dd-opt.is-selected");
        if (selBtn) {
          try { selBtn.scrollIntoView({ block: "nearest" }); } catch (e) {}
        }
      }

      btn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        if (wrap.classList.contains("is-open")) close();
        else open();
      });

      sel.addEventListener("change", function () {
        label.textContent = selectedText();
      });

      rebuild();
    });

    if (!document.documentElement.getAttribute("data-ppl-dd-doc")) {
      document.documentElement.setAttribute("data-ppl-dd-doc", "1");
      document.addEventListener("click", function (e) {
        var t = e.target;
        each(document.querySelectorAll(".ppl-dd.is-open"), function (w) {
          if (!w.contains(t)) {
            w.classList.remove("is-open");
            var p = w.querySelector(".ppl-dd-panel");
            if (p) p.hidden = true;
          }
        });
      });
      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape") {
          each(document.querySelectorAll(".ppl-dd.is-open"), function (w) {
            w.classList.remove("is-open");
            var p = w.querySelector(".ppl-dd-panel");
            if (p) p.hidden = true;
          });
        }
      });
    }
  }

  function boot() {
    wireLiveFilter();
    wireConfirm();
    wireJobTitles();
    wireRows();
    wireSeatPicker();
    wireJalali();
    wireReveal();
    wireRialInputs();
    wireGenderMilitary();
    wireMarriedChildren();
    wireThemedSelects();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else { boot(); }
})();
