/* Case Archive: bring in rows as the reader reaches them.
 *
 * WHY THIS EXISTS
 * The archive used to render every matching case. At 3,000 cases that was
 * 2.4 MB of HTML and 48,891 DOM nodes, and the page took 3.8 seconds — about
 * half of it the server rendering rows nobody had scrolled to yet, and about
 * half the browser building them. It was never the database: the slowest query
 * on the page is 8 ms.
 *
 * The page now ships one window of rows and asks cases:archive_slice for the
 * next window when the reader gets near the end of the list. Both the first
 * window and every later one are rendered from cases/templates/cases/_archive_rows.html,
 * so a scrolled-in row is byte-for-byte the row the first screen would have
 * shown. Nothing here builds markup — that is the whole point, and the reason
 * this file is short.
 *
 * WHAT THE SERVER PROMISES (see cases.views.archive_slice)
 *   GET <data-slice-url>?offset=&limit=&<the same f* filters the page carries>
 *   body    the rendered <tr> rows, ready to append
 *   headers X-Archive-Next-Offset, X-Archive-Has-More, X-Archive-Total
 *
 * WITHOUT JAVASCRIPT none of this runs and the foot of the table keeps its
 * plain "Show all N matching cases" link, which re-renders the page with every
 * row on it. The archive is never silently truncated.
 */
