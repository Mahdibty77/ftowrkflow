/* Lightweight, dependency-free Jalali (Shamsi) date + time picker.
   Attaches to <input data-jalali-datetime>, writing "YYYY-MM-DD HH:MM".
   Also attaches, date-only, to <input data-jalali-date>, writing
   "YYYY-MM-DD" — same calendar, no time row, and its own "Done" button
   instead of datetime mode's (day click selects, Done commits, same as
   datetime mode minus the time row). A birth date is the reason this
   variant exists: right widget, wrong grain, so it gets its own attribute
   rather than a second implementation of the calendar math.
   The popup's title year is itself a button that opens a scrollable
   year-picker view, so a distant year (a birth date, decades back) is two
   clicks away instead of dozens of month-arrow clicks. Month/weekday names
   and the "Today"/"Done" labels follow document.documentElement.lang — full
   Persian when the page is running in Persian, unchanged English otherwise.
   Conversion ported from cases/jalali.py. */
(function () {
  "use strict";

  // Which language is currently active on this page, read once at module
  // load rather than per-render. base.html stamps <html lang="..."> from the
  // request's LANGUAGE_CODE on the very first server-rendered byte (see that
  // template's own <html> line), and this script is loaded just before
  // </body> — so document.documentElement.lang is already correct and stable
  // by the time this line runs, and there is no client-side language switch
  // to react to mid-page (the per-person toggle in accounts/settings does a
  // real navigation, which re-runs this whole file). Anything other than an
  // explicit "fa" is treated as English.
  var LANG = (document.documentElement.lang || "en").toLowerCase().indexOf("fa") === 0 ? "fa" : "en";

  function div(a, b) { return Math.floor(a / b); }

  function g2j(gy, gm, gd) {
    var gdm = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
    var gy2 = gy - 1600, gm2 = gm - 1, gd2 = gd - 1;
    var n = 365 * gy2 + div(gy2 + 3, 4) - div(gy2 + 99, 100) + div(gy2 + 399, 400);
    n += gdm[gm2] + gd2;
    if (gm2 > 1 && ((gy % 4 === 0 && gy % 100 !== 0) || gy % 400 === 0)) n += 1;
    var jdn = n - 79, jnp = div(jdn, 12053); jdn %= 12053;
    var jy = 979 + 33 * jnp + 4 * div(jdn, 1461); jdn %= 1461;
    if (jdn >= 366) { jy += div(jdn - 1, 365); jdn = (jdn - 1) % 365; }
    var jm, jd;
    if (jdn < 186) { jm = 1 + div(jdn, 31); jd = 1 + (jdn % 31); }
    else { jm = 7 + div(jdn - 186, 30); jd = 1 + ((jdn - 186) % 30); }
    return [jy, jm, jd];
  }

  function j2g(jy, jm, jd) {
    var jy2 = jy - 979;
    var jdn = 365 * jy2 + div(jy2, 33) * 8 + div((jy2 % 33) + 3, 4);
    jdn += (jm < 7) ? (jm - 1) * 31 : (jm - 7) * 30 + 186;
    jdn += jd - 1;
    var gdn = jdn + 79;
    var gy = 1600 + 400 * div(gdn, 146097); gdn %= 146097;
    var leap = true;
    if (gdn >= 36525) {
      gdn -= 1; gy += 100 * div(gdn, 36524); gdn %= 36524;
      if (gdn >= 365) gdn += 1; else leap = false;
    }
    gy += 4 * div(gdn, 1461); gdn %= 1461;
    if (gdn >= 366) { leap = false; gdn -= 1; gy += div(gdn, 365); gdn %= 365; }
    var gd = gdn + 1;
    var months = [0, 31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    var gm = 0;
    for (var i = 1; i <= 12; i++) { if (gd <= months[i]) { gm = i; break; } gd -= months[i]; }
    return [gy, gm, gd];
  }

  var JMONTHS_EN = ["Farvardin", "Ordibehesht", "Khordad", "Tir", "Mordad", "Shahrivar",
                    "Mehr", "Aban", "Azar", "Dey", "Bahman", "Esfand"];
  // Same twelve months, Persian script. A second hand-maintained table on
  // purpose, not a gettext lookup — these are proper calendar names, not
  // sentence fragments assembled at render time — matching the two other
  // hand-maintained copies this codebase already carries (core/templatetags/
  // ft_extras.py's ``_JMONTHS`` and people/shift_hours.py's ``JMONTHS_FA``;
  // see that file's own comment for why merging all three into one shared
  // table is a larger, separate change than this one).
  var JMONTHS_FA = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
                    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"];
  // Persian week starts on Saturday, so both tables below run Sa..Fr — the
  // same order jWeekday() below returns (0=Sat..6=Fri).
  var WEEK_EN = ["Sa", "Su", "Mo", "Tu", "We", "Th", "Fr"];
  // Single-letter Persian weekday initials (شنبه, یکشنبه, دوشنبه, سه‌شنبه,
  // چهارشنبه, پنجشنبه, جمعه). No existing precedent for an abbreviated form
  // was found elsewhere in this codebase (checked core/templatetags/
  // ft_extras.py, people/calendar_ir.py, people/iran_holidays.py), so these
  // are the widget's own — chosen for width, since the grid column is the
  // same cramped ~30px cell the day-of-month numbers already share.
  var WEEK_FA = ["ش", "ی", "د", "س", "چ", "پ", "ج"];

  var MONTHS = LANG === "fa" ? JMONTHS_FA : JMONTHS_EN;
  var WEEK = LANG === "fa" ? WEEK_FA : WEEK_EN;

  // The only other user-facing strings this file prints. Not a full i18n
  // framework on purpose — this file has zero Django template integration
  // today (see the file's top-of-file docstring) and stays that way; a
  // two-key lookup is all "Today"/"Done" need.
  var LABELS = LANG === "fa"
    ? { today: "امروز", done: "تأیید", year: "سال" }
    : { today: "Today", done: "Done", year: "Year" };

  // Year-picker range, anchored on "now" rather than on whatever month the
  // popup happens to be showing: 120 years back covers any realistic birth
  // date (the field this was built for) with room to spare, and 15 years
  // forward is far more than a reminder/due-date field ever needs. Widened
  // per-popup (see renderYears) if the input's own pre-filled value already
  // falls outside this window, so an existing far-out value is never
  // unreachable.
  var YEAR_RANGE_PAST = 120;
  var YEAR_RANGE_FUTURE = 15;

  function jLeap(jy) {
    var a = j2g(jy, 1, 1), b = j2g(jy + 1, 1, 1);
    var da = Date.UTC(a[0], a[1] - 1, a[2]), db = Date.UTC(b[0], b[1] - 1, b[2]);
    return Math.round((db - da) / 86400000) === 366;
  }
  function daysInJMonth(jy, jm) {
    if (jm <= 6) return 31;
    if (jm <= 11) return 30;
    return jLeap(jy) ? 30 : 29;
  }
  // Weekday (0=Sat..6=Fri) of a Jalali date.
  function jWeekday(jy, jm, jd) {
    var g = j2g(jy, jm, jd);
    var dow = new Date(g[0], g[1] - 1, g[2]).getDay(); // 0=Sun..6=Sat
    return (dow + 1) % 7; // shift so 0=Sat
  }
  function pad(n) { return (n < 10 ? "0" : "") + n; }

  function build(input, dateOnly) {
    input.style.display = "none";
    var wrap = document.createElement("div");
    wrap.className = "jdp-wrap";
    var display = document.createElement("input");
    display.type = "text";
    display.readOnly = true;
    display.className = input.className;
    display.placeholder = input.getAttribute("placeholder") || "Pick a date";
    display.value = input.value || "";
    wrap.appendChild(display);

    var pop = document.createElement("div");
    pop.className = "jdp-pop";
    pop.style.display = "none";
    document.body.appendChild(pop);
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);

    var today = g2j(new Date().getFullYear(), new Date().getMonth() + 1, new Date().getDate());
    // "view" toggles the popup between the normal month+day grid ("days")
    // and the year picker ("years") added for fast decades-back navigation —
    // see render()/renderYears() below. Always reset to "days" on open (in
    // the display click handler) so leaving the year picker mid-browse never
    // leaks into the next time this same input is opened.
    var state = { jy: today[0], jm: today[1], jd: null, hh: 9, mm: 0, view: "days" };

    // Pre-fill from existing value (accept dot, slash or dash separators).
    var m = (input.value || "").match(/(\d{3,4})[.\/-](\d{1,2})[.\/-](\d{1,2})(?:\s+(\d{1,2}):(\d{1,2}))?/);
    if (m) {
      state.jy = +m[1]; state.jm = +m[2]; state.jd = +m[3];
      if (m[4]) { state.hh = +m[4]; state.mm = +m[5]; }
    }

    function commit() {
      if (state.jd) {
        var v = state.jy + "-" + pad(state.jm) + "-" + pad(state.jd);
        if (!dateOnly) v += " " + pad(state.hh) + ":" + pad(state.mm);
        input.value = v; display.value = v;
        input.dispatchEvent(new Event("change", { bubbles: true }));
      }
    }

    function clearValue() {
      state.jd = null;
      input.value = ""; display.value = "";
      input.dispatchEvent(new Event("change", { bubbles: true }));
    }

    function render() {
      // Year picker is a separate popup view entirely (its own head, its own
      // scrollable grid of years, no time row) — see renderYears().
      if (state.view === "years") { renderYears(); return; }
      var first = jWeekday(state.jy, state.jm, 1);
      var dim = daysInJMonth(state.jy, state.jm);
      // The year in the title is its own button (data-open-years) rather
      // than plain text — clicking it is the whole point of this round's
      // change: reaching a year like 1367 by clicking the month-prev arrow
      // dozens of times was the original complaint.
      var html = '<div class="jdp-head">' +
        '<button type="button" class="jdp-nav" data-nav="-1">&#8249;</button>' +
        '<span class="jdp-title">' + MONTHS[state.jm - 1] + ' ' +
          '<button type="button" class="jdp-year-btn" data-open-years>' + state.jy + '</button></span>' +
        '<button type="button" class="jdp-nav" data-nav="1">&#8250;</button></div>';
      html += '<div class="jdp-grid">';
      WEEK.forEach(function (w) { html += '<div class="jdp-w">' + w + '</div>'; });
      for (var i = 0; i < first; i++) html += '<div></div>';
      for (var d = 1; d <= dim; d++) {
        var sel = (state.jd === d) ? ' jdp-sel' : '';
        html += '<button type="button" class="jdp-day' + sel + '" data-day="' + d + '">' + d + '</button>';
      }
      html += '</div>';
      if (dateOnly) {
        // Date-only used to commit and close on the very day click itself
        // (see this file's top-of-file docstring, now updated). That meant
        // there was never a moment to reach the new year picker above and
        // still land on a day afterwards without reopening the popup from
        // scratch. A "Done" button — the same control datetime mode already
        // has, minus the time row — fixes both problems at once: day click
        // now only *selects*, same as datetime mode, and this button commits.
        html += '<div class="jdp-time">' +
          '<span class="jdp-spacer"></span>' +
          '<button type="button" class="jdp-today">' + LABELS.today + '</button>' +
          '<button type="button" class="jdp-ok">' + LABELS.done + '</button></div>';
      } else {
        html += '<div class="jdp-time"><i class="fa-regular fa-clock"></i>' +
          '<input type="number" min="0" max="23" class="jdp-hh" value="' + pad(state.hh) + '">:' +
          '<input type="number" min="0" max="59" class="jdp-mm" value="' + pad(state.mm) + '">' +
          '<span class="jdp-spacer"></span>' +
          '<button type="button" class="jdp-today">' + LABELS.today + '</button>' +
          '<button type="button" class="jdp-ok">' + LABELS.done + '</button></div>';
      }
      pop.innerHTML = html;
    }

    // The year-picker view: a flat, scrollable list (not month-style
    // pagination) so that ANY year in range is at most one open + one click
    // away — no repeated "next decade" clicks to reach something like 1367.
    // Descending (most recent first) so the common case — a year within the
    // last decade or two — sits near the top; older years are a scroll away.
    // The range is anchored on "now" (YEAR_RANGE_PAST/FUTURE above) but
    // widened to always include state.jy, so a pre-filled value from far
    // outside the default window (an unusually old birth date, say) is never
    // unreachable. The back arrow returns to the month/day view without
    // picking anything, since clicking a year is otherwise the only way out.
    function renderYears() {
      var lo = Math.min(today[0] - YEAR_RANGE_PAST, state.jy);
      var hi = Math.max(today[0] + YEAR_RANGE_FUTURE, state.jy);
      var html = '<div class="jdp-head">' +
        '<button type="button" class="jdp-nav" data-back-to-days>&#8249;</button>' +
        '<span class="jdp-title">' + LABELS.year + '</span>' +
        '<button type="button" class="jdp-nav jdp-nav-ghost" tabindex="-1" aria-hidden="true">&#8250;</button></div>';
      html += '<div class="jdp-grid jdp-ygrid">';
      for (var y = hi; y >= lo; y--) {
        var sel = (y === state.jy) ? ' jdp-sel' : '';
        html += '<button type="button" class="jdp-day' + sel + '" data-year="' + y + '">' + y + '</button>';
      }
      html += '</div>';
      pop.innerHTML = html;
      // Bring the currently-selected (or current) year into view instead of
      // always opening scrolled to the very top of a ~135-year list.
      var selEl = pop.querySelector(".jdp-sel");
      if (selEl) selEl.scrollIntoView({ block: "center" });
    }

    function place() {
      var r = display.getBoundingClientRect();
      var w = 268, vw = window.innerWidth, vh = window.innerHeight;
      var left = Math.min(r.left, vw - w - 8);
      if (left < 8) left = 8;
      pop.style.left = left + "px";
      // Show below by default; flip above if it would overflow the viewport.
      var below = r.bottom + 4;
      if (below + 320 > vh && r.top - 320 > 0) {
        pop.style.top = Math.max(8, r.top - 4 - 320) + "px";
      } else {
        pop.style.top = below + "px";
      }
    }

    display.addEventListener("click", function (e) {
      e.stopPropagation();
      var open = pop.style.display !== "none";
      document.querySelectorAll(".jdp-pop").forEach(function (p) { p.style.display = "none"; });
      // Always reopen on the month/day view, never wherever the year picker
      // was left mid-browse last time (see the "view" field's own comment).
      if (!open) { state.view = "days"; render(); pop.style.display = "block"; place(); }
    });

    // Pressing Delete or Backspace on the focused field clears it (resets filter).
    display.addEventListener("keydown", function (e) {
      if (e.key === "Delete" || e.key === "Backspace") {
        e.preventDefault();
        clearValue();
        pop.style.display = "none";
      }
    });

    pop.addEventListener("click", function (e) {
      // Keep the popup open on any interaction inside it; rebuilding the grid
      // detaches the clicked node, so we must not let this reach the
      // document-level "click outside" handler.
      e.stopPropagation();
      var openYears = e.target.closest("[data-open-years]");
      if (openYears) {
        state.view = "years";
        render(); return;
      }
      var backToDays = e.target.closest("[data-back-to-days]");
      if (backToDays) {
        state.view = "days";
        render(); return;
      }
      var year = e.target.closest("[data-year]");
      if (year) {
        // Land back on the month view, on whichever month was already
        // showing — a birth date is usually "I know roughly which month,
        // just not which year", so keeping the month avoids throwing away
        // the one thing the user had already narrowed down. Defaulting to
        // month 1 instead would be an equally defensible choice; this is
        // the one made here.
        state.jy = parseInt(year.getAttribute("data-year"), 10);
        state.view = "days";
        render(); return;
      }
      var nav = e.target.closest("[data-nav]");
      if (nav) {
        state.jm += parseInt(nav.getAttribute("data-nav"), 10);
        if (state.jm < 1) { state.jm = 12; state.jy -= 1; }
        if (state.jm > 12) { state.jm = 1; state.jy += 1; }
        render(); return;
      }
      var day = e.target.closest("[data-day]");
      if (day) {
        // Used to commit-and-close immediately in date-only mode (see the
        // top-of-file docstring). Now that date-only mode has its own
        // "Done" button (added alongside the year picker above, so a day
        // picked after jumping through the year grid isn't forced to
        // re-commit blind), a day click only selects here too — identical
        // to datetime mode's own day click below.
        state.jd = parseInt(day.getAttribute("data-day"), 10);
        render(); return;
      }
      if (e.target.closest(".jdp-today")) {
        var t = g2j(new Date().getFullYear(), new Date().getMonth() + 1, new Date().getDate());
        state.jy = t[0]; state.jm = t[1]; state.jd = t[2];
        if (dateOnly) { commit(); pop.style.display = "none"; return; }
        render(); return;
      }
      if (e.target.closest(".jdp-ok")) {
        // Only datetime mode's popup has the hour/minute inputs; date-only's
        // "Done" (added in this round) shares this same handler but has
        // nothing to read here.
        if (!dateOnly) {
          var hh = parseInt(pop.querySelector(".jdp-hh").value, 10);
          var mm = parseInt(pop.querySelector(".jdp-mm").value, 10);
          state.hh = isNaN(hh) ? 0 : Math.max(0, Math.min(23, hh));
          state.mm = isNaN(mm) ? 0 : Math.max(0, Math.min(59, mm));
        }
        commit(); pop.style.display = "none"; return;
      }
    });

    // Close only when the user presses outside the widget. Using mousedown
    // (which fires before the click that rebuilds the grid) avoids any race
    // with the day/month buttons being re-rendered.
    document.addEventListener("mousedown", function (e) {
      if (!wrap.contains(e.target) && !pop.contains(e.target)) pop.style.display = "none";
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("input[data-jalali-datetime]").forEach(function (el) { build(el, false); });
    document.querySelectorAll("input[data-jalali-date]").forEach(function (el) { build(el, true); });
  });
})();
