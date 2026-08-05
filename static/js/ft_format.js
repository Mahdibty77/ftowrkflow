/* Shared live formatters for money (1,234,567) and Jalali dates (1405.04.05).
 * Used by People forms; also exposed as window.FTFormat for other screens.
 */
(function (global) {
  "use strict";

  function digitsOnly(s) {
    return String(s || "").replace(/[^\d۰-۹٠-٩]/g, "")
      .replace(/[۰-۹]/g, function (c) { return String(c.charCodeAt(0) - 1776); })
      .replace(/[٠-٩]/g, function (c) { return String(c.charCodeAt(0) - 1632); });
  }

  function formatThousands(s) {
    var d = digitsOnly(s);
    if (!d) return "";
    return d.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  function formatJalaliDots(s) {
    var digits = digitsOnly(s).slice(0, 8);
    if (digits.length <= 4) return digits;
    if (digits.length <= 6) return digits.slice(0, 4) + "." + digits.slice(4);
    return digits.slice(0, 4) + "." + digits.slice(4, 6) + "." + digits.slice(6, 8);
  }

  function wireMoneyInputs(root) {
    var scope = root || document;
    var nodes = scope.querySelectorAll(".ppl-rial-input, [data-money-input]");
    Array.prototype.forEach.call(nodes, function (inp) {
      if (inp.getAttribute("data-money-wired")) return;
      inp.setAttribute("data-money-wired", "1");
      if (inp.classList.contains("ppl-rial-input") && !inp.closest(".ppl-rial-wrap")) {
        var wrap = document.createElement("div");
        wrap.className = "ppl-rial-wrap";
        inp.parentNode.insertBefore(wrap, inp);
        wrap.appendChild(inp);
        var unit = document.createElement("span");
        unit.className = "ppl-rial-unit";
        unit.textContent = "ریال";
        wrap.appendChild(unit);
      }
      inp.value = formatThousands(inp.value);
      inp.addEventListener("input", function () {
        var start = inp.selectionStart;
        var before = digitsOnly(inp.value.slice(0, start)).length;
        inp.value = formatThousands(inp.value);
        var pos = 0, seen = 0, formatted = inp.value;
        for (; pos < formatted.length && seen < before; pos++) {
          if (/\d/.test(formatted.charAt(pos))) seen++;
        }
        try { inp.setSelectionRange(pos, pos); } catch (e) {}
      });
      var form = inp.form;
      if (form && !form.getAttribute("data-money-submit")) {
        form.setAttribute("data-money-submit", "1");
        form.addEventListener("submit", function () {
          Array.prototype.forEach.call(
            form.querySelectorAll(".ppl-rial-input, [data-money-input]"),
            function (el) { el.value = digitsOnly(el.value); }
          );
        });
      }
    });
  }

  function wireJalaliDots(root) {
    var scope = root || document;
    var nodes = scope.querySelectorAll(
      ".ppl-jdate, input[name$='_date'], input[name$='_on'], input[placeholder*='۱۴'], [data-jalali-date]"
    );
    Array.prototype.forEach.call(nodes, function (inp) {
      if (inp.type && inp.type !== "text") return;
      if (inp.getAttribute("data-jalali-wired")) return;
      inp.setAttribute("data-jalali-wired", "1");
      inp.addEventListener("input", function () {
        var next = formatJalaliDots(inp.value);
        if (next !== inp.value) inp.value = next;
      });
    });
  }

  global.FTFormat = {
    digitsOnly: digitsOnly,
    formatThousands: formatThousands,
    formatJalaliDots: formatJalaliDots,
    wireMoneyInputs: wireMoneyInputs,
    wireJalaliDots: wireJalaliDots
  };
})(window);
