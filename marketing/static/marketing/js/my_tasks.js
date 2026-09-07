/* ===========================================================================
   My Tasks — Reminders tab: day/hour grouping and the "missed only" toggle.

   THE ONLY BEHAVIOUR THIS FILE OWNS is translating three page-only controls
   — the day pills (#taskDayTabs), the hour pills (#taskHourTabs), and the
   missed-only switch (#taskMissedToggle) — into the three HIDDEN <input>s
   marketing/templates/marketing/_my_tasks_reminders.html already wires with
   data-filter-for="myTasksTable" (#taskDayFilter / #taskHourFilter /
   #taskMissedFilter). Everything about actually HIDING a row is
   static/js/ui.js's own existing `data-filter-table` pass — this file never
   sets `tr.style.display` itself, it only sets a hidden control's `.value`
   and then calls the public re-run hook that pass already exposes on the
   table (`table.ftApplyFilters`), the same one a table whose rows arrive
   after the first pass (the archive's own infinite scroll) already uses.
   Two page-only pill rows sharing one generic engine this way is what keeps
   "click Today, then click 14:00" and "type a case number in the filter
   card" from ever being two different notions of "narrow the list".

   Self-guarding, like marketing/js/chart_interact.js and
   marketing/js/directory.js: if #myTasksTable is not on the page, every
   lookup below finds nothing and this file does nothing.
   =========================================================================== */
(function () {
  "use strict";

  var table = document.getElementById("myTasksTable");
  if (!table) return;

  var dayTabs = document.getElementById("taskDayTabs");
  var hourTabs = document.getElementById("taskHourTabs");
  var dayFilter = document.getElementById("taskDayFilter");
  var hourFilter = document.getElementById("taskHourFilter");
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
        if (hourTabs) {
          // Inline style, not the `hidden` property — see the template's own
          // comment on #taskHourTabs for why `.archive-status-tabs`'s own
          // `display:flex` needs an inline override to actually win.
          hourTabs.style.display = isToday ? "" : "none";
          if (!isToday) {
            // Leaving Today clears whichever hour block was picked — an
            // hour filter with no "Today" day filter behind it would
            // silently narrow every OTHER day down to nothing, which is not
            // what closing the hour row is supposed to mean.
            var allHour = hourTabs.querySelector(".archive-status-tab");
            if (allHour) setActive(hourTabs, allHour);
            if (hourFilter) hourFilter.value = "";
          }
        }
        apply();
      });
    });
  }

  // ---------------------------------------------------------------- hours --
  if (hourTabs && hourFilter) {
    Array.prototype.forEach.call(hourTabs.querySelectorAll(".archive-status-tab"), function (btn) {
      btn.addEventListener("click", function () {
        setActive(hourTabs, btn);
        hourFilter.value = btn.getAttribute("data-hour-key") || "";
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
  // data-filter-for="myTasksTable", which includes the three hidden ones
  // above). What ui.js cannot know is that THIS page also has pill buttons
  // whose own `.is-active` class needs to come back to "All" / "All hours"
  // in step with it — left alone, a reader would see "Today" still
  // highlighted after a clear that actually put every day back on screen.
  var clearBtn = document.querySelector('[data-filter-clear="myTasksTable"]');
  if (clearBtn) {
    clearBtn.addEventListener("click", function () {
      if (dayTabs) {
        var allDay = dayTabs.querySelector(".archive-status-tab");
        if (allDay) setActive(dayTabs, allDay);
      }
      if (hourTabs) {
        hourTabs.style.display = "none";
        var allHour = hourTabs.querySelector(".archive-status-tab");
        if (allHour) setActive(hourTabs, allHour);
      }
      if (missedToggle) missedToggle.checked = false;
    });
  }
})();
