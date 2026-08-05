/* ===========================================================================
   Case-creation inquiry grid.
   Lets the user paste rows straight from Excel (tab/newline separated) or add
   rows by hand, then serialises everything into the hidden ``pasted_table``
   field as JSON on submit.
   Columns: Description, Size, Qty, Unit.
   =========================================================================== */
(function () {
  "use strict";

  var body = document.getElementById("items-body");
  var form = document.getElementById("case-form");
  if (!body || !form) return;

  var IG = window.FTInquiryGrid;

  function renumber() {
    body.querySelectorAll("tr").forEach(function (tr, i) {
      // In NEW CASE both the client row (#) and the Item number renumber
      // together 1..N — deleting a row reflows both identically.
      var cr = tr.querySelector(".client-no");
      if (cr) cr.textContent = i + 1;
      var n = tr.querySelector(".row-no");
      if (n) n.textContent = i + 1;
    });
    body.querySelectorAll(".c-size").forEach(function (inp) {
      inp.setAttribute("maxlength", "20");
    });
  }

  function newRow(values) {
    values = values || {};
    var tr = document.createElement("tr");
    tr.innerHTML =
      '<td class="mono client-no"></td>' +
      '<td class="mono row-no"></td>' +
      '<td><input type="text" class="c-description"></td>' +
      '<td><input type="text" class="c-size" maxlength="20"></td>' +
      '<td><input type="text" class="c-quantity" inputmode="decimal"></td>' +
      '<td><input type="text" class="c-unit"></td>' +
      '<td><button type="button" class="btn btn-sm btn-danger del-row">×</button></td>';
    if (values.description != null) tr.querySelector(".c-description").value = values.description;
    if (values.size != null) {
      var sz = String(values.size);
      if (sz.length > 20) sz = sz.slice(0, 20);
      tr.querySelector(".c-size").value = sz;
    }
    if (values.unit != null) {
      var u = String(values.unit);
      if (IG) u = (IG.unitFilter || IG.lettersOnlyFilter)(u);
      tr.querySelector(".c-unit").value = u;
    }
    if (values.quantity != null) {
      var q = String(values.quantity);
      if (IG) q = IG.qtyFilter(q);
      tr.querySelector(".c-quantity").value = q;
    }
    body.appendChild(tr);
    return tr;
  }

  function ensureOneRow() {
    if (!body.children.length) newRow();
    renumber();
  }

  var banner = document.getElementById("client-error");
  if (IG) {
    IG.attachInputGuards(body);
    IG.attachPaste(body, {
      banner: banner,
      newRow: function () { var tr = newRow(); renumber(); return tr; },
      onRenumber: renumber
    });
  }

  body.addEventListener("click", function (e) {
    if (e.target.classList.contains("del-row")) {
      e.target.closest("tr").remove();
      ensureOneRow();
    }
  });

  var addBtn = document.getElementById("add-row");
  if (addBtn) addBtn.addEventListener("click", function () { newRow(); renumber(); saveDraft(); });
  var clearBtn = document.getElementById("clear-rows");
  if (clearBtn) clearBtn.addEventListener("click", function () { body.innerHTML = ""; ensureOneRow(); saveDraft(); });

  // ---- collect current grid rows -----------------------------------------
  function collectRows() {
    var rows = [];
    body.querySelectorAll("tr").forEach(function (tr) {
      var cr = tr.querySelector(".client-no");
      var row = {
        // The client row number (#) is captured at creation; it later persists
        // even when newer versions delete rows, so gaps reveal removals.
        client_row: cr ? (cr.textContent || "").trim() : "",
        description: (tr.querySelector(".c-description") || {}).value || "",
        size: (tr.querySelector(".c-size") || {}).value || "",
        quantity: (tr.querySelector(".c-quantity") || {}).value || "",
        unit: (tr.querySelector(".c-unit") || {}).value || ""
      };
      if (row.description || row.size || row.unit || row.quantity) rows.push(row);
    });
    return rows;
  }

  function fillRows(rows) {
    body.innerHTML = "";
    (rows || []).forEach(function (r) { newRow(r); });
    ensureOneRow();
    renumber();
  }

  // ---- draft persistence (survives navigating away & back) ---------------
  var DRAFT_KEY = "ft_newcase_draft";
  var fieldIds = ["id_kind", "id_offer_type", "id_client", "id_order_no", "id_deadline", "id_price_type", "id_client_commercial_expert", "id_client_technical_expert", "id_client_technical_phone"];
  function saveDraft() {
    try {
      var data = { rows: collectRows(), fields: {} };
      fieldIds.forEach(function (id) { var el = document.getElementById(id); if (el) data.fields[id] = el.value; });
      localStorage.setItem(DRAFT_KEY, JSON.stringify(data));
    } catch (e) { /* ignore */ }
  }
  function restoreDraft() {
    var hidden = document.getElementById("id_pasted_table");
    // 1) If the server re-rendered with a bound pasted_table (e.g. validation
    //    error), rebuild the grid from it so nothing is lost.
    if (hidden && hidden.value && hidden.value !== "[]") {
      try { fillRows(JSON.parse(hidden.value)); return; } catch (e) { /* fall through */ }
    }
    // 2) Otherwise restore the local draft (fields + rows).
    try {
      var data = JSON.parse(localStorage.getItem(DRAFT_KEY) || "null");
      if (data) {
        if (data.fields) fieldIds.forEach(function (id) {
          var el = document.getElementById(id);
          if (el && data.fields[id] != null && !el.value) el.value = data.fields[id];
        });
        if (data.rows && data.rows.length) { fillRows(data.rows); return; }
      }
    } catch (e) { /* ignore */ }
    ensureOneRow();
  }

  form.addEventListener("input", saveDraft);
  form.addEventListener("change", saveDraft);

  form.addEventListener("submit", function () {
    // Serialise the grid into the hidden field; validation runs separately
    // (see the inline validator in case_create.html, capture phase).
    var hidden = document.getElementById("id_pasted_table");
    if (hidden) { try { hidden.value = JSON.stringify(collectRows()); } catch (e) { hidden.value = "[]"; } }
    clearExcelOnSubmit();
    try { localStorage.removeItem(DRAFT_KEY); } catch (e) { /* ignore */ }
  });

  // ---- Excel upload -> preview rows into the grid ------------------------
  var fileInput = document.getElementById("id_excel_file");
  var uploadBtn = document.getElementById("upload-excel-btn");
  var uploadStatus = document.getElementById("upload-status");
  // Tracks whether the currently-selected Excel file has been previewed into
  // the grid. The grid (serialised into pasted_table) is the single source of
  // truth on submit, so a previewed file must NOT also be posted (that double
  // the rows). An un-previewed file blocks submit until it is loaded.
  var excelPreviewed = false;
  if (fileInput && uploadBtn) {
    fileInput.addEventListener("change", function () {
      uploadBtn.style.display = fileInput.files && fileInput.files.length ? "" : "none";
      excelPreviewed = false;
      if (uploadStatus) uploadStatus.textContent = "";
    });
    uploadBtn.addEventListener("click", function () {
      if (!fileInput.files || !fileInput.files.length) return;
      var fd = new FormData();
      fd.append("excel_file", fileInput.files[0]);
      var token = (document.querySelector("[name=csrfmiddlewaretoken]") || {}).value || "";
      if (uploadStatus) uploadStatus.textContent = "Reading…";
      fetch(window.FT_PREVIEW_URL, { method: "POST", headers: { "X-CSRFToken": token }, body: fd })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.ok) {
            // Client-side double-check (server already validated).
            if (IG) {
              var check = IG.validateRows(data.rows || []);
              if (!check.ok) {
                excelPreviewed = false;
                if (uploadStatus) uploadStatus.textContent = "Upload rejected.";
                IG.showBanner(banner, "Upload cancelled — " + check.message);
                window.scrollTo({ top: 0, behavior: "smooth" });
                return;
              }
            }
            fillRows(data.rows);
            excelPreviewed = true;
            saveDraft();
            if (IG) { IG.hideBanner(banner); IG.clearPaint(body); }
            if (uploadStatus) uploadStatus.textContent = data.rows.length + " rows loaded.";
          } else {
            excelPreviewed = false;
            if (uploadStatus) uploadStatus.textContent = data.error || "Could not read file.";
            if (IG && data.error) {
              IG.showBanner(banner, "Upload cancelled — " + data.error);
              window.scrollTo({ top: 0, behavior: "smooth" });
            }
          }
        })
        .catch(function () { if (uploadStatus) uploadStatus.textContent = "Upload failed."; });
    });
  }

  // Expose preview state to the inline validator so it can block an
  // un-previewed Excel from being submitted.
  window.FT_excelNeedsPreview = function () {
    return !!(fileInput && fileInput.files && fileInput.files.length && !excelPreviewed);
  };
  window.FT_collectInquiryRows = collectRows;
  window.FT_validateInquiryGrid = function () {
    if (!IG) return { ok: true, errors: [], message: "" };
    var result = IG.validateRows(collectRows());
    if (!result.ok) IG.paintErrors(body, result.errors);
    else IG.clearPaint(body);
    return result;
  };
  // On submit, the grid already carries the rows (in pasted_table); never also
  // post the raw Excel file, or the server would read both and double the rows.
  function clearExcelOnSubmit() {
    if (fileInput) { try { fileInput.value = ""; } catch (e) { /* ignore */ } }
  }

  // Group phone numbers as they are typed: 09035847574 -> 0903 584 7574
  function formatPhone(digits) {
    digits = (digits || "").replace(/\D/g, "").slice(0, 15);
    if (digits.length === 11 && digits[0] === "0") {
      return (digits.slice(0, 4) + " " + digits.slice(4, 7) + " " + digits.slice(7)).trim();
    }
    var out = [];
    for (var i = 0; i < digits.length; i += 4) out.push(digits.slice(i, i + 4));
    return out.join(" ");
  }
  document.querySelectorAll('input[data-phone]').forEach(function (inp) {
    inp.addEventListener('input', function () {
      var pos = inp.value.length;
      inp.value = formatPhone(inp.value);
      if (pos >= inp.value.length) { /* caret stays at end naturally */ }
    });
    if (inp.value) inp.value = formatPhone(inp.value);
  });

  restoreDraft();
})();
