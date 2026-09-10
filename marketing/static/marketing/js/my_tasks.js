/* ===========================================================================
   My Tasks — Reminders tab: the day pill, the "missed only" toggle, and the
   always-visible per-hour boxes' own small filter mirror.

   THE DAY PILL (#taskDayTabs) AND THE MISSED-ONLY SWITCH (#taskMissedToggle)
   translate into TWO hidden <input>s
   marketing/templates/marketing/_my_tasks_reminders.html already wires with
   data-filter-for="myTasksTable" (#taskDayFilter / #taskMissedFilter).
   Everything about actually HIDING a row of the REAL table is
   static/js/ui.js's own existing `data-filter-table` pass — this file never
   sets a real `<tr>`'s `style.display` itself, it only sets a hidden
   control's `.value` and then calls the public re-run hook that pass
   already exposes on the table (`table.ftApplyFilters`), the same one a
   table whose rows arrive after the first pass (the archive's own infinite
   scroll) already uses.

   AN HOUR PILL ROW USED TO SIT HERE TOO — a THIRD hidden input this file
   set on click, narrowing the same table by hour. The owner's own
   instruction, "کلا تب های ساعت‌ها ... را خالی بزار" (empty the hour-tabs
   concept out entirely), replaced that click-filter row with
   `#taskHourBoxesWrap` — one always-visible stack of per-hour boxes PER DAY
   PILL, the VIEW builds directly (marketing/views.py::
   _task_hour_groups_for_day) — there is nothing left to click, so there is
   no hour-pill wiring left in this file either.

   THIS ROUND GENERALISES THE HOUR-BOX TREATMENT FROM "TODAY ONLY" TO
   "WHICHEVER SINGLE DAY PILL IS CURRENTLY ACTIVE" — the owner's own
   instruction: Tomorrow, and any further-future day pill (a work-shift-
   calendar reminder badge lands here for one, several days out), all get
   the identical always-visible-box treatment Today alone used to have. So
   `#taskHourBoxesWrap` now holds MULTIPLE `.task-hour-boxes` stacks, one per
   day pill, each carrying its own `data-day-key`; `activateDay` below shows
   AT MOST ONE of them — whichever matches the just-activated pill — and
   hides the rest, replacing the old single-element `isToday` check. THE SAME
   function also hides `#taskTableCard` (the flat table's own card) whenever
   a specific day (not "All") is active — the owner's second instruction
   this round: once the hour boxes ARE the view for one day, the identical
   rows repeated flat underneath them is redundant rather than additive.

   `data-active-day-key`, ON `#taskDayTabs` ITSELF — new this round — is read
   ONCE, on load: when marketing/views.py::my_tasks resolves ?from=/?to= to
   one single calendar day (see that view's own `_single_day_key_from_range`),
   this auto-activates the matching pill exactly as a click would, so a
   work-shift-calendar badge for a day out lands here with that day's own
   hour boxes already open, not on "All" with only the flat table filtered.

   What remains this file's job otherwise, unchanged: showing/hiding the
   right hour-box stack in step with the day pill, and — since the boxes'
   own rows are `<div>`s the filter card's search/case/company/date controls
   cannot reach through ui.js's `<table>`-shaped pass — a second, small
   filter pass of this file's own that mirrors those SAME controls onto
   them. See `hbApply` below and _my_tasks_reminders.html's own head comment
   for the full reasoning.

   Self-guarding, like marketing/js/chart_interact.js and
   marketing/js/directory.js: if #myTasksTable is not on the page, every
   lookup below finds nothing and this file does nothing.
   =========================================================================== */
