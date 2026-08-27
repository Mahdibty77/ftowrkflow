/* Collects the complete tool grid (all rows, including live Remark/Revision and
   calculation edits) and posts it back to the case as a TO/PI version.

   The virtual-scroll engine keeps every <tr> in memory (only a window is in the
   DOM at a time), so we read VirtualScrollEngine.getRows() rather than the
   visible tbody — otherwise off-screen rows would be lost. */
(function () {
  "use strict";
  var CFG = window.FT_TOOL_SAVE || {};

  // View-only mode (a unit looking at its own form while the case sits with
  // somebody else). The template renders no Save button at all, and the save
  // endpoint refuses this seat regardless — see _may_build_form in bridge.py,
  // which is the same check that set this flag. Bailing out here means no
  // collector, no dirty-tracking listeners, and no code path that could post.
  if (CFG.readOnly) return;

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

  // Bumped by every listener that could have changed a cell. It is a counter,
  // NOT a signature: comparing two integers is free, so the autosave below can
  // ask "did anything happen while my POST was in the air?" without paying for
  // a second collect()+JSON.stringify of a 380-row grid. (The debounced
  // refreshDirty that follows the same event is still what decides `dirty`
  // authoritatively; this only closes the in-flight window.)
  var changeSeq = 0;

  function scheduleDirtyCheck() {
    if (!isEditMode()) return;
    changeSeq++;
    if (dirtyTimer) clearTimeout(dirtyTimer);
    dirtyTimer = setTimeout(refreshDirty, 120);
  }

  function captureInitialSignature() {
    if (!isEditMode()) return;
    // Settle calc-derived cells (TOTAL PRICE, UNIT PRICE, …) into their
    // steady-state representation before reading the baseline. Until the
    // grid's own calc engine (calculation_controls.js / pi_pricing.js) has
    // run at least once, a data-calc-variable cell with no data-calc-value
    // attribute yet falls back to its server-rendered textContent in
    // cellValue() (e.g. "0 Rial"); the moment ANY later ft-calc-refreshed
    // fires — a real price edit, or just a flag toggle, since both flag
    // paths in item_flag.js dispatch it — that attribute gets populated and
    // the same cell serialises differently from then on (e.g. "0"). Without
    // this nudge that one-time, irreversible format shift would show up as
    // a phantom diff against initialSig, so toggling a flag back OFF (a
    // true no-op) would leave Save stuck enabled instead of disabling again.
    // Firing the grid's own "recompute now" signal here — the same event
    // pi_unsuppliable.js / item_flag.js / calculation_controls.js already
    // dispatch for this exact purpose — makes the baseline match whatever
    // representation every later signature will also see. It only settles
    // display/serialisation timing, not any calculation rule, and changes
    // nothing about what save() eventually posts.
    try { document.dispatchEvent(new CustomEvent("ft-calc-refreshed")); } catch (_e0) {}
    try {
      initialSig = tableSignature();
      dirty = false;
      syncSaveBtn();
    } catch (_e) {}
  }

  /** Where a successful save lands: the case page. */
  function caseUrl() {
    return CFG.saveUrl.replace(/\/tool\/case\/(\d+)\/.*$/, "/cases/$1/");
  }

  /** The POST body for one save. One builder for both kinds of save, so the
      two can never drift into posting different payloads — they differ in
      exactly one field, ``intent``, which says WHO asked:

        intent=manual  a person pressed Save. The endpoint behaves exactly as
                       it always has, including creating a new version when the
                       form has fallen behind its inquiry.
        intent=auto    the five-minute timer. The endpoint refuses this one
                       (409) if it would create a version instead of overwriting
                       the current one — see save_from_tool. A timer must never
                       publish a version nobody asked for.

      The flag is declared on BOTH paths rather than only on the automatic one,
      so a save that reaches the server carrying no intent at all is a bug or a
      forged request, not an ordinary press of Save. */
  function buildBody(data, intent) {
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
    body.set("intent", intent === "auto" ? "auto" : "manual");
    return body;
  }

  // Exactly one save may be in the air at a time — manual or automatic. The
  // endpoint replaces the whole version record, so two overlapping POSTs would
  // race to be last writer and the loser's rows would vanish.
  var inFlight = false;
  var pendingManual = false;   // user pressed Save while an autosave was flying

  // The Save button's own label ("Save TO" / "Save PI"), read ONCE at bind time,
  // before anything can overwrite it with "Saving…". It used to be captured
  // inside save() from the live textContent, which was correct only as long as
  // exactly one thing could ever put the button into its busy state. It is not:
  // a press of Save DURING an automatic save now marks the button busy at press
  // time (see below) and the real POST follows afterwards, so a capture inside
  // save() would have read "Saving…" and a failure would then have restored the
  // button to the word "Saving…" for good.
  var saveBtnLabel = "";

  function markSaveBusy(btn) {
    btn = btn || document.getElementById("tool-save-btn");
    if (!btn) return;
    btn.dataset.saving = "1";
    btn.disabled = true;
    btn.textContent = "Saving…";
  }

  function clearSaveBusy(btn) {
    btn = btn || document.getElementById("tool-save-btn");
    if (!btn) return;
    btn.dataset.saving = "";
    btn.disabled = false;
    if (saveBtnLabel) btn.textContent = saveBtnLabel;
    // syncSaveBtn() is what decides whether Save is actually pressable (edit
    // mode + dirty); calling it here means no exit path can leave the button in
    // a state the rest of this file did not choose.
    syncSaveBtn();
  }

  function save() {
    var btn = document.getElementById("tool-save-btn");
    // Unit conversion guard: if the user asked to convert to another currency
    // (From ≠ To) but left the rate empty, refuse to save and flag the field.
    //
    // It runs BEFORE the in-flight check on purpose. A press of Save while an
    // automatic save is in the air has to be answered exactly as a press at any
    // other moment is, and this is the one answer that must not be deferred:
    // deferring it would accept the press, show "Saving your changes…", and only
    // minutes later — when the autosave settled — pop the alert and focus a
    // field the user had long since moved away from. Nothing about the guard
    // itself changes, and for the ordinary press (nothing in flight) the order
    // of the two checks makes no difference at all.
    if (window.CalcConversionNeedsRate && window.CalcConversionNeedsRate()) {
      if (window.CalcSyncConversion) window.CalcSyncConversion();
      var rateEl = document.getElementById("calc-convert-rate");
      if (rateEl) { try { rateEl.focus(); } catch (e) {} }
      clearSaveBusy(btn);
      alert("Confirm a valid To currency before saving, or leave To equal to From.");
      return;
    }
    // An autosave is already posting this grid. Do not start a second POST
    // (see inFlight above); remember that the user asked, and finish their
    // request the moment the autosave settles. The button goes into the SAME
    // busy state a normal press puts it in, so the press looks like what it is —
    // a save in progress — instead of leaving an enabled button that invites the
    // user to press it again (every extra press only re-set the same flag).
    if (inFlight) {
      pendingManual = true;
      markSaveBusy(btn);
      showToast("Saving your changes…", "info", 6000);
      return;
    }
    if (isEditMode()) {
      refreshDirty();
      if (!dirty) {
        clearSaveBusy(btn);
        alert("No changes to save. Edit at least one cell first.");
        return;
      }
    }
    var data = collect();
    if (!data.table.length) { clearSaveBusy(btn); alert("There are no rows to save."); return; }
    var body = buildBody(data, "manual");

    inFlight = true;
    markSaveBusy(btn);
    fetch(CFG.saveUrl, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded", "X-CSRFToken": CFG.csrfToken },
      body: body.toString(),
      redirect: "follow"
    }).then(function (res) {
      if (res.redirected) { window.location = res.url; return; }
      // Permission failures redirect (handled above), but a server error or a
      // stale CSRF token comes back as a plain 500/403 with res.redirected
      // false. Navigating away on those would show the PREVIOUS version with no
      // error while the only copy of the priced grid — it lives solely in the
      // page's memory, see collect() — is discarded. Fail into .catch instead.
      if (!res.ok) throw new Error("save failed: HTTP " + res.status);
      return res.text().then(function () { window.location = caseUrl(); });
    }).catch(function () {
      inFlight = false;
      // A press of Save that arrived while THIS save was in the air has now been
      // answered by this same attempt's failure; do not run a second one on top
      // of the alert below.
      pendingManual = false;
      // clearSaveBusy() restores the label and re-enables the button. That
      // matters in build / newversion, where syncSaveBtn() alone leaves Save
      // disabled: a failed attempt used to leave it permanently dead and the
      // only way forward was a reload that threw the session's work away.
      refreshDirty();
      clearSaveBusy(btn);
      alert("Could not save. Please try again.");
    });
  }

  // =====================================================================
  //  The transient message strip
  // =====================================================================
  // Matches the app's existing message language — the .flash / flash-success /
  // flash-error classes base.html renders Django messages with — rather than
  // inventing a new one. Two deliberate differences from a Django flash:
  //
  //  * it is position:fixed, so appearing and disappearing moves NOTHING on the
  //    page (an in-flow banner would shove a 380-row grid down by its height
  //    twice per message, which on this page is both a visible jump and a
  //    forced relayout of the virtual-scroll viewport), and
  //  * it is pointer-events:none, so even in the instant it is on screen it
  //    cannot swallow a click aimed at a control underneath it.
  //
  // It sits centred over the tool bar's flexible spacer — the one strip at the
  // top of this page that holds no control at all.
  var toastTimer = null;

  function showToast(text, kind, ms) {
    var el = document.getElementById("tool-autosave-toast");
    if (!el) return;
    var txt = el.querySelector(".ft-autosave-text");
    var icon = el.querySelector("i");
    if (txt) txt.textContent = text; else el.textContent = text;
    if (icon) {
      icon.className = "fa-solid " + (kind === "error"
        ? "fa-triangle-exclamation"
        : (kind === "info" ? "fa-circle-info" : "fa-circle-check"));
    }
    el.classList.remove("is-ok", "is-error", "is-info");
    el.classList.add("is-" + (kind || "ok"));
    el.classList.add("is-on");
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.classList.remove("is-on"); },
                            ms || 4000);
  }

  // =====================================================================
  //  Automatic save
  // =====================================================================
  // WHY IT IS SAFE TO LET THIS FIRE BY ITSELF — the one thing that had to be
  // proved first is what a repeat save does on the server:
  //
  //   bridge.save_from_tool maps the posted ``mode`` to save_form's arguments:
  //     mode=build       -> new_version = (current is not None)
  //     mode=newversion  -> new_version = True
  //     mode=edit        -> new_version = False   (and is_edit=True)
  //   and services.save_form with new_version False resolves ``version`` to the
  //   CURRENT version and then does
  //     CaseForm.objects.get_or_create(case, kind, side, version, two_stage)
  //   so the second and every later save of the same version REUSES the same
  //   CaseForm row and overwrites its columns/table/meta. No new version, no
  //   new row.
  //
  // Everything below follows from that:
  //
  //  * EDIT MODE ONLY. In build mode the very same code says
  //    ``new_version = current is not None`` — a build-mode POST against a form
  //    that already exists creates a WHOLE NEW VERSION. An automatic save in
  //    build mode would therefore mint 01, 02, 03 … every five minutes. And a
  //    build that has never been saved has no version to save into: the first
  //    POST would create one behind the user's back and publish work they had
  //    not decided to publish. So autosave never runs unless CFG.mode is
  //    exactly "edit".
  //  * "edit" from the server already excludes the branch cases. tool_for_case
  //    normalises the mode BEFORE rendering it into FT_TOOL_SAVE: with no
  //    current form it renders "build", and when the form is BEHIND its inquiry
  //    (a higher inquiry number, or a two-stage generation change at the same
  //    number — services.form_behind_inquiry) it renders "newversion". So a
  //    page that says mode=edit is a page whose save WAS an in-place overwrite
  //    AT THE MOMENT IT WAS RENDERED — and that is a snapshot, not a promise.
  //  * WHAT MODE=EDIT DOES NOT COVER, AND HOW IT IS CLOSED. A tool tab can sit
  //    open for hours. If a new Inquiry version lands from another session in
  //    that time, this page still holds mode=edit while the form behind it has
  //    become stale, and save_from_tool then upgrades edit -> newversion (and
  //    services.save_form independently resolves version = inq_version). The
  //    arithmetic is right — a form built against an old inquiry genuinely
  //    needs a new version, and that is exactly what a manual Save should still
  //    do — but a TIMER must never publish one. So every POST from this file
  //    now declares WHO asked for it (buildBody's ``intent``), and the endpoint
  //    refuses an ``intent=auto`` save that would create a version, answering
  //    409 with a reason and writing nothing at all. The check lives on the
  //    server because that is the side a stale tab cannot lie to: this page's
  //    idea of the mode is by definition the thing that has gone out of date.
  //    Nothing is polled to discover this and no request is added to the
  //    ordinary path — form_behind_inquiry was already being computed inside
  //    save_from_tool on every save — and the refused path is actually one
  //    request SHORTER, because a 409 needs no confirmation fetch.
  //  * READ-ONLY seats never reach here: the whole module returns at the top on
  //    CFG.readOnly, so there is no timer, no collector and no code path that
  //    could post. (The endpoint refuses that seat too — _may_build_form.)
  //  * A CLEAN GRID NEVER POSTS. The tick reads the existing `dirty` boolean —
  //    the same flag that gates the Save button — and returns. It does NOT
  //    compute a signature to find out; that would put a collect() +
  //    JSON.stringify of the whole grid on a repeating timer, which is exactly
  //    the periodic cost this page cannot afford.
  //  * NOTHING RUNS WHILE THE TAB IS HIDDEN. The timer is cleared on
  //    visibilitychange and re-armed for its remaining time when the tab comes
  //    back, so a backgrounded tool is completely inert.

  var AUTOSAVE_MS = 5 * 60 * 1000;   // five minutes — the shipped interval
  var SETTLE_MS = 3000;              // grace after the tab comes back / after IME
  var MAX_IME_DEFERRALS = 10;        // ≤ 10 s of waiting for a composition
  var MAX_FAILURES = 3;              // then stop and say so, rather than retry forever

  var autoTimer = null;
  var nextDueAt = 0;
  var failures = 0;
  var stopped = false;
  var composing = false;             // IME (Persian) composition in progress
  var imeDeferrals = 0;
  var lastVerdict = null;            // what the last automatic save concluded

  function autosaveEnabled() {
    // CFG.readOnly already returned at the top of the module; this is the
    // second condition, and it is the one that keeps versions safe.
    return isEditMode() && !stopped;
  }

  function scheduleAutosave(ms) {
    if (autoTimer) { clearTimeout(autoTimer); autoTimer = null; }
    if (!autosaveEnabled()) return;
    nextDueAt = Date.now() + ms;
    // A hidden tab arms nothing at all; visibilitychange arms it for whatever
    // is left of this interval when the tab comes back.
    if (document.hidden) return;
    autoTimer = setTimeout(autosaveTick, ms);
  }

  /** Tags stripped, entities decoded, whitespace collapsed. */
  function messageText(html) {
    var s = String(html || "").replace(/<[^>]+>/g, "");
    try {
      var ta = document.createElement("textarea");
      ta.innerHTML = s;          // textarea is RCDATA: decodes, builds no nodes
      s = ta.value;
    } catch (_e) {}
    return s.replace(/\s+/g, " ").trim();
  }

  /** Every message the app's own strip is showing, as {kind, text} pairs.
   *
   *  core/templates/base.html renders the session's Django messages as
   *
   *    <div class="messages">
   *      <div class="flash flash-success"><i …></i><span>PI (External) saved.</span></div>
   *    </div>
   *
   *  so the class carries the level and the <span> carries the text. A flash
   *  holds no nested <div>, which is why the first </div> closes it.
   *
   *  Returns ``null`` when the page carried NO message block at all. That is a
   *  different answer from "a block with nothing in it" and the caller treats
   *  it as such: nothing was queued yet, which is evidence about timing, not
   *  about the save. */
  function readMessages(html) {
    var body = String(html || "");
    var at = body.indexOf('class="messages"');
    if (at === -1) return null;
    var win = body.slice(at, at + 8000);
    var re = /<div[^>]*class="flash\s+flash-([a-z0-9 _-]*)"[^>]*>([\s\S]{0,800}?)<\/div>/gi;
    var out = [];
    var m;
    while ((m = re.exec(win))) {
      var lvl = String(m[1] || "").trim().split(/\s+/)[0].toLowerCase();
      var sp = /<span[^>]*>([\s\S]{0,600}?)<\/span>/i.exec(m[2]);
      out.push({ kind: lvl, text: messageText(sp ? sp[1] : m[2]) });
    }
    return out;
  }

  // THE COMPLETE SET OF SENTENCES ONE save_from_tool POST CAN QUEUE.
  //
  // Read off the endpoint: it queues EXACTLY ONE message on every path that ends
  // in its 302, and there are only five of them —
  //
  //   stored   messages.success(f"{kind}{label} saved.")
  //            ``label`` is " (Internal)" / " (External)" whenever a side was
  //            resolved, which is always when this page carries one.
  //   refused  "Nothing was saved: this case has a separate Internal and
  //             External side, …"        (the POST named no side)
  //            "You are not allowed to build this form right now."   ┐ the two
  //            "You can only work on your own side of this case."    ┘ texts
  //                                                     _may_build_form returns
  //            "The tool sent malformed data; nothing was saved."
  //
  // (Its remaining answer, the 409, never reaches the queue at all — it is read
  // straight off the POST's own response.)
  //
  // Matching against that CLOSED SET is what turns "a message exists" into "this
  // save's message". The queue was emptied immediately before the POST, so the
  // one sentence out of these five that is on the confirmation page afterwards
  // is this save's. Anything else on that page — a message from another tab, an
  // unrelated app message queued in the window — matches none of them, is never
  // read as a verdict in either direction, and only makes the reading
  // "unconfirmed". The previous rule was "the first message with an error level
  // is this save's refusal", which had no way to tell an unrelated error apart
  // from this endpoint's own.
  var SAVED_RE = /^(TO|PI)(?:\s*\(([^)]*)\))?\s+saved\.$/i;

  var REFUSAL_SENTENCES = [
    /^Nothing was saved: this case has a separate Internal and External side\b/i,
    /^You are not allowed to build this form right now\.?$/i,
    /^You can only work on your own side of this case\.?$/i,
    /^The tool sent malformed data; nothing was saved\.?$/i
  ];

  /** The side label this page's own save would carry, or null when this page
   *  resolved no side (then the label cannot be checked and is not). */
  function expectedSideLabel() {
    var s = String(CFG.side || "").toUpperCase();
    if (s === "INTERNAL") return "internal";
    if (s === "EXTERNAL") return "external";
    return null;
  }

  /** Is this the success sentence THIS save would have produced — right kind
   *  AND right side? A split case runs two independent documents, so "PI
   *  (External) saved." from the other tab must not vouch for this one. */
  function isThisSaveStored(m) {
    if (m.kind !== "success") return false;
    var g = SAVED_RE.exec(m.text);
    if (!g) return false;
    if (g[1].toUpperCase() !== String(CFG.kind || "").toUpperCase()) return false;
    var want = expectedSideLabel();
    if (want === null) return true;
    return String(g[2] || "").trim().toLowerCase() === want;
  }

  /** Is this one of the four sentences the SAVE endpoint refuses with? */
  function isThisSaveRefusal(m) {
    if (m.kind !== "error" && m.kind !== "danger") return false;
    for (var i = 0; i < REFUSAL_SENTENCES.length; i++) {
      if (REFUSAL_SENTENCES[i].test(m.text)) return true;
    }
    return false;
  }

  // WHERE THE OUTCOME IS READ FROM, AND WHY IT IS NOT THE CASE PAGE.
  //
  // Apart from the 409 it reserves for a refused TIMER, the save endpoint
  // answers EVERY outcome the same way — a 302 to the case page — for a stored
  // grid and for a refusal alike, so the status line alone cannot tell those
  // apart; the answer has to be read off a rendered Django flash.
  // But the case page is the most expensive page in the product. Measured on
  // the 377-row PI used to verify this file (DEBUG off, so it is not a
  // development artefact): the save POST itself costs ~40 ms, while rendering
  // /cases/3/ costs ~2.5-2.9 s of server CPU and ~3.8 MB. fetch's default
  // redirect:"follow" would make EVERY autosave pay that — twelve times an
  // hour, per open tool tab, for a page nobody ever looks at. On an app
  // already under scrutiny for exactly this cost that is not acceptable.
  //
  // So the autosave POST does NOT follow its redirect (redirect:"manual" hands
  // back an opaque response and the browser never requests the target), and
  // the verdict is read off the INBOX instead. It extends the same
  // core/templates/base.html, so it renders the same `.flash flash-*` block
  // from the same message queue — and it costs ~32 KB / ~16-60 ms. Reading it
  // is also what CONSUMES the queued "PI saved." message, so a stack of them
  // cannot ambush the user on their next click. Every seat reaches it: the
  // roles whose home is elsewhere (admin -> /accounts/console/) are redirected
  // to another base.html page that renders the same flash, which is why the
  // confirmation GET follows redirects even though the POST does not.
  //
  // Net effect per autosave cycle on that 377-row PI: ~2.5 s + 3.8 MB of
  // server work removed, ~60 ms + 32 KB kept.
  var CONFIRM_URL = "/cases/";

  // WHOSE MESSAGE IS IT? — the queue is EMPTIED before every automatic POST.
  //
  // Django's message queue is per SESSION and one-shot: it holds whatever any
  // earlier request from any tab queued, until some page renders it. Reading a
  // message out of it therefore says nothing about who put it there. That was
  // real and it was dangerous in both directions: an unrelated error left over
  // from an earlier request painted a red "your changes are NOT saved" over an
  // autosave that had stored perfectly, and an unrelated success sitting in the
  // queue could vouch for a save that had in fact been refused. Telling the
  // user something untrue about whether their work is safe is the one thing
  // this feature must never do.
  //
  // So attribution is established rather than assumed, in three steps:
  //
  //   1. DRAIN. Immediately before the POST, the confirmation page is fetched
  //      once and thrown away. Rendering it is what CONSUMES the queue (see
  //      base.html's {% for message in messages %}), so the queue is empty at
  //      the moment the save is sent, and ``queueDrained`` records that it
  //      really was — a drain that failed makes every later reading "unknown"
  //      instead of a guess.
  //   2. POST. save_from_tool queues exactly ONE message for this request on
  //      every path that ends in its 302 — "PI saved." / "TO (Internal) saved."
  //      on success, the refusal text or "The tool sent malformed data" when it
  //      stored nothing. (Its third answer, the 409, never reaches the queue at
  //      all and is read straight off the response below.)
  //   3. CONFIRM and MATCH AGAINST A CLOSED SET. The confirmation page is
  //      fetched again and every message on it is matched against the five
  //      sentences this endpoint can queue for one POST (see REFUSAL_SENTENCES
  //      and isThisSaveStored below) — success for THIS kind AND THIS side, or
  //      one of its four refusal texts. A correct reading has exactly one such
  //      candidate; anything else — a foreign message, or two candidates
  //      because another request landed one inside the window — is reported as
  //      "could not be confirmed" and is never read as a verdict in either
  //      direction.
  //
  // Cost: one extra ~32 KB / ~16-60 ms GET per automatic save, i.e. twelve
  // times an hour per open tool tab. That is the price of the answer being
  // about this request; it is still ~2.5 s and ~3.8 MB per cycle cheaper than
  // following the POST's redirect to the case page even once (see above).
  //
  // NONE OF THIS IS NEEDED once the endpoint answers an automatic save on its
  // own terms. It already answers a refusal with a 409 that carries its reason;
  // if it also answered a stored automatic save with a plain
  // ``{"ok": true, "saved": true}`` (status 200) instead of the shared 302, the
  // verdict would arrive on the POST itself, both GETs would disappear, and the
  // message queue would stop being consulted at all. The reader for that answer
  // is already here (see the JSON branch in runAutosave), so the day the server
  // sends it this file uses it and the drain/confirm pair below becomes dead
  // weight that can be deleted. A MANUAL save is untouched by any of it.
  var queueDrained = false;

  /** Empty the session's message queue so the next reading can only contain
   *  messages queued after this point. Never rejects: a failed drain is
   *  recorded (``queueDrained`` stays false) and downgrades the verdict to
   *  "unknown" rather than stopping the save — storing the work matters more
   *  than reporting on it, and an unconfirmed save is reported as unconfirmed. */
  function drainMessages() {
    queueDrained = false;
    // ``no-store`` on BOTH the drain and the confirmation below. Django sends no
    // Cache-Control on these pages, which leaves a browser free to serve either
    // of them from its heuristic cache — and a cached copy carries the message
    // block as it stood when it was cached. A drain that never reached the
    // server would not empty anything, and a confirmation served from cache
    // would report an old request's message as this save's. Both are exactly the
    // misattribution this whole section exists to prevent.
    return fetch(CONFIRM_URL, { redirect: "follow", credentials: "same-origin",
                                cache: "no-store" })
      .then(function (r) {
        return r.text().then(function () {
          // Rendering the page is what consumes the queue, so a drain only
          // counts when it landed on one of the app's own pages. A redirect to
          // the login or activation screen rendered no message block of the
          // app's, so the queue may still hold everything it held before.
          var u = String(r.url || "");
          queueDrained = !!r.ok
            && !/\/(accounts\/)?login\b/i.test(u)
            && !/\/activate\b/i.test(u);
        });
      })
      .catch(function () {});
  }

  /** Did that POST actually store the grid? Answer honestly or say "unknown".
   *
   *  ``res``/``text`` are the CONFIRMATION page (see CONFIRM_URL), not the POST
   *  — the POST's own response is an opaque redirect that carries nothing to
   *  read. A session that died shows up here just as well: the confirmation GET
   *  lands on the login page and ``res.url`` says so. */
  function verdict(res, text) {
    if (!res.ok) {
      // THIS IS THE CONFIRMATION PAGE'S STATUS, NOT THE SAVE'S. The POST was
      // sent, answered and finished before this GET was even made, so a 500
      // from /cases/ says exactly nothing about whether the grid was stored —
      // it is a fact about a different request. Reporting it as "your changes
      // are NOT saved" is the same misattribution the drain-and-match
      // machinery above exists to prevent, only pointing the other way: a save
      // that stored perfectly would be painted as lost, and the user would be
      // told something untrue about whether their work is safe. Say what is
      // actually known instead — and leave it retryable (no ``undrained``),
      // since unlike a failed drain a second reading really can settle it.
      return { ok: false, unknown: true,
               msg: "Auto-save could not be confirmed (the confirmation page "
                  + "answered " + res.status + "). Press Save to be sure." };
    }
    var url = String(res.url || "");
    if (/\/(accounts\/)?login\b/i.test(url)) {
      return { ok: false, fatal: true,
               msg: "Auto-save failed — you have been signed out. "
                  + "Sign in again in another tab, then press Save." };
    }
    if (/\/activate\b/i.test(url)) {
      return { ok: false, fatal: true,
               msg: "Auto-save failed — this installation is not activated. "
                  + "Your changes are NOT saved." };
    }
    if (!queueDrained) {
      // The queue could not be emptied before the POST, so anything on this
      // page may belong to an earlier request. Say so instead of attributing
      // somebody else's message to this save.
      return { ok: false, unknown: true, undrained: true,
               msg: "Auto-save could not be confirmed. Press Save to be sure." };
    }
    var msgs = readMessages(text);
    if (msgs === null || !msgs.length) {
      // No Django message block on the page at all. That is "unknown", and it
      // must NOT be read as a refusal: an earlier version fell back to scanning
      // the first 20 KB of the whole page for the strings
      // "flash-error"/"flash-danger", so any page whose markup or CSS merely
      // MENTIONS those class names would report a save that actually succeeded
      // as refused — and a refusal is fatal, which would stop automatic saving
      // for the rest of the session over nothing. Only a real message may
      // produce a verdict.
      return { ok: false, unknown: true,
               msg: "Auto-save could not be confirmed. Press Save to be sure." };
    }
    // Count how many of the messages on this page COULD be this save's — i.e.
    // how many belong to the closed set above. Because the queue was drained
    // immediately before the POST and the endpoint queues exactly one message
    // per request, a correct reading has exactly ONE candidate. Two candidates
    // means another request landed its own message inside the window and the
    // two can no longer be told apart; zero means this save's message has not
    // arrived yet (or the page carries only foreign messages). Both of those
    // are "unconfirmed" — never a verdict.
    var stored = 0;
    var refusals = [];
    for (var i = 0; i < msgs.length; i++) {
      if (isThisSaveStored(msgs[i])) stored++;
      else if (isThisSaveRefusal(msgs[i])) refusals.push(msgs[i]);
    }
    if (stored === 1 && refusals.length === 0) return { ok: true };
    if (stored === 0 && refusals.length === 1) {
      return { ok: false, fatal: true,
               msg: "Auto-save was refused: " + refusals[0].text
                  + " Your changes are NOT saved." };
    }
    // Either nothing on this page is one of this endpoint's own sentences, or
    // more than one is. Say so instead of picking one.
    return { ok: false, unknown: true,
             msg: "Auto-save could not be confirmed. Press Save to be sure." };
  }

  /** Read the outcome off the confirmation page — retrying ONCE if the page
   *  carried no message at all.
   *
   *  save_from_tool queues a message on EVERY path that ends in its 302,
   *  success and refusal alike, so a confirmation page with no message block is
   *  never evidence about the save — it means the message had not landed yet.
   *  It is delivered through Django's default FallbackStorage, i.e. a cookie
   *  set on the POST's 302, and a confirmation issued microseconds later can
   *  lose that race. Observed live: two saves a couple of seconds apart, where
   *  the second came back unconfirmed and (before this) counted as a failure.
   *  One short retry turns that timing artefact into the real answer instead of
   *  an alarm about a save that in fact stored perfectly.
   *
   *  A verdict of "unknown" because the DRAIN failed is not retried: the second
   *  reading would be exactly as unattributable as the first. */
  function confirmSave(isRetry) {
    return fetch(CONFIRM_URL, { redirect: "follow", credentials: "same-origin",
                                cache: "no-store" })
      .then(function (r) {
        return r.text().then(function (t) {
          var v = verdict(r, t);
          if (v.unknown && !v.undrained && !isRetry) {
            return new Promise(function (done) { setTimeout(done, 400); })
              .then(function () { return confirmSave(true); });
          }
          return v;
        });
      });
  }

  function autosaveFailed(v) {
    failures++;
    showToast(v.msg, "error", 9000);
    // dirty and initialSig are deliberately untouched: the grid still differs
    // from what the server holds, so Save stays enabled and the next tick will
    // try again. Nothing here may let the user believe the work is stored.
    if (v.fatal || failures >= MAX_FAILURES) {
      stopped = true;
      if (autoTimer) { clearTimeout(autoTimer); autoTimer = null; }
      setTimeout(function () {
        showToast("Automatic saving has stopped. Press Save to store your work.",
                  "error", 12000);
      }, 9500);
      return;
    }
    scheduleAutosave(AUTOSAVE_MS);
  }

  function runAutosave() {
    // The same guard the manual Save applies: a half-configured currency
    // conversion must not be stored. The manual path alerts and focuses the
    // rate field; an automatic one may do neither, so it says so and waits.
    if (window.CalcConversionNeedsRate && window.CalcConversionNeedsRate()) {
      showToast("Auto-save paused — confirm the To currency first.", "error", 7000);
      scheduleAutosave(AUTOSAVE_MS);
      return;
    }
    var data;
    try { data = collect(); } catch (_e) { data = null; }
    if (!data || !data.table.length) { scheduleAutosave(AUTOSAVE_MS); return; }

    // The signature of exactly what is being POSTed, taken from the payload we
    // already built — never a second collect(). On success this becomes the new
    // baseline, so anything typed while the request was in the air still counts
    // as unsaved (see seqAtPost below) instead of being marked clean and lost.
    var postedSig = null;
    try { postedSig = JSON.stringify(data.table); } catch (_e2) {}
    var seqAtPost = changeSeq;
    var body = buildBody(data, "auto");

    inFlight = true;
    showToast("Saving your changes…", "info", 4000);
    // Empty the one-shot message queue FIRST, so whatever is in it afterwards
    // was queued by the POST below and by nothing else. ``inFlight`` is already
    // set, so a press of Save during the drain is held exactly as it is during
    // the POST itself.
    drainMessages().then(function () {
      return fetch(CFG.saveUrl, {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded", "X-CSRFToken": CFG.csrfToken },
        body: body.toString(),
        redirect: "manual"
      });
    }).then(function (res) {
      // Under redirect:"manual" a 3xx arrives as an OPAQUE response (type
      // "opaqueredirect", status 0, ok false) and the target is never fetched.
      // That is the normal outcome here — the endpoint redirects on success
      // and on refusal alike — so it is emphatically not an error. Any
      // non-redirect answer is a real status, and the endpoint only produces
      // one when the request never reached save_form at all (500, stale CSRF)
      // — or when it refused to let a TIMER create a version, below.
      //
      // 409 = "this automatic save would have created a VERSION, so I did not
      // run it". It is the one answer the endpoint gives an automatic save and
      // never gives a manual one, and it is not a failure of any kind: the
      // server deliberately wrote nothing. The reason travels in the body, so
      // the user is told the truth — what happened and what to do about it —
      // rather than a generic error. It is also the whole answer, and the only
      // one that needs no attribution: there is nothing to confirm, so a
      // refused tick costs one request fewer than a stored one (the drain and
      // the POST, and no confirmation).
      //
      // Deliberately fatal. Once the inquiry has moved ahead, every later tick
      // would be refused for exactly the same reason, so re-arming the timer
      // would only repeat the same message every five minutes. Automatic saving
      // stops, Save stays enabled (autosaveFailed leaves `dirty` alone), and
      // the decision to branch a new version goes back to the person — which is
      // the entire point.
      //
      // THE RED CONSOLE LINE, AND WHY IT STAYS — a decision, not an oversight.
      // A refused tick leaves "POST …/save/ 409 (Conflict)" in the browser
      // console. Nothing throws and nothing is broken: that line is the
      // browser's own network log of a real HTTP status, written before any
      // JavaScript sees the response, and there is no fetch option, no
      // try/catch and no handler that can suppress it — the only way to remove
      // it from the client side is for the server to stop answering 4xx.
      //
      // Weighed both ways: the status code is what makes a refusal unambiguous
      // here. It is the one answer a manual save can never receive, it needs no
      // confirmation request and no message-queue attribution at all, and it
      // cannot be mistaken for a success by any later change to this file.
      // Against that, the console line is cosmetic, is seen only with devtools
      // open, and appears AT MOST ONCE PER TOOL TAB: the refusal is fatal, so
      // automatic saving stops and no second 409 is ever requested. Trading an
      // unambiguous refusal for a tidier console is not a trade worth making,
      // so the 409 is kept exactly as it is.
      //
      // If it must go one day, the honest way is the JSON answer described in
      // the residual — status 200 with ``{"ok": false, "saved": false,
      // "reason": …}`` for every automatic outcome — which keeps the refusal
      // just as explicit in the body rather than pretending it succeeded; the
      // reader below already accepts that shape.
      if (res.type !== "opaqueredirect" && res.status === 409) {
        return res.json().catch(function () { return null; }).then(function (j) {
          return { ok: false, fatal: true,
                   msg: (j && j.reason)
                     || ("Auto-save skipped — saving now would create a new "
                       + "version. Nothing has been saved; press Save when you "
                       + "want to create it.") };
        });
      }
      // A JSON answer to the POST ITSELF is the whole verdict, and the only
      // kind of verdict that is about this request by construction rather than
      // by the attribution machinery above. The endpoint does not send one for
      // a stored automatic save today — success and refusal share the 302 — so
      // this branch is the reader for the answer described in the residual
      // (``{"ok": true, "saved": true}``, status 200, automatic saves only). It
      // is here so that the day the endpoint answers that way this file uses it
      // immediately, instead of draining, confirming and then reporting "could
      // not be confirmed" because a 200 carried no queued message.
      if (res.type !== "opaqueredirect" && res.ok
          && /\bjson\b/i.test(res.headers.get("content-type") || "")) {
        return res.json().then(function (j) {
          if (j && j.ok === true) return { ok: true };
          return { ok: false, fatal: true,
                   msg: (j && j.reason)
                     || "Auto-save was refused. Your changes are NOT saved." };
        }).catch(function () { return confirmSave(false); });
      }
      if (res.type !== "opaqueredirect" && !res.ok) {
        return { ok: false, msg: "Auto-save failed (server error " + res.status
                                + "). Your changes are NOT saved — press Save." };
      }
      return confirmSave(false);
    }).catch(function () {
      return { ok: false, msg: "Auto-save failed — no connection to the server. "
                             + "Your changes are NOT saved; they are still here." };
    }).then(function (v) {
      inFlight = false;
      lastVerdict = v;
      // Did anything change between the collect() that built this payload and
      // now? Read it ONCE, HERE, and use that reading for the rest of this
      // handler.
      //
      // It has to be read before anything below runs, because
      // scheduleDirtyCheck() — three lines down — increments ``changeSeq``
      // itself. Testing ``changeSeq === seqAtPost`` again after that call could
      // never be true, which silently killed the manual-Save branch further
      // down: pressing Save while an autosave was in flight fell through to a
      // second save() that found a grid identical to what had just been stored,
      // and answered the user's press with the modal "No changes to save. Edit
      // at least one cell first." — an alert about a save they had asked for
      // and that had in fact just happened.
      var untouchedSincePost = (changeSeq === seqAtPost);
      if (v.ok) {
        failures = 0;
        if (postedSig != null) initialSig = postedSig;
        // Anything the user touched while the POST was flying is still unsaved.
        dirty = !untouchedSincePost;
        syncSaveBtn();
        scheduleDirtyCheck();   // debounced; settles the button authoritatively
        var t = new Date();
        showToast("Saved automatically at " + ("0" + t.getHours()).slice(-2)
                  + ":" + ("0" + t.getMinutes()).slice(-2), "ok", 4000);
      }
      if (pendingManual) {
        // The user pressed Save while this was in the air. Honour it: on a
        // successful autosave their work is already stored, so finish the way
        // a manual save finishes — go to the case. Otherwise run the real
        // manual save so they get its own error handling.
        pendingManual = false;
        // Finish the way a manual save finishes ONLY when the automatic save
        // actually stored what the user asked to store. ``untouchedSincePost``
        // is the test: it says nothing was typed between the collect() that
        // built this payload and now. If a keystroke DID land in that window —
        // the request is only ~100 ms in the air, but a click on Save right
        // after typing lands squarely inside it — navigating away here would
        // leave those characters in a page that is about to be discarded, i.e.
        // the one thing a Save press must never do. In that case fall through
        // to the real manual save below, which collects the grid again and
        // posts the newest text.
        if (v.ok && untouchedSincePost) { window.location = caseUrl(); return; }
        // Either the automatic save failed, or the user typed while it was in
        // the air. Run their manual Save: it reports its own outcome, so do not
        // also toast over it — but the timer must not be left dead either:
        // without this re-arm, one failure that happened to coincide with a
        // click on Save silently ended automatic saving for the rest of the
        // session.
        scheduleAutosave(AUTOSAVE_MS);
        save();
        return;
      }
      if (v.ok) scheduleAutosave(AUTOSAVE_MS);
      else autosaveFailed(v);
    });
  }

  function autosaveTick() {
    autoTimer = null;
    if (!autosaveEnabled()) return;
    if (document.hidden) return;              // re-armed by visibilitychange
    // The tool has been EJECTED: tool_guard.js's status poll found that this
    // seat may no longer edit this case (it moved on, or the side was
    // cancelled), and it has thrown its blocking overlay over the page and
    // frozen every field. The save endpoint would refuse a POST from here
    // anyway — _may_build_form is the same check on both sides — so firing
    // would buy nothing and cost something: a red "Auto-save was refused"
    // strip appearing UNDER the overlay, telling a user who is already being
    // shown the real explanation that something else went wrong too. One
    // selector lookup, once per five minutes, on a node the guard appends to
    // <body>; there is nothing to observe and nothing to poll.
    if (document.querySelector(".tg-eject-overlay")) {
      stopped = true;
      return;
    }
    if (inFlight) { scheduleAutosave(SETTLE_MS); return; }
    if (!dirty) { scheduleAutosave(AUTOSAVE_MS); return; }   // no change -> no POST
    if (composing && imeDeferrals < MAX_IME_DEFERRALS) {
      // Mid-word in the Persian IME: what is in the box right now is a
      // provisional composition, not what the user means to store. Wait for it
      // — briefly, and never for ever. Focus is never touched, here or
      // anywhere else in this file, so typing is not interrupted either way.
      imeDeferrals++;
      scheduleAutosave(1000);
      return;
    }
    imeDeferrals = 0;
    runAutosave();
  }

  function startAutosave() {
    if (!autosaveEnabled()) return;
    if (document.documentElement.dataset.ftAutosaveBound) return;
    document.documentElement.dataset.ftAutosaveBound = "1";
    document.addEventListener("compositionstart", function () { composing = true; }, true);
    document.addEventListener("compositionend", function () { composing = false; }, true);
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) {
        if (autoTimer) { clearTimeout(autoTimer); autoTimer = null; }
        return;
      }
      if (autoTimer || !autosaveEnabled()) return;
      scheduleAutosave(Math.max(SETTLE_MS, nextDueAt - Date.now()));
    });
    window.addEventListener("beforeunload", function () {
      if (autoTimer) { clearTimeout(autoTimer); autoTimer = null; }
    });
    scheduleAutosave(AUTOSAVE_MS);
  }

  // A small, deliberate handle for support and for tests. It grants nothing a
  // user could not already do by pressing Save — runNow() goes through the very
  // same guards (edit mode, dirty, one in flight) — and it makes "did autosave
  // run, and what did it say" answerable on a live machine.
  window.FTToolAutosave = {
    intervalMs: AUTOSAVE_MS,
    isDirty: function () { return dirty; },
    isRunning: function () { return !!autoTimer; },
    dueInMs: function () { return autoTimer ? (nextDueAt - Date.now()) : null; },
    // What the last automatic save concluded, and whether the queue really was
    // empty when it was sent — "did autosave run, and what did it say" is the
    // question support actually asks, and now it has an answer that is about
    // one specific save. Reads state; changes none.
    lastVerdict: function () { return lastVerdict; },
    queueWasDrained: function () { return queueDrained; },
    runNow: function () { autosaveTick(); }
  };

  function bind() {
    var btn = document.getElementById("tool-save-btn");
    if (btn && !btn.dataset.bound) {
      btn.dataset.bound = "1";
      // The real label, read before any save can replace it with "Saving…".
      saveBtnLabel = btn.textContent;
      btn.addEventListener("click", save);
    }
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
      // Row flags (PI NOT SUPPLIABLE, TO Technical Problem apply/clear) are
      // real changes — collect() already serialises them into the row
      // signature (_unsuppliable / _issue / _issue_reason above) — but none
      // of their controls are a plain input/change, or one of the click
      // classes above: item_flag.js's .ic-box (the live control for BOTH the
      // PI Not-Suppliable ban icon and the TO Technical-Problem wrench) and
      // the #tp-reason-confirm / wrench-clear TO path live outside that list.
      // item_flag.js dispatches ft-flags-changed itself on every apply/clear
      // of either flag (applyIssue / clearIssue / the PI toggle in
      // onTableClick — verified: it is the ONLY dispatcher of this event in
      // the itemcoder JS), so listening for that one event, scoped to actual
      // flag changes, catches every case without hand-listing controls.
      //
      // ft-calc-refreshed is deliberately NOT wired here. It sounds like the
      // same kind of signal but is not: it also fires for perfectly ordinary,
      // non-flag calc-engine settles — an unrelated price recompute, and
      // notably calculation_controls.js's bootstrap(), which fires an async
      // fetch to sync the managed FX rate and then dispatches
      // ft-calc-refreshed once its batched repaint finishes, on EVERY PI
      // load, whether or not any flag was touched. On a large case that
      // settle can land a second or more after page load — after
      // captureInitialSignature's own baseline captures below — so treating
      // it as "the user changed something" was misreading an ordinary async
      // settle as a live edit and left Save permanently enabled from load,
      // with zero user action, for the rest of the session. (Confirmed live:
      // a 377-row PI page load with a slow FX endpoint stayed stuck enabled
      // from ~300ms through 5s+ with the old wiring, and stayed correctly
      // disabled the whole time once this listener was removed.)
      document.addEventListener("ft-flags-changed", scheduleDirtyCheck);
      // Snapshot after the grid / pricing / split UI finish initialising.
      setTimeout(captureInitialSignature, 400);
      setTimeout(captureInitialSignature, 1200);
      // Arm the automatic save only after the baseline above exists, so the
      // very first tick compares against a real signature.
      setTimeout(startAutosave, 1300);
    }
  }
  bind();
  document.addEventListener("DOMContentLoaded", bind);
})();
