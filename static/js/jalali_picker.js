/* Lightweight, dependency-free Jalali (Shamsi) date + time picker.
   Attaches to <input data-jalali-datetime>. Writes the value back as
   "YYYY-MM-DD HH:MM" in Jalali. Conversion ported from cases/jalali.py. */
(function () {
  "use strict";

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

  var JMONTHS = ["Farvardin", "Ordibehesht", "Khordad", "Tir", "Mordad", "Shahrivar",
                 "Mehr", "Aban", "Azar", "Dey", "Bahman", "Esfand"];
  // Persian week starts on Saturday.
  var WEEK = ["Sa", "Su", "Mo", "Tu", "We", "Th", "Fr"];

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

  function build(input) {
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
    var state = { jy: today[0], jm: today[1], jd: null, hh: 9, mm: 0 };

    // Pre-fill from existing value (accept dot, slash or dash separators).
    var m = (input.value || "").match(/(\d{3,4})[.\/-](\d{1,2})[.\/-](\d{1,2})(?:\s+(\d{1,2}):(\d{1,2}))?/);
    if (m) {
      state.jy = +m[1]; state.jm = +m[2]; state.jd = +m[3];
      if (m[4]) { state.hh = +m[4]; state.mm = +m[5]; }
    }

    function commit() {
      if (state.jd) {
        var v = state.jy + "-" + pad(state.jm) + "-" + pad(state.jd) + " " + pad(state.hh) + ":" + pad(state.mm);
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
      var first = jWeekday(state.jy, state.jm, 1);
      var dim = daysInJMonth(state.jy, state.jm);
      var html = '<div class="jdp-head">' +
        '<button type="button" class="jdp-nav" data-nav="-1">&#8249;</button>' +
        '<span class="jdp-title">' + JMONTHS[state.jm - 1] + ' ' + state.jy + '</span>' +
        '<button type="button" class="jdp-nav" data-nav="1">&#8250;</button></div>';
      html += '<div class="jdp-grid">';
      WEEK.forEach(function (w) { html += '<div class="jdp-w">' + w + '</div>'; });
      for (var i = 0; i < first; i++) html += '<div></div>';
      for (var d = 1; d <= dim; d++) {
        var sel = (state.jd === d) ? ' jdp-sel' : '';
        html += '<button type="button" class="jdp-day' + sel + '" data-day="' + d + '">' + d + '</button>';
      }
      html += '</div>';
      html += '<div class="jdp-time"><i class="fa-regular fa-clock"></i>' +
        '<input type="number" min="0" max="23" class="jdp-hh" value="' + pad(state.hh) + '">:' +
        '<input type="number" min="0" max="59" class="jdp-mm" value="' + pad(state.mm) + '">' +
        '<span class="jdp-spacer"></span>' +
        '<button type="button" class="jdp-today">Today</button>' +
        '<button type="button" class="jdp-ok">Done</button></div>';
      pop.innerHTML = html;
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
      if (!open) { render(); pop.style.display = "block"; place(); }
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
      var nav = e.target.closest("[data-nav]");
      if (nav) {
        state.jm += parseInt(nav.getAttribute("data-nav"), 10);
        if (state.jm < 1) { state.jm = 12; state.jy -= 1; }
        if (state.jm > 12) { state.jm = 1; state.jy += 1; }
        render(); return;
      }
      var day = e.target.closest("[data-day]");
      if (day) { state.jd = parseInt(day.getAttribute("data-day"), 10); render(); return; }
      if (e.target.closest(".jdp-today")) {
        var t = g2j(new Date().getFullYear(), new Date().getMonth() + 1, new Date().getDate());
        state.jy = t[0]; state.jm = t[1]; state.jd = t[2]; render(); return;
      }
      if (e.target.closest(".jdp-ok")) {
        var hh = parseInt(pop.querySelector(".jdp-hh").value, 10);
        var mm = parseInt(pop.querySelector(".jdp-mm").value, 10);
        state.hh = isNaN(hh) ? 0 : Math.max(0, Math.min(23, hh));
        state.mm = isNaN(mm) ? 0 : Math.max(0, Math.min(59, mm));
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
    document.querySelectorAll("input[data-jalali-datetime]").forEach(build);
  });
})();