(function () {
  "use strict";

  var table = document.getElementById("myTasksTable");
  if (!table) return;

  var dayTabs = document.getElementById("taskDayTabs");
  var hourBoxesWrap = document.getElementById("taskHourBoxesWrap");
  var tableCard = document.getElementById("taskTableCard");
  var dayFilter = document.getElementById("taskDayFilter");
  var missedFilter = document.getElementById("taskMissedFilter");
  var missedToggle = document.getElementById("taskMissedToggle");

  function apply() {
    // ui.js sets this on the table once its own data-filter-table pass has
    // run over it; a page that somehow loads this file first (script order
    // changed elsewhere) simply does nothing rather than throwing.
    if (typeof table.ftApplyFilters === "function") table.ftApplyFilters();
  }

  function setActive(nav, btn) {
    if (!nav) return;
    Array.prototype.forEach.call(nav.querySelectorAll(".archive-status-tab"), function (b) {
      b.classList.remove("is-active");
      b.setAttribute("aria-selected", "false");
    });
    btn.classList.add("is-active");
    btn.setAttribute("aria-selected", "true");
  }

  // Shows the ONE hour-box stack matching `dayKey` (hides every other one),
  // and toggles the flat table's own card in step — see this file's own
  // head comment for why the two are one decision. `dayKey === ""` is the
  // "All" pill (or a genuine multi-day range, which never calls this at
  // all): no stack matches an empty key, so every stack stays hidden and
  // the table card is shown.
  function showDay(dayKey) {
    var matched = false;
    if (hourBoxesWrap) {
      Array.prototype.forEach.call(
        hourBoxesWrap.querySelectorAll(".task-hour-boxes"),
        function (stack) {
          var isMatch = !!dayKey && stack.getAttribute("data-day-key") === dayKey;
          // Inline style, not the `hidden` property — see the template's own
          // comment on #taskHourBoxesWrap for why `.archive-status-tabs`'s
          // own `display:flex` would need an inline override to actually
          // win at equal specificity.
          stack.style.display = isMatch ? "" : "none";
          if (isMatch) matched = true;
        }
      );
    }
    if (tableCard) tableCard.style.display = matched ? "none" : "";
  }

  // ----------------------------------------------------------------- days --
  function activateDay(btn) {
    setActive(dayTabs, btn);
    var key = btn.getAttribute("data-day-key") || "";
    if (dayFilter) dayFilter.value = key;
    showDay(key);
    apply();
  }

  if (dayTabs && dayFilter) {
    Array.prototype.forEach.call(dayTabs.querySelectorAll(".archive-status-tab"), function (btn) {
      btn.addEventListener("click", function () { activateDay(btn); });
    });
    // See this file's own head comment's "data-active-day-key" section — a
    // work-shift-calendar badge link for one specific day auto-opens that
    // day's own pill (and hence its own hour boxes) on load, the same as a
    // real click would. A key that names no pill on this page (should not
    // happen for a link this app itself builds — see
    // marketing/views.py::_single_day_key_from_range's own docstring — but a
    // hand-edited URL is not a fact) simply finds nothing and this is a
    // silent no-op, leaving the page on its ordinary "All" default.
    var activeKey = dayTabs.getAttribute("data-active-day-key") || "";
    if (activeKey) {
      var target = dayTabs.querySelector(
        '.archive-status-tab[data-day-key="' + activeKey + '"]');
      if (target) activateDay(target);
    }
  }

  // -------------------------------------------------------------- missed --
  if (missedToggle && missedFilter) {
    missedToggle.addEventListener("change", function () {
      missedFilter.value = missedToggle.checked ? "Due" : "";
      apply();
    });
  }

  // ---------------------------------------------------------- clear filters
  // "Clear filters" already resets every ordinary control through ui.js's
  // own data-filter-clear pass (it walks every control carrying
  // data-filter-for="myTasksTable", which includes the two hidden ones
  // above). What ui.js cannot know is that THIS page also has a pill strip
  // whose own `.is-active` pill needs to come back to "All" in step with
  // it, and hour-box stacks that need to disappear along with it — left
  // alone, a reader would see some OTHER day still highlighted, its hour
  // boxes still open and the table still hidden, after a clear that
  // actually put every day back on screen. Matched by `data-day-key=""`
  // rather than "the first pill" — "All" moved to the END of the strip this
  // round (see the template's own head comment), so position can no longer
  // stand in for identity here.
  var clearBtn = document.querySelector('[data-filter-clear="myTasksTable"]');
  if (clearBtn) {
    clearBtn.addEventListener("click", function () {
      if (dayTabs) {
        var allDay = dayTabs.querySelector('.archive-status-tab[data-day-key=""]');
        if (allDay) activateDay(allDay);
      }
      if (missedToggle) missedToggle.checked = false;
    });
  }

  /* -------------------------------------------------------------------------
     THE HOUR BOXES' OWN FILTER MIRROR.

     `#taskHourBoxesWrap` holds one `.hb-row` <div> per reminder, across
     every day's own stack (grouped under its own hour's box, per day,
     server-side — marketing/views.py::_task_hour_groups_for_day), each one
     carrying small, plain-English `data-hb-*` attributes instead of the
     `<td>`s ui.js's own `data-filter-table` pass reads a `<tr>`'s columns
     off — see _my_tasks_reminders.html's own head comment for why a `<div>`
     cannot simply be handed to that shared, every-page pass. This is the
     small, page-owned equivalent: the SAME filter-card controls (marked
     `data-hb-target` in the template, alongside their own
     `data-filter-colname`), read the SAME way ui.js reads them (mode,
     current value), compared against the matching `data-hb-*` attribute on
     each row instead of a column index. A row that fails ANY active
     control's own test is hidden — the identical AND ui.js applies to the
     real table — so a reminder filtered out of the table below disappears
     from its own hour box too, never one without the other. Applied across
     EVERY day's own stack at once, not only the currently-visible one — a
     hidden stack costs nothing extra to filter now, and it is then already
     correctly narrowed the instant `showDay` makes it the visible one.

     `dateKey`/`foldDigits` below are a DELIBERATE, VERBATIM MIRROR of
     static/js/ui.js's own pair of the same name — copied rather than
     imported because this file loads as a plain, non-module <script> the
     same way every other page-specific file under this app's own
     static/…/js/ folders does, and duplicating twelve lines of pure,
     side-effect-free string logic costs
     far less than teaching the build a module graph for one shared helper.
     Keep the two in sync by hand; see ui.js's own copy for what each step
     is for (Jalali digit-folding, separator normalisation, zero-padding).
   */
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
    if (!numeric) return head;
    return pad0(parts[0], 4) + "." + pad0(parts[1], 2) + "." + pad0(parts[2], 2);
  }

  if (hourBoxesWrap) {
    var hbControls = Array.prototype.slice.call(
      document.querySelectorAll('[data-filter-for="myTasksTable"][data-hb-target]'));

    function hbApply() {
      var terms = [];
      hbControls.forEach(function (c) {
        var raw = (c.value || "").trim().toLowerCase();
        if (!raw) return;
        terms.push({
          target: c.getAttribute("data-hb-target"),
          mode: c.getAttribute("data-filter-mode") || "contains",
          raw: raw,
        });
      });
      Array.prototype.forEach.call(hourBoxesWrap.querySelectorAll("[data-hb-row]"), function (row) {
        var show = terms.every(function (t) {
          var text = (row.getAttribute("data-hb-" + t.target) || "").toLowerCase();
          if (t.mode === "gte") return dateKey(text) >= dateKey(t.raw);
          if (t.mode === "lte") return dateKey(text) <= dateKey(t.raw);
          if (t.mode === "equals") return text === t.raw;
          return text.indexOf(t.raw) !== -1;
        });
        row.style.display = show ? "" : "none";
      });
    }

    hbControls.forEach(function (c) {
      c.addEventListener("input", hbApply);
      c.addEventListener("change", hbApply);
    });
    // Runs once on load too — a visit carrying ?from=/?to= (see
    // marketing/views.py::my_tasks's own docstring) pre-fills the date
    // fields with a value neither an "input" nor a "change" event ever
    // fired for, and every hour box still has to open already narrowed to
    // match, exactly like the real table below does on the same visit.
    hbApply();
  }
})();
