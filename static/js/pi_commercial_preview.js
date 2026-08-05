/* Commercial PI totals + optional currency conversion on case detail.
 *
 * Totals (Subtotal / VAT / Grand Total) always show with the active version's
 * currency unit. Unit conversion starts closed: the user confirms in English,
 * edits From/To/Rate with live preview, then Save persists the conversion on
 * that PI version (exports use the new amounts). After save the panel stays
 * closed until a new PI version exists.
 */
(function () {
  "use strict";

  function toNumber(text) {
    if (text == null) return 0;
    var s = String(text).replace(/,/g, "").replace(/[^\d.\-]/g, "").trim();
    if (!s) return 0;
    var n = Number(s);
    return isFinite(n) ? n : 0;
  }

  function formatRateDisplay(value) {
    var s = String(value == null ? "" : value).trim();
    if (!s) return "";
    var n = toNumber(s);
    if (!isFinite(n)) return s;
    var raw;
    if (Math.abs(n) >= 1) {
      raw = (Math.floor(n) === n)
        ? String(Math.round(n))
        : n.toFixed(6).replace(/\.?0+$/, "");
    } else {
      raw = n.toFixed(10).replace(/\.?0+$/, "") || "0";
    }
    var parts = String(raw).split(".");
    parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    return parts.join(".");
  }

  function setRateInputValue(rateInp, value) {
    if (!rateInp) return;
    var s = String(value == null ? "" : value).trim();
    rateInp.value = s ? formatRateDisplay(s) : "";
  }

  function formatMoney(n, currency, external) {
    var c = String(currency || (external ? "usd" : "rial")).toLowerCase();
    if (external && c === "rial") c = "usd";
    var useDec = (c !== "rial");
    var rounded = useDec ? Math.round(n * 100) / 100 : Math.round(n);
    try {
      return useDec
        ? rounded.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
        : rounded.toLocaleString("en-US");
    } catch (e) {
      return String(rounded);
    }
  }

  function unitLabel(code, external) {
    var c = String(code || (external ? "usd" : "rial")).toLowerCase();
    if (external && c === "rial") c = "usd";
    if (c === "usd" || c === "$") return "$";
    if (c === "eur" || c === "€") return "€";
    if (c === "rial") return "Rial";
    return String(c).toUpperCase();
  }

  function csrfToken() {
    var m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    if (m) return decodeURIComponent(m[1]);
    var inp = document.querySelector("input[name=csrfmiddlewaretoken]");
    return inp ? inp.value : "";
  }

  function activeWrap(panel) {
    if (!panel) return null;
    var bodies = panel.querySelectorAll(".vbody");
    for (var i = 0; i < bodies.length; i++) {
      if (bodies[i].style.display === "none") continue;
      var wrap = bodies[i].querySelector(".table-wrap[data-pi-form-id]");
      if (wrap) return wrap;
    }
    return panel.querySelector(".table-wrap[data-pi-form-id]");
  }

  function visiblePiTable(panel) {
    var wrap = activeWrap(panel);
    return wrap ? wrap.querySelector("table.data[data-pi-prices='1']") : null;
  }

  function factorFor(card) {
    var ext = card.getAttribute("data-external-currency") === "1";
    var panelOpen = !card.querySelector(".pi-conv-panel")?.hidden;
    if (!panelOpen) return 1;
    if (card._fxStale) return 1;
    var fallback = ext ? "usd" : "rial";
    var from = card.querySelector(".pi-conv-from")?.value || fallback;
    var to = card.querySelector(".pi-conv-to")?.value || from;
    if (ext) {
      if (from === "rial") from = "usd";
      if (to === "rial") to = "eur";
    }
    var rateText = card.querySelector(".pi-conv-rate")?.value || "";
    var rate = toNumber(rateText);
    if (!rateText || !rate || from === to) return 1;
    return 1 / rate;
  }

  function previewCurrency(card, baseCurrency) {
    var panel = card.querySelector(".pi-conv-panel");
    if (!panel || panel.hidden) return baseCurrency;
    if (card._fxStale) return baseCurrency;
    var ext = card.getAttribute("data-external-currency") === "1";
    var to = card.querySelector(".pi-conv-to")?.value || baseCurrency;
    if (ext && to === "rial") to = "eur";
    var rateText = card.querySelector(".pi-conv-rate")?.value || "";
    var from = card.querySelector(".pi-conv-from")?.value || baseCurrency;
    if (rateText && toNumber(rateText) && from !== to) return to;
    return baseCurrency;
  }

  function fxApiUrl(card) {
    var panel = card.querySelector(".pi-conv-panel");
    return (panel && panel.getAttribute("data-fx-api")) || "/fx-rates/api/";
  }

  function lockedFromOf(card) {
    var panel = card.querySelector(".pi-conv-panel");
    var locked = panel && panel.getAttribute("data-locked-from");
    if (locked) return String(locked).toLowerCase();
    return card.getAttribute("data-external-currency") === "1" ? "usd" : "rial";
  }

  function lockFromSelect(card) {
    var fromSel = card.querySelector(".pi-conv-from");
    if (!fromSel) return lockedFromOf(card);
    var from = lockedFromOf(card);
    fromSel.innerHTML = "";
    var opt = document.createElement("option");
    opt.value = from;
    opt.textContent = from === "usd" ? "USD ($)" : "Rial";
    opt.selected = true;
    fromSel.appendChild(opt);
    fromSel.value = from;
    fromSel.disabled = true;
    return from;
  }

  function fillUnitOptions(sel, units, ext, preferred) {
    if (!sel || !units || !units.length) return;
    var cur = preferred || sel.value;
    sel.innerHTML = "";
    units.forEach(function (u) {
      if (ext && u.code === "rial") return;
      var opt = document.createElement("option");
      opt.value = u.code;
      var sym = u.symbol || u.code.toUpperCase();
      if (sym === "﷼") sym = "Rial";
      opt.textContent = sym + " — " + (u.name || u.code.toUpperCase());
      sel.appendChild(opt);
    });
    if (cur && Array.prototype.some.call(sel.options, function (o) { return o.value === cur; })) {
      sel.value = cur;
    } else if (sel.options.length) {
      sel.value = sel.options[0].value;
    }
  }

  function syncFxRate(card, done) {
    var staleEl = card.querySelector(".pi-fx-stale");
    var noteEl = card.querySelector(".pi-fx-rate-note");
    var rateInp = card.querySelector(".pi-conv-rate");
    var rateWrap = card.querySelector(".pi-conv-rate-wrap");
    var rateUnit = card.querySelector(".pi-conv-rate-unit");
    var saveBtn = card.querySelector(".pi-conv-save");
    var ext = card.getAttribute("data-external-currency") === "1";
    var from = lockFromSelect(card);
    var toSel = card.querySelector(".pi-conv-to");
    var to = (toSel && toSel.value) || from;
    if (ext && to === "rial") to = "eur";
    // No FX warning copy in the Unit conversion panel.
    if (staleEl) { staleEl.hidden = true; staleEl.textContent = ""; }
    if (noteEl) noteEl.textContent = "";
    var showRate = Boolean(to && from && to !== from);
    if (rateWrap) rateWrap.style.display = showRate ? "" : "none";
    if (rateUnit) {
      var fromLbl = from === "usd" ? "$" : (from === "eur" ? "€" : (from === "rial" ? "Rial" : String(from || "").toUpperCase()));
      var toLbl = to === "usd" ? "$" : (to === "eur" ? "€" : (to === "rial" ? "Rial" : String(to || "").toUpperCase()));
      rateUnit.textContent = showRate ? (fromLbl + " / " + toLbl) : "";
    }
    if (!showRate) {
      setRateInputValue(rateInp, "");
      if (saveBtn) saveBtn.disabled = true;
      if (typeof done === "function") done();
      else refreshCard(card);
      return;
    }
    var url = fxApiUrl(card) + "?from=" + encodeURIComponent(from) + "&to=" + encodeURIComponent(to);
    fetch(url, { credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (!data || data.ok === false) throw new Error((data && data.error) || "FX lookup failed");
        fillUnitOptions(toSel, data.units, ext, to);
        lockFromSelect(card);
        card._fxStale = !!data.stale;
        if (saveBtn) saveBtn.disabled = !!data.stale || from === to || !data.convertible;
        if (data.convertible && data.rate != null && !data.stale) {
          setRateInputValue(rateInp, data.rate);
        } else {
          setRateInputValue(rateInp, "");
        }
        if (typeof done === "function") done();
        else refreshCard(card);
      })
      .catch(function () {
        card._fxStale = true;
        setRateInputValue(rateInp, "");
        if (saveBtn) saveBtn.disabled = true;
        if (typeof done === "function") done();
        else refreshCard(card);
      });
  }

  function syncGate(card) {
    var panel = card.closest(".tab-panel");
    var wrap = activeWrap(panel);
    var gate = card.querySelector(".pi-conv-gate");
    var convPanel = card.querySelector(".pi-conv-panel");
    var openBtn = gate ? gate.querySelector(".pi-conv-open") : null;
    if (!gate) return;
    var converted = wrap && wrap.getAttribute("data-pi-converted") === "1";
    var isCurrent = !wrap || wrap.getAttribute("data-pi-is-current") === "1";
    if (converted || !isCurrent) {
      gate.hidden = true;
      if (convPanel) convPanel.hidden = true;
      hideConfirmPrompt(card);
    } else if (convPanel && !convPanel.hidden) {
      gate.hidden = false;
      if (openBtn) openBtn.hidden = true;
      hideConfirmPrompt(card);
    } else {
      gate.hidden = false;
      if (openBtn) openBtn.hidden = false;
    }
  }

  function refreshCard(card) {
    var panel = card.closest(".tab-panel");
    var wrap = activeWrap(panel);
    var table = visiblePiTable(panel);
    var vatPct = toNumber(card.getAttribute("data-vat-percent") || "10");
    var ext = card.getAttribute("data-external-currency") === "1";
    var baseCur = (wrap && wrap.getAttribute("data-pi-currency")) || (ext ? "usd" : "rial");
    var factor = factorFor(card);
    var displayCur = previewCurrency(card, baseCur);
    var label = unitLabel(displayCur, ext);
    var subtotal = 0;
    var svcSum = 0;
    var hasSvc = false;

    if (table) {
      table.querySelectorAll("td[data-col-key='UNIT PRICE'], td[data-col-key='TOTAL PRICE'], td[data-col-key='SERVICE PRICE']").forEach(function (td) {
        var tr = td.closest("tr");
        var base = td.getAttribute("data-base-value");
        if (base == null || base === "") {
          base = td.textContent;
          td.setAttribute("data-base-value", base);
        }
        var raw = toNumber(base);
        var converted = raw * factor;
        var key = td.getAttribute("data-col-key");
        if (key === "SERVICE PRICE" && !raw) {
          td.textContent = "";
        } else {
          td.textContent = formatMoney(converted, displayCur, ext) + " " + label;
        }
        if (key === "TOTAL PRICE") {
          if (tr && (tr.classList.contains("row-soft-deleted") || tr.classList.contains("row-unsuppliable"))) return;
          subtotal += converted;
        }
      });
      table.querySelectorAll("tbody tr").forEach(function (tr) {
        if (tr.classList.contains("row-soft-deleted")) return;
        if (tr.classList.contains("row-unsuppliable")) return;
        var comment = (tr.getAttribute("data-svc-comment") || "").trim();
        if (!comment || /^(nan|none|<na>|null)$/i.test(comment)) return;
        hasSvc = true;
        var unit = toNumber(tr.getAttribute("data-svc-raw") || "");
        var qty = toNumber(tr.getAttribute("data-svc-qty") || "");
        if (!qty) qty = 1;
        svcSum += unit * qty * factor;
      });
    }

    var vat = subtotal * (vatPct / 100);
    var grand = subtotal + vat + svcSum;
    var subEl = card.querySelector(".pi-tot-sub");
    var vatEl = card.querySelector(".pi-tot-vat");
    var svcEl = card.querySelector(".pi-tot-svc");
    var svcBadge = card.querySelector(".pi-tot-svc-badge");
    var grandEl = card.querySelector(".pi-tot-grand");
    var vatLabel = card.querySelector(".pi-tot-vat-label");
    if (subEl) subEl.textContent = formatMoney(subtotal, displayCur, ext);
    if (vatEl) vatEl.textContent = formatMoney(vat, displayCur, ext);
    if (svcEl) svcEl.textContent = formatMoney(svcSum, displayCur, ext);
    if (svcBadge) svcBadge.hidden = !(hasSvc || svcSum > 0);
    if (grandEl) grandEl.textContent = formatMoney(grand, displayCur, ext);
    card.querySelectorAll(".pi-tot-unit").forEach(function (el) {
      el.textContent = label;
    });
    if (vatLabel) vatLabel.textContent = "VAT (" + String(vatPct).replace(/\.0+$/, "") + "%)";
    syncGate(card);
  }

  function openConversion(card) {
    var ext = card.getAttribute("data-external-currency") === "1";
    var from = lockFromSelect(card);
    var toSel = card.querySelector(".pi-conv-to");
    if (toSel) {
      if (ext) toSel.value = (from === "usd") ? "eur" : "usd";
      else toSel.value = (from === "rial") ? "usd" : (from === "usd" ? "eur" : "rial");
    }
    var rate = card.querySelector(".pi-conv-rate");
    if (rate) rate.value = "";
    var panel = card.querySelector(".pi-conv-panel");
    var gate = card.querySelector(".pi-conv-gate");
    var confirmBox = card.querySelector(".pi-conv-confirm");
    if (confirmBox) confirmBox.hidden = true;
    if (panel) panel.hidden = false;
    if (gate) {
      var openBtn = gate.querySelector(".pi-conv-open");
      if (openBtn) openBtn.hidden = true;
    }
    syncFxRate(card);
  }

  function showConfirmPrompt(card) {
    var confirmBox = card.querySelector(".pi-conv-confirm");
    if (confirmBox) confirmBox.hidden = false;
  }

  function hideConfirmPrompt(card) {
    var confirmBox = card.querySelector(".pi-conv-confirm");
    if (confirmBox) confirmBox.hidden = true;
  }

  function closeConversion(card, resetPreview) {
    var panel = card.querySelector(".pi-conv-panel");
    if (panel) panel.hidden = true;
    hideConfirmPrompt(card);
    if (resetPreview) {
      var table = visiblePiTable(card.closest(".tab-panel"));
      if (table) {
        table.querySelectorAll("td[data-col-key='UNIT PRICE'], td[data-col-key='TOTAL PRICE']").forEach(function (td) {
          var base = td.getAttribute("data-base-value");
          if (base != null) td.textContent = base;
        });
      }
    }
    syncGate(card);
    refreshCard(card);
  }

  function saveConversion(card) {
    var wrap = activeWrap(card.closest(".tab-panel"));
    if (!wrap) {
      alert("No Proforma version selected.");
      return;
    }
    if (wrap.getAttribute("data-pi-converted") === "1") {
      alert("This Proforma version was already converted.");
      return;
    }
    if (wrap.getAttribute("data-pi-is-current") !== "1") {
      alert("Only the current Proforma version can be converted.");
      return;
    }
    if (card._fxStale) {
      return;
    }
    var formId = wrap.getAttribute("data-pi-form-id");
    var from = lockFromSelect(card);
    var to = card.querySelector(".pi-conv-to")?.value || "";
    var rate = card.querySelector(".pi-conv-rate")?.value || "";
    if (!rate || !toNumber(rate) || from === to) {
      return;
    }
    var url = card.getAttribute("data-action-url");
    if (!url) return;
    var btn = card.querySelector(".pi-conv-save");
    if (btn) { btn.disabled = true; btn.textContent = "Saving…"; }
    var body = new URLSearchParams();
    body.set("action", "convert_pi_currency");
    body.set("form_id", formId);
    body.set("side", card.getAttribute("data-side") || "");
    body.set("from_unit", from);
    body.set("to_unit", to);
    fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-CSRFToken": csrfToken(),
        "X-Requested-With": "XMLHttpRequest"
      },
      body: body.toString()
    }).then(function (res) {
      return res.json().catch(function () { return { ok: res.ok }; }).then(function (data) {
        if (!res.ok || data.ok === false) {
          throw new Error((data && data.error) || "Save failed.");
        }
        window.location.reload();
      });
    }).catch(function (err) {
      if (btn) { btn.disabled = false; btn.innerHTML = '<i class="fa-solid fa-floppy-disk"></i> Confirm'; }
      alert(err.message || "Could not save conversion.");
    });
  }

  function bindCard(card) {
    if (card.dataset.bound === "1") return;
    card.dataset.bound = "1";
    card.addEventListener("click", function (ev) {
      if (ev.target.closest(".pi-conv-open")) {
        ev.preventDefault();
        showConfirmPrompt(card);
      } else if (ev.target.closest(".pi-conv-confirm-yes")) {
        ev.preventDefault();
        openConversion(card);
      } else if (ev.target.closest(".pi-conv-confirm-no")) {
        ev.preventDefault();
        hideConfirmPrompt(card);
      } else if (ev.target.closest(".pi-conv-cancel")) {
        ev.preventDefault();
        closeConversion(card, true);
      } else if (ev.target.closest(".pi-conv-save")) {
        ev.preventDefault();
        saveConversion(card);
      }
    });
    card.addEventListener("input", function (ev) {
      if (ev.target.closest(".pi-conv-to")) syncFxRate(card);
    });
    card.addEventListener("change", function (ev) {
      if (ev.target.closest(".pi-conv-to")) syncFxRate(card);
    });
    var panel = card.closest(".tab-panel");
    if (panel) {
      panel.addEventListener("click", function (ev) {
        if (ev.target.closest(".vchip")) {
          setTimeout(function () {
            closeConversion(card, false);
            refreshCard(card);
          }, 0);
        }
      });
    }
    refreshCard(card);
  }

  function bind() {
    document.querySelectorAll(".pi-comm-wrap").forEach(bindCard);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
