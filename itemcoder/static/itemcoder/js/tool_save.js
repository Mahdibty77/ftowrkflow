/* Collects the complete tool grid (all rows, including live Remark/Revision and
   calculation edits) and posts it back to the case as a TO/PI version.

   The virtual-scroll engine keeps every <tr> in memory (only a window is in the
   DOM at a time), so we read VirtualScrollEngine.getRows() rather than the
   visible tbody — otherwise off-screen rows would be lost. */
(function () {
  "use strict";
  var CFG = window.FT_TOOL_SAVE || {};

  /** Strip colour / highlight markup (and escaped markup) to plain FTCO text. */
  function plainFtcoText(htmlOrText) {
    var s = String(htmlOrText || "");
    if (!s) return "";
    var low = s.toLowerCase();
    if (low.indexOf("&lt;") !== -1
        && (low.indexOf("span") !== -1 || low.indexOf("bdi") !== -1 || low.indexOf("br") !== -1)) {
      try {
        var taUn = document.createElement("textarea");
        taUn.innerHTML = s;
        s = taUn.value;
      } catch (_e) {
        s = s.replace(/&lt;/gi, "<").replace(/&gt;/gi, ">").replace(/&amp;/gi, "&")
          .replace(/&quot;/gi, '"').replace(/&#39;/gi, "'");
      }
    }
    s = s.replace(/<br\s*\/?>/gi, " ").replace(/<[^>]+>/g, "");
    try {
      var ta2 = document.createElement("textarea");
      ta2.innerHTML = s;
      s = ta2.value;
    } catch (_e2) {
      s = s.replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
        .replace(/&quot;/g, '"').replace(/&#39;/g, "'");
    }
    return s.replace(/\s+/g, " ").trim();
  }

  function cellValue(td) {
    var colName = td.getAttribute("data-col-name") || "";
    if (colName === "#") {
      var hashTxt = td.querySelector(".client-no-text");
      if (hashTxt) return (hashTxt.textContent || "").trim();
      var raw = td.getAttribute("data-raw-hash");
      if (raw) return raw.trim();
    }
    if (colName === "Final Arranged Text") {
      // Prefer the stashed pre-flag description when Technical Problem emptied
      // the visible cell (issuePrevHtml). Do NOT prefer originalHtml over a
      // live editable textarea — that discarded user FTCO edits on Save.
      if (td.dataset.issuePrevHtml) {
        return plainFtcoText(td.dataset.issuePrevHtml);
      }
      var ftcoTa = td.querySelector(
        "textarea.ftco-desc-textarea, textarea.ftco-self-textarea"
      );
      if (ftcoTa) return ftcoTa.value;
      var tr = td.closest("tr");
      var userEdited = (td.dataset.userEdited === "1")
        || (tr && tr.getAttribute("data-ftco-user-edited") === "1");
      // Manual edits must persist as plain text — never colour <span> markup
      // (that markup was showing up as literal tags after reopen).
      if (userEdited) {
        return plainFtcoText(td.dataset.originalHtml || td.innerHTML || "");
      }
      if (td.dataset.originalHtml) return td.dataset.originalHtml;
      return td.innerHTML;
    }
    if (colName === "Filled_Features") {
      // ROOT CAUSE of the Group & Feature filter's value-parsing bug: this
      // cell is server-rendered as "key = <span>VALUE</span><br>key2 = ..."
      // (see final_arrange_builder.py / colored_display). Falling through to
      // the generic td.textContent handler below strips every <br> with NO
      // separator inserted in its place, gluing all key/value pairs into one
      // unbroken string on every save from that point forward — reopening a
      // saved row then hands the filter a value like
      // "material_group_pipe_pipe = C.Smaterial_type_pipe_pipe = SMLS..."
      // instead of two separate entries, which is what actually produced
      // values like "C." instead of "C.S". Preserving innerHTML here, the
      // same way Final Arranged Text already does above, keeps the <br>
      // separators intact so this cell round-trips through save/reload
      // exactly as it was originally rendered.
      return td.innerHTML;
    }
    var field = td.querySelector("textarea, input, select");
    if (field) {
      // SERVICE PRICE: persist the FINAL painted unit (FX + margins when the
      // Service toggle is ON) — same number the user sees in the PI grid.
      // The editable BASE stays in ``_service_price_raw`` (collected below).
      if (colName === "SERVICE PRICE") {
        var trSvc = td.closest("tr");
        var svcBase = (field.dataset && field.dataset.raw) || "";
        if (!svcBase && trSvc) svcBase = trSvc.getAttribute("data-service-price-raw") || "";
        if (!svcBase) svcBase = String(field.value || "").replace(/[^0-9.\-]/g, "");
        var baseSvc = parseFloat(String(svcBase).replace(/,/g, ""));
        if (!isFinite(baseSvc) || baseSvc === 0) return svcBase || "";
        var svcFactor = 1;
        if (window.CalcConversionFactor) {
          try {
            var cf = window.CalcConversionFactor();
            if (isFinite(cf) && cf > 0) svcFactor = cf;
          } catch (_e) {}
        }
        if (window.PIServiceFeatureOn && window.PIServiceFeatureOn()
            && window.CalcMarginFactor && trSvc) {
          try {
            var mf = window.CalcMarginFactor(trSvc, "unit_price");
            if (isFinite(mf) && mf > 0) svcFactor *= mf;
          } catch (_e2) {}
        }
        var svcFinal = baseSvc * svcFactor;
        return String(svcFinal);
      }
      // UNIT PRICE: persist the FINAL painted value (data-calc-value = base ×
      // FX × margins). Keep ``_unit_price_raw`` as the editable base so reopen
      // / margin restore never double-applies.
      if (colName === "UNIT PRICE") {
        var unitFinal = td.getAttribute("data-calc-value");
        if (unitFinal != null && String(unitFinal).trim() !== "") {
          return String(unitFinal).trim();
        }
        if (field.dataset && field.dataset.raw) return field.dataset.raw;
        return field.value;
      }
      return field.value;
    }
    // Calc cells (unit/total price): save the displayed number + unit only, never
    // the runtime "manual"/list source tag, so the saved proforma shows just the
    // value and unit.
    if (td.hasAttribute("data-calc-variable")) {
      // Prefer the numeric base (data-calc-value) so dual TOTAL PRICE overlays
      // (base + service line) never leak into the saved TOTAL PRICE.
      var calcVal = td.getAttribute("data-calc-value");
      if (calcVal != null && String(calcVal).trim() !== "") return String(calcVal).trim();
      var disp = td.querySelector(".calc-display-value");
      if (disp) return (disp.textContent || "").trim();
    }
    return (td.textContent || "").trim();
  }

  function columnOrder() {
    var ths = document.querySelectorAll("#virtual-scroll-header-row th[data-col-name]");
    var cols = [];
    ths.forEach(function (th) {
      var name = th.getAttribute("data-col-name");
      if (name && name.indexOf("__") !== 0 && cols.indexOf(name) === -1) cols.push(name);
    });
    return cols;
  }

  function collect() {
    var rows = (window.VirtualScrollEngine && window.VirtualScrollEngine.getRows)
      ? window.VirtualScrollEngine.getRows() : [];
    var cols = columnOrder();
    var table = rows.map(function (tr) {
      var obj = {};
      tr.querySelectorAll("td[data-col-name]").forEach(function (td) {
        var name = td.getAttribute("data-col-name");
        if (!name || name.indexOf("__") === 0) return;
        if (name === "proforma remark") return;   // read-only reference column
        obj[name] = cellValue(td);
        if (cols.indexOf(name) === -1) cols.push(name);
      });
      // Keep the per-row feature values so a reloaded TO/PI (and a PI seeded
      // from a TO) still carries data-vars for the in-tool feature filter.
      var rawVars = tr.getAttribute("data-vars");
      if (rawVars) { try { obj["Feature_Variables"] = JSON.parse(rawVars); } catch (e) {} }
      // Persist per-row flags so they survive save / reload.
      if (tr.getAttribute("data-unsuppliable") === "1") obj["_unsuppliable"] = "1";
      if (tr.getAttribute("data-issue") === "1") obj["_issue"] = "1";
      // Manual FTCO DISCRIPTION edit — only THIS row keeps the typed text.
      var ftcoTd = tr.querySelector('td[data-col-name="Final Arranged Text"]');
      if (
        tr.getAttribute("data-ftco-user-edited") === "1"
        || (ftcoTd && ftcoTd.dataset.userEdited === "1")
      ) {
        obj["_ftco_user_edited"] = "1";
      }
      var issueReason = tr.getAttribute("data-issue-reason") || "";
      if (issueReason) obj["_issue_reason"] = issueReason;
      // Service Price (PI only): the price itself is an ordinary column
      // (collected above); also persist the raw number + comment so reload /
      // edit / export restore exactly what the user typed.
      var serviceComment = (tr.getAttribute("data-service-comment") || "").trim();
      if (serviceComment && !/^(nan|none|<na>|null)$/i.test(serviceComment)) {
        obj["_service_comment"] = serviceComment;
      }
      var svcRaw = tr.getAttribute("data-service-price-raw") || "";
      if (!svcRaw) {
        var svcTd = tr.querySelector('td[data-col-name="SERVICE PRICE"] input');
        if (svcTd) svcRaw = (svcTd.dataset && svcTd.dataset.raw) || "";
      }
      if (svcRaw && obj["_service_comment"]) {
        // BASE for tool restore; SERVICE PRICE column (above) holds the final.
        obj["_service_price_raw"] = svcRaw;
      } else {
        delete obj["SERVICE PRICE"];
        delete obj["_service_price_raw"];
      }
      if (tr.getAttribute("data-deleted") === "1") obj["_deleted"] = "1";
      if (tr.getAttribute("data-added") === "1") obj["_added"] = "1";
      // Remark Old/New split (point 5): carry the read-only Old value + flag so
      // the server can promote a typed New remark and keep Old when New is blank.
      if (tr.getAttribute("data-remark-split") === "1") {
        obj["_remark_split"] = "1";
        obj["_prev_remark"] = tr.getAttribute("data-prev-remark") || "";
      }
      var pfAck = tr.getAttribute("data-pf-ack") || "";
      if (pfAck) obj["_pf_ack"] = pfAck;
      var remarkAck = tr.getAttribute("data-remark-ack") || "";
      if (remarkAck) obj["_remark_ack"] = remarkAck;
      if (tr.getAttribute("data-pf-pending") === "1") {
        obj["_pf_pending"] = "1";
        var pfText = tr.getAttribute("data-pf-text") || "";
        if (pfText) obj["_pf_text"] = pfText;
      }
      // Brand Old/New split: carry Prev + ack/pending so the server can promote.
      // Persist _brand_ack whenever present (including PI absorb marker on
      // non-split rows) — but never invent empty acks for every row.
      if (tr.getAttribute("data-brand-split") === "1") {
        obj["_brand_split"] = "1";
        obj["_prev_brand"] = tr.getAttribute("data-prev-brand") || "";
      }
      if (tr.hasAttribute("data-brand-ack")) {
        var brandAckVal = tr.getAttribute("data-brand-ack") || "";
        // TO: only with an active split (promote rewrites non-split acks).
        // PI: also keep absorb marker on non-split rows after handoff collapse.
        if (tr.getAttribute("data-brand-split") === "1") {
          obj["_brand_ack"] = brandAckVal;
        } else if (String(CFG.kind || "").toUpperCase() === "PI" && brandAckVal !== "") {
          obj["_brand_ack"] = brandAckVal;
        }
      }
      if (tr.getAttribute("data-brand-pending") === "1") {
        obj["_brand_pending"] = "1";
        var brandPf = tr.getAttribute("data-brand-pf-text") || "";
        if (brandPf) obj["_brand_pf_text"] = brandPf;
      }
      if (tr.hasAttribute("data-brand-baseline")) {
        obj["_brand_baseline"] = tr.getAttribute("data-brand-baseline") || "";
      }
      // Persist the UNIT PRICE source label (Manual / list name) and the raw
      // numeric so the chip and value come back exactly after save → edit.
      var upTd = tr.querySelector('td[data-col-name="UNIT PRICE"], td[data-calc-variable="unit_price"]');
      if (upTd) {
        var src = upTd.getAttribute("data-price-source") || "";
        if (src) obj["_price_source"] = src;
        var base = upTd.getAttribute("data-calc-base") || upTd.getAttribute("data-calc-raw") || "";
        if (!base) {
          var upInp = upTd.querySelector("input.pi-unit-input");
          if (upInp && upInp.dataset && upInp.dataset.raw) base = upInp.dataset.raw;
        }
        if (base !== "") obj["_unit_price_raw"] = base;
      }
      return obj;
    });
    return { columns: cols, table: table };
  }

  // ---- Edit mode: Save only when the grid actually changed ----
  var initialSig = null;
  var dirty = false;
  var dirtyTimer = null;

  function isEditMode() {
    return String(CFG.mode || "build").toLowerCase() === "edit";
  }

  function tableSignature() {
    try {
      return JSON.stringify(collect().table);
    } catch (_e) {
      return "";
    }
  }

  function syncSaveBtn() {
    var btn = document.getElementById("tool-save-btn");
    if (!btn || btn.dataset.saving === "1") return;
    if (!isEditMode()) {
      btn.disabled = false;
      btn.removeAttribute("title");
      return;
    }
    btn.disabled = !dirty;
    btn.title = dirty ? "" : "Make at least one change before saving";
  }

  function refreshDirty() {
    if (!isEditMode() || initialSig == null) return;
    dirty = tableSignature() !== initialSig;
    syncSaveBtn();
  }

  function scheduleDirtyCheck() {
    if (!isEditMode()) return;
    if (dirtyTimer) clearTimeout(dirtyTimer);
    dirtyTimer = setTimeout(refreshDirty, 120);
  }

  function captureInitialSignature() {
    if (!isEditMode()) return;
    try {
      initialSig = tableSignature();
      dirty = false;
      syncSaveBtn();
    } catch (_e) {}
  }

  function save() {
    // Unit conversion guard: if the user asked to convert to another currency
    // (From ≠ To) but left the rate empty, refuse to save and flag the field.
    if (window.CalcConversionNeedsRate && window.CalcConversionNeedsRate()) {
      if (window.CalcSyncConversion) window.CalcSyncConversion();
      var rateEl = document.getElementById("calc-convert-rate");
      if (rateEl) { try { rateEl.focus(); } catch (e) {} }
      alert("Confirm a valid To currency before saving, or leave To equal to From.");
      return;
    }
    if (isEditMode()) {
      refreshDirty();
      if (!dirty) {
        alert("No changes to save. Edit at least one cell first.");
        syncSaveBtn();
        return;
      }
    }
    var data = collect();
    if (!data.table.length) { alert("There are no rows to save."); return; }
    var meta = {
      "DOC NO.": document.querySelector(".tool-bar .doc") ? document.querySelector(".tool-bar .doc").textContent : "",
    };
    // Effective display currency + full calc state (conversion + margins) so the
    // version stores exactly what was shown and can be restored / carried over.
    if (window.CalcCurrentCurrency) { try { meta.currency = window.CalcCurrentCurrency(); } catch (e) {} }
    if (window.CalcSerializeState) { try { meta.calc = window.CalcSerializeState(); } catch (e) {} }
    var body = new URLSearchParams();
    body.set("columns", JSON.stringify(data.columns));
    body.set("table", JSON.stringify(data.table));
    body.set("meta", JSON.stringify(meta));
    body.set("mode", CFG.mode || "build");

    var btn = document.getElementById("tool-save-btn");
    btn.dataset.saving = "1";
    btn.disabled = true; var label = btn.textContent; btn.textContent = "Saving…";
    fetch(CFG.saveUrl, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded", "X-CSRFToken": CFG.csrfToken },
      body: body.toString(),
      redirect: "follow"
    }).then(function (res) {
      if (res.redirected) { window.location = res.url; return; }
      return res.text().then(function () { window.location = CFG.saveUrl.replace(/\/tool\/case\/(\d+)\/.*$/, "/cases/$1/"); });
    }).catch(function () {
      btn.dataset.saving = "";
      btn.textContent = label;
      refreshDirty();
      alert("Could not save. Please try again.");
    });
  }

  function bind() {
    var btn = document.getElementById("tool-save-btn");
    if (btn && !btn.dataset.bound) { btn.dataset.bound = "1"; btn.addEventListener("click", save); }
    if (!document.documentElement.dataset.ftSaveDirtyBound) {
      document.documentElement.dataset.ftSaveDirtyBound = "1";
      document.addEventListener("input", scheduleDirtyCheck, true);
      document.addEventListener("change", scheduleDirtyCheck, true);
      document.addEventListener("click", function (e) {
        var t = e.target;
        if (!t || !t.closest) return;
        if (t.closest(".brd-pf-btn, .rmk-pf-btn, .pi-btn, #svc-card")) {
          scheduleDirtyCheck();
        }
      }, true);
      // Snapshot after the grid / pricing / split UI finish initialising.
      setTimeout(captureInitialSignature, 400);
      setTimeout(captureInitialSignature, 1200);
    }
  }
  bind();
  document.addEventListener("DOMContentLoaded", bind);
})();
