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
  /* The filters are answered by the server now — they have to be, because a
     filter that only searched the loaded rows could not find a case further
     down the list, and would hide it without saying so. Each control therefore
     re-submits the form, and the server returns the first window of the new
     result set. */
  var form = document.getElementById("archiveFilterForm");

  function submitFilters() {
    if (!form) return;
    form.submit();
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
      if (el.tagName !== "INPUT" || el.type === "checkbox" || el.type === "radio") return;
      // Typing: wait until they stop, so one search is not eight requests.
      window.clearTimeout(pending);
      pending = window.setTimeout(submitFilters, 350);
    });
    form.addEventListener("submit", function () {
      window.clearTimeout(pending);
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
