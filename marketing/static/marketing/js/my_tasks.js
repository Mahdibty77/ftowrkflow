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
   instruction this round, "کلا تب های ساعت‌ها ... را خالی بزار" (empty the
   hour-tabs concept out entirely), replaced that click-filter row with
   `#taskHourBoxes`, an always-visible stack of per-hour boxes the VIEW now
   builds directly (marketing/views.py::_task_hour_groups) — there is
   nothing left to click, so there is no hour-pill wiring left in this file
   either. What DOES remain this file's job: showing/hiding that whole
   section in step with the day pill (exactly the same `isToday` check the
   old hour-pill row used to gate its own visibility on), and — since the
   boxes' own rows are `<div>`s the filter card's search/case/company/date
   controls cannot reach through ui.js's `<table>`-shaped pass — a second,
   small filter pass of this file's own that mirrors those SAME controls
   onto them. See `hbApply` below and _my_tasks_reminders.html's own head
   comment for the full reasoning.

   Self-guarding, like marketing/js/chart_interact.js and
   marketing/js/directory.js: if #myTasksTable is not on the page, every
   lookup below finds nothing and this file does nothing.
   =========================================================================== */
(function () {
  "use strict";

  var table = document.getElementById("myTasksTable");
  if (!table) return;

  var dayTabs = document.getElementById("taskDayTabs");
  var hourBoxes = document.getElementById("taskHourBoxes");
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

  // ----------------------------------------------------------------- days --
  if (dayTabs && dayFilter) {
    Array.prototype.forEach.call(dayTabs.querySelectorAll(".archive-status-tab"), function (btn) {
      btn.addEventListener("click", function () {
        setActive(dayTabs, btn);
        dayFilter.value = btn.getAttribute("data-day-key") || "";
        var isToday = btn.getAttribute("data-is-today") === "1";
        if (hourBoxes) {
          // Inline style, not the `hidden` property — see the template's own
          // comment on #taskHourBoxes for why `.archive-status-tabs`'s own
          // `display:flex` would need an inline override to actually win
          // (a rule this section no longer even carries, having dropped
          // that class along with the click-filter buttons it was styling).
          hourBoxes.style.display = isToday ? "" : "none";
        }
        apply();
      });
    });
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
  // above). What ui.js cannot know is that THIS page also has a pill button
  // whose own `.is-active` class needs to come back to "All" in step with
  // it, and an hour-box section that needs to disappear along with it —
  // left alone, a reader would see "Today" still highlighted, and the hour
  // boxes still open, after a clear that actually put every day back on
  // screen.
  var clearBtn = document.querySelector('[data-filter-clear="myTasksTable"]');
  if (clearBtn) {
    clearBtn.addEventListener("click", function () {
      if (dayTabs) {
        var allDay = dayTabs.querySelector(".archive-status-tab");
        if (allDay) setActive(dayTabs, allDay);
      }
      if (hourBoxes) hourBoxes.style.display = "none";
      if (missedToggle) missedToggle.checked = false;
    });
  }

  /* -------------------------------------------------------------------------
     THE HOUR BOXES' OWN FILTER MIRROR.

     `#taskHourBoxes` holds one `.hb-row` <div> per TODAY reminder (grouped
     under its own hour's box, server-side — marketing/views.py::
     _task_hour_groups), each one carrying small, plain-English `data-hb-*`
     attributes instead of the `<td>`s ui.js's own `data-filter-table` pass
     reads a `<tr>`'s columns off — see _my_tasks_reminders.html's own head
     comment for why a `<div>` cannot simply be handed to that shared,
     every-page pass. This is the small, page-owned equivalent: the SAME
     filter-card controls (marked `data-hb-target` in the template,
     alongside their own `data-filter-colname`), read the SAME way ui.js
     reads them (mode, current value), compared against the matching
     `data-hb-*` attribute on each row instead of a column index. A row that
     fails ANY active control's own test is hidden — the identical AND ui.js
     applies to the real table — so a reminder filtered out of the table
     below disappears from its own hour box too, never one without the
     other.

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

  if (hourBoxes) {
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
      Array.prototype.forEach.call(hourBoxes.querySelectorAll("[data-hb-row]"), function (row) {
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
