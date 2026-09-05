/* ===========================================================================
   Contact phone rows — "+ Add phone" / per-row remove on the Add Contact page
   (marketing/templates/marketing/contact_form.html).

   PURELY PRESENTATION. A contact can now carry any number of phone numbers
   (see marketing/models.py::ContactPhone), and this script only adds and
   removes whole ROWS OF PLAIN INPUTS in the browser — it does not validate
   anything, and nothing it does is trusted server-side. Every row's three
   inputs share the SAME name across every row ("phone_prefix" / "phone" /
   "phone_ext"), which is what lets marketing/forms.py::ContactForm read them
   all back with QueryDict.getlist and zip the three same-named lists into
   rows again (see ContactForm._submitted_phone_rows). Removing a row here
   just removes its three inputs from the DOM, so they are simply absent from
   the POST — there is no hidden "deleted" flag to track, unlike the inquiry
   items grid's paste-from-Excel machinery (static/js/inquiry_grid_validate.js),
   which this page deliberately does not borrow: that grid solves pasting a
   whole spreadsheet in; this is a much smaller problem (copy one existing
   row, clear it, let the person type into it) and does not need it.
   =========================================================================== */
(function () {
  "use strict";

  function ready(fn) {
    if (document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn);
  }

  ready(function () {
    var container = document.getElementById("phone-rows");
    var addBtn = document.getElementById("add-phone-row");
    if (!container || !addBtn) return;

    function addRow() {
      // Clone the LAST existing row rather than carrying a separate blank
      // <template> — the row shape lives in exactly one place (the template
      // rendered by Django), so a future field added to the row is picked up
      // here automatically instead of needing a second, hand-kept copy in
      // this file. If every row was removed, fall back to cloning a bare
      // structure the same way a fresh page would have rendered one.
      var rows = container.querySelectorAll("[data-phone-row]");
      var source = rows.length ? rows[rows.length - 1] : null;
      var row = source ? source.cloneNode(true) : null;
      if (!row) return;
      // Clear every value the clone inherited — this is a NEW, empty row,
      // not a copy of whatever the person had already typed into the last
      // one.
      row.querySelectorAll("input").forEach(function (input) { input.value = ""; });
      container.appendChild(row);
    }

    function removeRow(row) {
      // Removing the row removes its three inputs from the DOM entirely, so
      // they are simply absent from the next submit — there is nothing else
      // to clean up. Never remove the LAST row outright: leaving one empty
      // row behind means a person who cleared everything still sees a place
      // to type a number, rather than a blank section with no way back in
      // short of a page reload. ContactForm.clean() already discards a
      // fully-blank row on submit, so this costs nothing server-side.
      var rows = container.querySelectorAll("[data-phone-row]");
      if (rows.length <= 1) {
        row.querySelectorAll("input").forEach(function (input) { input.value = ""; });
        return;
      }
      row.remove();
    }

    addBtn.addEventListener("click", addRow);

    // Event delegation on the container, not one listener per button: rows
    // added later by addRow() above need no separate wiring this way.
    container.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-remove-phone-row]");
      if (!btn) return;
      var row = btn.closest("[data-phone-row]");
      if (row) removeRow(row);
    });
  });
})();