(function () {
  "use strict";

  var table = document.getElementById("archiveTable");
  if (!table) return;

  var scroller = document.getElementById("archiveScroll");
  var body = document.getElementById("archiveRows");
  var foot = document.getElementById("archiveFoot");
  var sliceUrl = table.getAttribute("data-slice-url") || "";
  var windowSize = parseInt(table.getAttribute("data-window") || "58", 10) || 58;

  var nextOffset = parseInt(table.getAttribute("data-next-offset") || "0", 10) || 0;
  var total = parseInt(table.getAttribute("data-total") || "0", 10) || 0;
  var hasMore = foot ? !foot.hidden : false;
  var loading = false;
  var failed = false;

  var moreLink = foot ? foot.querySelector(".archive-more-link") : null;
  var busyEl = foot ? foot.querySelector(".archive-more-busy") : null;
  var errorEl = foot ? foot.querySelector(".archive-more-error") : null;
  var retryBtn = document.getElementById("archiveRetry");

  /* ----------------------------------------------------------- selection */
  /* Select mode (picking cases for a seat Delegate) keeps its chosen ids in a
     Set, NOT in the DOM. A row can be scrolled out and rebuilt at any moment,
     so the DOM cannot be the record of what the user picked — reading it back
     would quietly submit whatever happened to be on screen. Every arriving
     slice is repainted from the Set instead. */
  var selected = Object.create(null);
  var selectCount = 0;
  var confirmBtn = document.getElementById("archiveSelectConfirm");
  var countEl = document.getElementById("archiveSelectCount");
  var selectMode = !!confirmBtn;
  var returnTo = confirmBtn ? (confirmBtn.getAttribute("data-return") || "") : "";

  function paintSelection(scope) {
    (scope || body).querySelectorAll(".archive-select-row").forEach(function (tr) {
      var on = !!selected[tr.getAttribute("data-case-id")];
      tr.classList.toggle("is-selected", on);
      tr.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function syncSelectChrome() {
    if (countEl) countEl.textContent = String(selectCount);
    if (confirmBtn) confirmBtn.disabled = selectCount === 0;
  }

  function toggleSelection(id) {
    if (!id) return;
    if (selected[id]) { delete selected[id]; selectCount -= 1; }
    else { selected[id] = true; selectCount += 1; }
    syncSelectChrome();
    paintSelection();
  }

  if (selectMode && body) {
    // Delegated, because most rows do not exist yet when this runs.
    body.addEventListener("click", function (e) {
      var tr = e.target && e.target.closest ? e.target.closest(".archive-select-row") : null;
      if (!tr || !body.contains(tr)) return;
      e.preventDefault();
      e.stopPropagation();
      toggleSelection(tr.getAttribute("data-case-id"));
    });
    body.addEventListener("keydown", function (e) {
      if (e.key !== "Enter" && e.key !== " ") return;
      var tr = e.target && e.target.closest ? e.target.closest(".archive-select-row") : null;
      if (!tr || !body.contains(tr)) return;
      e.preventDefault();
      toggleSelection(tr.getAttribute("data-case-id"));
    });
    if (confirmBtn) {
      confirmBtn.addEventListener("click", function () {
        var ids = Object.keys(selected);
        if (!ids.length || !returnTo) return;
        var sep = returnTo.indexOf("?") >= 0 ? "&" : "?";
        window.location.href = returnTo + sep + "cases=" + encodeURIComponent(ids.join(","));
      });
    }
    syncSelectChrome();
    paintSelection();
  }

  /* -------------------------------------------------------- loading more */
  function setFootState(state) {
    if (!foot) return;
    if (moreLink) moreLink.hidden = state !== "idle";
    if (busyEl) busyEl.hidden = state !== "busy";
    if (errorEl) errorEl.hidden = state !== "error";
    foot.hidden = state === "done";
  }

  function sliceRequestUrl() {
    // Carry the page's own query string: the filters, the search, the status
    // tab, the date range and select mode all live there, and the slice has to
    // be judged by exactly the same ones or a scrolled-in row could be a case
    // the current filter excludes.
    var params = new URLSearchParams(window.location.search);
    // Document No. is a live search (see liveSearchDoc() below): typing it
    // never touches the URL, so window.location.search can be stale the
    // moment the reader scrolls without having submitted anything else.
    // docInput (declared further down, in scope for this whole IIFE by the
    // time this actually runs) is the one place its current value lives.
    if (docInput) {
      if (docInput.value) params.set("fdoc", docInput.value);
      else params.delete("fdoc");
    }
    params.set("offset", String(nextOffset));
    params.set("limit", String(windowSize));
    return sliceUrl + (sliceUrl.indexOf("?") >= 0 ? "&" : "?") + params.toString();
  }

  function loadMore() {
    if (loading || failed || !hasMore || !sliceUrl || !body) return;
    loading = true;
    setFootState("busy");

    fetch(sliceRequestUrl(), {
      credentials: "same-origin",
      headers: { "X-Requested-With": "XMLHttpRequest" },
    }).then(function (res) {
      if (!res.ok) throw new Error("slice " + res.status);
      return res.text().then(function (html) {
        return {
          html: html,
          next: parseInt(res.headers.get("X-Archive-Next-Offset") || "", 10),
          more: res.headers.get("X-Archive-Has-More") === "1",
          total: parseInt(res.headers.get("X-Archive-Total") || "", 10),
        };
      });
    }).then(function (data) {
      // Parsed as table markup so the browser keeps the rows as rows: assigning
      // <tr> to a div's innerHTML makes the parser throw the tags away.
      var host = document.createElement("tbody");
      host.innerHTML = data.html;
      var added = 0;
      while (host.firstElementChild) {
        body.appendChild(host.firstElementChild);
        added += 1;
      }

      // Trust the server's own count of where it got to. Falling back to the
      // number of rows we appended would drift the moment a slice comes back
      // shorter than asked for.
      nextOffset = isNaN(data.next) ? nextOffset + added : data.next;
      if (!isNaN(data.total)) total = data.total;
      hasMore = data.more && added > 0;

      table.setAttribute("data-next-offset", String(nextOffset));
      table.setAttribute("data-total", String(total));

      if (selectMode) paintSelection();
      // ui.js hides rows the column filters exclude; a row that has just
      // arrived has never been through that pass. The server has already
      // applied the same filters, so this normally hides nothing — it matters
      // in the moment between typing in a filter box and the debounced reload.
      if (typeof table.ftApplyFilters === "function") {
        try { table.ftApplyFilters(); } catch (_e) {}
      }

      loading = false;
      setFootState(hasMore ? "idle" : "done");
      // The window that just arrived may still not fill the viewport — on a
      // tall screen the sentinel can be visible again immediately.
      if (hasMore) window.setTimeout(maybeLoad, 0);
    }).catch(function () {
      // Keep what is already on screen and say so. The "Show all" link stays
      // reachable underneath, so there is always a way to the rest of the list.
      loading = false;
      failed = true;
      setFootState("error");
    });
  }

  function nearEnd() {
    if (!foot || foot.hidden) return false;
    var box = scroller || document.scrollingElement || document.documentElement;
    if (scroller) {
      return scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 400;
    }
    return box.scrollTop + window.innerHeight >= box.scrollHeight - 400;
  }

  function maybeLoad() {
    if (nearEnd()) loadMore();
  }

  if (hasMore && sliceUrl) {
    setFootState("idle");
    if ("IntersectionObserver" in window && foot) {
      var io = new IntersectionObserver(function (entries) {
        for (var i = 0; i < entries.length; i += 1) {
          if (entries[i].isIntersecting) { loadMore(); break; }
        }
      }, { root: scroller || null, rootMargin: "400px" });
      io.observe(foot);
    }
    // Kept alongside the observer rather than as an either/or: the observer can
    // miss a fast flick that lands past the sentinel without ever intersecting.
    (scroller || window).addEventListener("scroll", maybeLoad, { passive: true });
    window.addEventListener("resize", maybeLoad, { passive: true });
    window.setTimeout(maybeLoad, 0);
  }

  if (retryBtn) {
    retryBtn.addEventListener("click", function () {
      failed = false;
      loadMore();
    });
  }

  /* ------------------------------------------------- filters and the tabs */
  /* Every OTHER filter is answered by the server through a real page reload —
     it has to be, because a filter that only searched the loaded rows could
     not find a case further down the list, and would hide it without saying
     so. Each control re-submits the form and the server returns the first
     window of the new result set, one URL per choice. That is fine for a
     dropdown: picking a value is a single, deliberate moment.

     Document No. is different: it is typed character by character, and a URL
     navigation per keystroke is exactly the "brings the whole page down"
     complaint a live search must not have. It gets its own path below —
     liveSearchDoc() — that asks the SAME server predicate (archive_slice,
     the SAME endpoint the scroll-in window already uses) over fetch(), with
     no page reload and no URL change, the way the inbox search already
     works. Every other field is untouched. */
  var form = document.getElementById("archiveFilterForm");
  var docInput = form ? form.querySelector('input[name="fdoc"]') : null;

  function submitFilters() {
    if (!form) return;
    // requestSubmit(), not submit(): the plain DOM submit() method does not
    // fire a "submit" event, which is the one thing base.html's close-button
    // beforeunload guard listens for to tell "just an ordinary in-app
    // navigation" apart from "actually leaving" (see its own long comment).
    // Every status-tab click called plain submit() here, so the guard never
    // saw it as internal navigation and popped the "Leave site?" confirm on
    // every single click, for anyone with a shift open — requestSubmit()
    // submits exactly the same form the same way but fires that event first.
    form.requestSubmit();
  }

  if (form) {
    var pending = null;
    form.addEventListener("change", function (e) {
      var el = e.target;
      if (!el || !el.name || el.name.charAt(0) !== "f") return;
      submitFilters();
    });
    form.addEventListener("input", function (e) {
      var el = e.target;
      if (!el || !el.name || el.name.charAt(0) !== "f") return;
      if (el === docInput) return; // liveSearchDoc() owns this field.
      if (el.tagName !== "INPUT" || el.type === "checkbox" || el.type === "radio") return;
      // Typing: wait until they stop, so one search is not eight requests.
      window.clearTimeout(pending);
      pending = window.setTimeout(submitFilters, 350);
    });
    form.addEventListener("submit", function () {
      window.clearTimeout(pending);
    });
  }

  /* ---------------------------------------------- live Document No. search */
  if (form && docInput && sliceUrl && body) {
    var docPending = null;
    var docGen = 0; // bumped per request; a late reply from a stale keystroke is dropped.

    function liveSearchDoc() {
      var myGen = ++docGen;
      var params = new URLSearchParams(new FormData(form));
      params.set("offset", "0");
      params.set("limit", String(windowSize));
      params.set("tabs", "1");
      var url = sliceUrl + (sliceUrl.indexOf("?") >= 0 ? "&" : "?") + params.toString();

      fetch(url, {
        credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest" },
      }).then(function (res) {
        if (!res.ok) throw new Error("slice " + res.status);
        return res.text().then(function (html) {
          return {
            html: html,
            next: parseInt(res.headers.get("X-Archive-Next-Offset") || "", 10),
            more: res.headers.get("X-Archive-Has-More") === "1",
            total: parseInt(res.headers.get("X-Archive-Total") || "", 10),
            tabsRaw: res.headers.get("X-Archive-Tabs") || "",
          };
        });
      }).then(function (data) {
        if (myGen !== docGen) return; // a newer keystroke already answered this.

        // A fresh RESULT SET, not another window of the same one: replace, not
        // append — the same table the scroll-in path appends to, so a row
        // already scrolled in from a stale search does not linger underneath.
        var host = document.createElement("tbody");
        host.innerHTML = data.html;
        body.innerHTML = "";
        while (host.firstElementChild) body.appendChild(host.firstElementChild);

        nextOffset = isNaN(data.next) ? 0 : data.next;
        total = isNaN(data.total) ? total : data.total;
        hasMore = !!data.more;
        table.setAttribute("data-next-offset", String(nextOffset));
        table.setAttribute("data-total", String(total));
        failed = false;
        setFootState(hasMore ? "idle" : "done");

        var counter = document.querySelector('[data-filter-count="archiveTable"]');
        if (counter) counter.textContent = String(total);

        if (data.tabsRaw) {
          try {
            var payload = JSON.parse(data.tabsRaw);
            (payload.tabs || []).forEach(function (t) {
              var btn = document.querySelector(
                '#archiveStatusTabs .archive-status-tab[data-status="' + t.label + '"]');
              var countEl = btn ? btn.querySelector(".ast-count") : null;
              if (countEl) countEl.textContent = String(t.count);
            });
            var allBtn = document.querySelector(
              "#archiveStatusTabs .archive-status-tab-all .ast-count");
            if (allBtn && typeof payload.all === "number") {
              allBtn.textContent = String(payload.all);
            }
          } catch (_e) { /* malformed header: leave the tab strip as it was */ }
        }

        if (selectMode) paintSelection();
        // A row that has just arrived has never been through ui.js's other
        // column filters (dropdowns picked alongside this search). The server
        // already applied every filter in the query, so this normally hides
        // nothing — it only matters in the instant between typing and this
        // reply landing.
        if (typeof table.ftApplyFilters === "function") {
          try { table.ftApplyFilters(); } catch (_e2) {}
        }
        // The window that just landed may not fill the viewport.
        if (hasMore) window.setTimeout(maybeLoad, 0);
      }).catch(function () {
        if (myGen !== docGen) return;
        failed = true;
        setFootState("error");
      });
    }

    docInput.addEventListener("input", function () {
      window.clearTimeout(docPending);
      // Shorter than the other fields' 350ms: nothing here leaves this page,
      // so there is no navigation cost to amortise by waiting longer.
      docPending = window.setTimeout(liveSearchDoc, 200);
    });
    form.addEventListener("submit", function () {
      window.clearTimeout(docPending);
    });
  }

  var statusSelect = document.getElementById("archiveStatusFilter");
  var tabs = Array.prototype.slice.call(
    document.querySelectorAll("#archiveStatusTabs .archive-status-tab"));

  function paintTabs(active) {
    tabs.forEach(function (btn) {
      var on = btn.getAttribute("data-status") === active;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
    });
  }

  if (tabs.length && statusSelect) {
    paintTabs(statusSelect.value ? statusSelect.value : "ALL");
    tabs.forEach(function (btn) {
      btn.addEventListener("click", function () {
        var key = btn.getAttribute("data-status") || "ALL";
        paintTabs(key);
        statusSelect.value = key === "ALL" ? "" : key;
        submitFilters();
      });
    });
  }

  var mine = document.getElementById("archiveMineToggle");
  if (mine) {
    mine.addEventListener("change", function () {
      // Deliberately drops the other filters, exactly as before this file
      // existed: switching between "everything" and "only mine" is a change of
      // what you are looking at, not a refinement of it.
      var url = new URL(window.location.href);
      url.search = "";
      if (mine.checked) url.searchParams.set("mine", "1");
      window.location.href = url.pathname + (url.search || "");
    });
  }
})();
