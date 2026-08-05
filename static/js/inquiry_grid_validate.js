/* ===========================================================================
   Inquiry grid validation — Description / Size / Qty / Unit
   Used by New Case, Edit items, and New version editors.
   =========================================================================== */
(function (window) {
  "use strict";

  var QTY_RE = /^\d+(\.\d+)?$/;
  var UNIT_RE = /^[\p{L}.\/\-_ %]+$/u;

  function rowLabel(row, index) {
    var n = parseInt(String(row.client_row || row["#"] || "").trim(), 10);
    return isFinite(n) && n > 0 ? n : index;
  }

  function isDeleted(row) {
    return String(row.deleted || row._deleted || "") === "1";
  }

  function isValidUnit(v) {
    if (!v || !UNIT_RE.test(v)) return false;
    // At least one letter (Latin / Persian / …); digits are never allowed.
    return /[\p{L}]/u.test(v) && !/\d/.test(v);
  }

  function isQty(v) {
    return !!(v && QTY_RE.test(v));
  }

  /** Validate row objects. Returns { ok, errors:[{row,field,message}], message }. */
  function validateRows(rows) {
    var errors = [];
    (rows || []).forEach(function (row, i) {
      if (!row || isDeleted(row)) return;
      var desc = String(row.description || "").trim();
      var size = String(row.size || "").trim();
      var qty = String(row.quantity != null ? row.quantity : (row.qty || "")).trim();
      var unit = String(row.unit || "").trim();
      if (!(desc || size || qty || unit)) return;
      var label = rowLabel(row, i + 1);
      if (desc.length < 7) {
        errors.push({
          row: label, field: "description",
          message: "Row " + label + ": Description must be at least 7 characters (currently " + desc.length + ")."
        });
      }
      if (size.length > 20) {
        errors.push({
          row: label, field: "size",
          message: "Row " + label + ": Size must be at most 20 characters (currently " + size.length + ")."
        });
      }
      if (!isQty(qty)) {
        errors.push({
          row: label, field: "quantity",
          message: "Row " + label + ": Qty must contain numbers only" + (qty ? "." : " (cannot be empty).")
        });
      }
      if (!isValidUnit(unit)) {
        errors.push({
          row: label, field: "unit",
          message: "Row " + label + ": Unit must contain letters" +
            (unit ? " (digits not allowed; . / - _ % and spaces are OK)." : " (cannot be empty).")
        });
      }
    });
    return {
      ok: errors.length === 0,
      errors: errors,
      message: errors.length
        ? ("Inquiry items are invalid:\n" + errors.map(function (e) { return e.message; }).join("\n"))
        : ""
    };
  }

  function collectFromBody(body, opts) {
    opts = opts || {};
    var rows = [];
    if (!body) return rows;
    body.querySelectorAll("tr").forEach(function (tr) {
      var c = tr.querySelector(".client-no");
      var clientText = "";
      if (c) {
        clientText = (c.getAttribute("data-client") || "").trim();
        if (!clientText) {
          var t = c.querySelector(".client-no-text");
          clientText = ((t ? t.textContent : c.textContent) || "").replace(/[−+\s]/g, "").trim();
        }
      }
      var deleted = tr.getAttribute("data-deleted") === "1";
      var row = {
        client_row: clientText,
        description: (tr.querySelector(".c-description") || {}).value || "",
        size: (tr.querySelector(".c-size") || {}).value || "",
        quantity: (tr.querySelector(".c-quantity") || {}).value || "",
        unit: (tr.querySelector(".c-unit") || {}).value || "",
        deleted: deleted ? "1" : "0"
      };
      if (opts.skipEmpty && !(String(row.description).trim() || String(row.size).trim() ||
          String(row.quantity).trim() || String(row.unit).trim()) && !deleted) {
        return;
      }
      rows.push(row);
    });
    return rows;
  }

  function validateTableBody(body, opts) {
    return validateRows(collectFromBody(body, opts));
  }

  function clearPaint(body) {
    if (!body) return;
    body.querySelectorAll("input.is-inq-error").forEach(function (inp) {
      inp.classList.remove("is-inq-error");
      inp.style.borderColor = "";
      inp.style.boxShadow = "";
    });
  }

  function paintErrors(body, errors) {
    clearPaint(body);
    if (!body || !errors || !errors.length) return;
    var byRow = {};
    errors.forEach(function (e) {
      if (!byRow[e.row]) byRow[e.row] = {};
      byRow[e.row][e.field] = true;
    });
    var fieldClass = {
      description: "c-description",
      size: "c-size",
      quantity: "c-quantity",
      unit: "c-unit"
    };
    body.querySelectorAll("tr").forEach(function (tr, i) {
      var c = tr.querySelector(".client-no");
      var label = i + 1;
      if (c) {
        var raw = (c.getAttribute("data-client") || "").trim();
        if (!raw) {
          var t = c.querySelector(".client-no-text");
          raw = ((t ? t.textContent : c.textContent) || "").replace(/[−+\s]/g, "").trim();
        }
        var n = parseInt(raw, 10);
        if (isFinite(n) && n > 0) label = n;
      }
      var fields = byRow[label];
      if (!fields) return;
      Object.keys(fields).forEach(function (field) {
        var inp = tr.querySelector("." + fieldClass[field]);
        if (!inp) return;
        inp.classList.add("is-inq-error");
        inp.style.borderColor = "#d11";
        inp.style.boxShadow = "0 0 0 2px rgba(209,17,17,.20)";
      });
    });
  }

  function showBanner(el, message) {
    if (!el) return;
    var span = el.querySelector("span");
    var text = String(message || "").replace(/\n/g, " · ");
    if (span) span.textContent = text;
    else el.textContent = text;
    el.style.display = el.classList.contains("flash") ? "flex" : "block";
  }

  function hideBanner(el) {
    if (!el) return;
    el.style.display = "none";
  }

  function unitFilter(value) {
    return String(value || "").replace(/[^\p{L}.\/\-_ %]/gu, "");
  }

  function qtyFilter(value) {
    var s = String(value || "").replace(/[^\d.]/g, "");
    var parts = s.split(".");
    if (parts.length > 2) s = parts[0] + "." + parts.slice(1).join("");
    return s;
  }

  /** Live input guards: Size maxlength, Qty digits, Unit letters+punctuation. */
  function attachInputGuards(body) {
    if (!body || body._ftInqGuards) return;
    body._ftInqGuards = true;
    body.addEventListener("input", function (e) {
      var t = e.target;
      if (!t || !t.classList) return;
      if (t.classList.contains("c-size")) {
        if (t.value.length > 20) t.value = t.value.slice(0, 20);
      } else if (t.classList.contains("c-quantity")) {
        var q = qtyFilter(t.value);
        if (q !== t.value) t.value = q;
      } else if (t.classList.contains("c-unit")) {
        var u = unitFilter(t.value);
        if (u !== t.value) t.value = u;
      }
      if (t.classList.contains("is-inq-error")) {
        t.classList.remove("is-inq-error");
        t.style.borderColor = "";
        t.style.boxShadow = "";
      }
    });
    body.querySelectorAll(".c-size").forEach(function (inp) {
      inp.setAttribute("maxlength", "20");
    });
  }

  /**
   * Parse Excel/TSV clipboard text with RFC4180-style quotes so a Description
   * cell that contains newlines stays one field (not extra fake rows).
   */
  function parseSpreadsheetRows(text) {
    var src = String(text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    var rows = [];
    var row = [];
    var field = "";
    var i = 0;
    var inQuotes = false;
    while (i < src.length) {
      var ch = src.charAt(i);
      if (inQuotes) {
        if (ch === '"') {
          if (src.charAt(i + 1) === '"') {
            field += '"';
            i += 2;
            continue;
          }
          inQuotes = false;
          i++;
          continue;
        }
        field += ch;
        i++;
        continue;
      }
      if (ch === '"') {
        // Only open a quoted field at the START of a cell. Mid-field quotes
        // (Excel inch marks like 6") must stay literal — otherwise Qty/Unit
        // get swallowed into Size and validation fails.
        if (field.length === 0) {
          inQuotes = true;
          i++;
          continue;
        }
        field += ch;
        i++;
        continue;
      }
      if (ch === "\t") {
        row.push(field);
        field = "";
        i++;
        continue;
      }
      if (ch === "\n") {
        row.push(field);
        field = "";
        if (row.length > 1 || (row.length === 1 && String(row[0]).trim() !== "")) {
          rows.push(row);
        }
        row = [];
        i++;
        continue;
      }
      field += ch;
      i++;
    }
    if (field.length || row.length) {
      row.push(field);
      if (row.length > 1 || (row.length === 1 && String(row[0]).trim() !== "")) {
        rows.push(row);
      }
    }
    return rows;
  }

  /** Map a pasted cell array onto Description/Size/Qty/Unit (4 adjacent cols). */
  function pickInquiryCells(cells, startCol) {
    var raw = (cells || []).map(function (c) { return String(c == null ? "" : c).trim(); });
    if (startCol > 0) {
      // Partial paste into mid-row: keep positional mapping.
      return raw;
    }
    // Drop a leading client-row number column when present.
    if (raw.length && /^\d+$/.test(raw[0]) && raw.length >= 5) {
      raw = raw.slice(1);
    }
    if (raw.length === 4) return raw;
    if (raw.length > 4) {
      var best = null;
      for (var s = 0; s <= raw.length - 4; s++) {
        var w = raw.slice(s, s + 4);
        if (w[0].length >= 7 && w[1].length <= 20 && isQty(w[2]) && isValidUnit(w[3])) {
          best = w;
          break;
        }
      }
      return best || raw.slice(-4);
    }
    while (raw.length < 4) raw.push("");
    return raw;
  }

  /**
   * Excel-style paste with validation. opts:
   *   { onRenumber, banner, beforeApply(rows)->bool optional }
   * Returns true if paste was handled.
   */
  function attachPaste(body, opts) {
    opts = opts || {};
    if (!body || body._ftInqPaste) return;
    body._ftInqPaste = true;
    var COLS = ["c-description", "c-size", "c-quantity", "c-unit"];
    var SRC_FULLROW = ["c-description", "c-size", "c-quantity", "c-unit"];
    var KEYS = ["description", "size", "quantity", "unit"];

    body.addEventListener("paste", function (e) {
      var text = (e.clipboardData || window.clipboardData).getData("text");
      if (!text || (text.indexOf("\t") === -1 && text.indexOf("\n") === -1)) return;
      e.preventDefault();

      var startInput = e.target;
      var startCol = COLS.indexOf((startInput.className || "").split(/\s+/).filter(function (c) {
        return COLS.indexOf(c) >= 0;
      })[0] || "");
      if (startCol < 0) startCol = 0;
      var startTr = startInput.closest("tr");
      var startIndex = Array.prototype.indexOf.call(body.children, startTr);
      if (startIndex < 0) startIndex = body.children.length;

      var sheetRows = parseSpreadsheetRows(text);
      if (!sheetRows.length) return;

      // Build a preview of resulting row values for validation (only pasted rows).
      var preview = [];
      var mapped = [];
      sheetRows.forEach(function (cells, li) {
        var picked = pickInquiryCells(cells, startCol);
        mapped.push(picked);
        var rowIndex = startIndex + li;
        var tr = body.children[rowIndex];
        var base = {
          client_row: String(rowIndex + 1),
          description: "",
          size: "",
          quantity: "",
          unit: "",
          deleted: "0"
        };
        if (tr) {
          var c = tr.querySelector(".client-no");
          if (c) {
            base.client_row = (c.getAttribute("data-client") || "").trim() ||
              ((c.querySelector(".client-no-text") || {}).textContent || c.textContent || "")
                .replace(/[−+\s]/g, "").trim() || base.client_row;
          }
          base.description = (tr.querySelector(".c-description") || {}).value || "";
          base.size = (tr.querySelector(".c-size") || {}).value || "";
          base.quantity = (tr.querySelector(".c-quantity") || {}).value || "";
          base.unit = (tr.querySelector(".c-unit") || {}).value || "";
          if (tr.getAttribute("data-deleted") === "1") base.deleted = "1";
        }
        if (startCol <= 0) {
          picked.forEach(function (val, ci) {
            if (ci >= KEYS.length) return;
            base[KEYS[ci]] = val;
          });
        } else {
          picked.forEach(function (val, ci) {
            var colPos = startCol + ci;
            if (colPos >= KEYS.length) return;
            base[KEYS[colPos]] = val;
          });
        }
        preview.push(base);
      });

      var result = validateRows(preview);
      if (!result.ok) {
        if (opts.banner) showBanner(opts.banner, "Paste cancelled — " + result.message);
        paintErrors(body, result.errors);
        window.scrollTo({ top: 0, behavior: "smooth" });
        return;
      }
      hideBanner(opts.banner);

      mapped.forEach(function (picked, li) {
        var rowIndex = startIndex + li;
        var tr = body.children[rowIndex];
        if (!tr && typeof opts.newRow === "function") tr = opts.newRow();
        if (!tr) return;
        if (startCol <= 0) {
          picked.forEach(function (val, ci) {
            if (ci >= SRC_FULLROW.length) return;
            var input = tr.querySelector("." + SRC_FULLROW[ci]);
            if (!input) return;
            input.value = val;
          });
        } else {
          picked.forEach(function (val, ci) {
            var colPos = startCol + ci;
            if (colPos >= COLS.length) return;
            var input = tr.querySelector("." + COLS[colPos]);
            if (!input) return;
            input.value = val;
          });
        }
      });
      if (typeof opts.onRenumber === "function") opts.onRenumber();
      clearPaint(body);
    });
  }

  window.FTInquiryGrid = {
    validateRows: validateRows,
    validateTableBody: validateTableBody,
    collectFromBody: collectFromBody,
    paintErrors: paintErrors,
    clearPaint: clearPaint,
    showBanner: showBanner,
    hideBanner: hideBanner,
    attachInputGuards: attachInputGuards,
    attachPaste: attachPaste,
    qtyFilter: qtyFilter,
    unitFilter: unitFilter,
    lettersOnlyFilter: unitFilter
  };
})(window);
