/* ===========================================================================
   "Add a task" / "Set your next reminder" (marketing/templates/marketing/
   task_form.html) — the live "also due that day" preview.

   THE OWNER'S OWN ASK THIS ROUND: the moment a person picks (and confirms)
   a date on the due-at field, before the whole form is even submitted, they
   should see what else of THEIR OWN is already due that same calendar day —
   so a reminder is never set blind on top of ones already sitting there.

   TRIGGERED ON THE DUE-AT FIELD'S OWN `change` EVENT, never on every
   keystroke — the real, underlying <input data-jalali-datetime> is not
   typed into directly (static/js/jalali_picker.js swaps a read-only display
   box in front of it and drives the real one), and that picker's own
   `commit()` (fired by its "Done" button) is what dispatches `change` on
   it, with `bubbles: true`. So this listens on the SAME input the label's
   own `for=` already points at (read here via `data-field-id`, not a
   hard-coded id — see the template's own comment on that panel), and fires
   exactly when a date is confirmed, never mid-typing.

   ONE SMALL FETCH, GET, NO CSRF NEEDED — Django's CSRF protection only
   guards unsafe methods (POST/PUT/DELETE); this is a read, so no header or
   token dance is needed the way marketing/static/marketing/js/
   chart_interact.js's own POST helper carries one.

   RENDERS A COMPACT LIST, NOT A TABLE — the owner's own wording, "just
   enough to see what's already there": time, company (if any), case
   document number (if any, and only if this viewer's own access still
   covers it — see marketing/views.py::my_tasks_day_reminders's own
   docstring on why that redaction happens server-side, not here), and the
   note. Built with plain DOM calls (textContent, never innerHTML) so a
   reminder note containing "<"/"&" can never be read back as markup.

   Self-guarding, like marketing/js/my_tasks.js and marketing/js/
   directory.js: if the due-at field or its panel is not on the page (a
   template that never included this file, or a future page reusing it that
   dropped the panel), every lookup below finds nothing and this file does
   nothing.
   =========================================================================== */
(function () {
  "use strict";

  var panel = document.getElementById("dueDayPreview");
  if (!panel) return;

  var fieldId = panel.getAttribute("data-field-id") || "";
  var input = fieldId ? document.getElementById(fieldId) : null;
  if (!input) return;

  var url = panel.getAttribute("data-url") || "";
  var headingText = panel.getAttribute("data-heading-text") || "";
  var emptyText = panel.getAttribute("data-empty-text") || "";
  var headEl = document.getElementById("dueDayPreviewHead");
  var listEl = document.getElementById("dueDayPreviewList");
  if (!url || !headEl || !listEl) return;

  // One in-flight request at a time — a fast re-pick (open the picker again,
  // change the date a second time before the first fetch has returned) must
  // not let an OLDER response overwrite a NEWER one's rows. `token` is
  // bumped on every call and a response is only rendered if it is still the
  // most recent one requested — the same "ignore a stale answer" discipline
  // marketing/static/marketing/js/chart_interact.js's own rapid-click
  // handling already documents for the identical reason.
  var token = 0;

  function clearPanel() {
    listEl.innerHTML = "";
    panel.hidden = true;
  }

  function renderRow(r) {
    var row = document.createElement("div");
    row.className = "hb-row";

    var time = document.createElement("span");
    time.className = "mono";
    time.textContent = r.time || "";
    row.appendChild(time);

    if (r.company) {
      var companyChip = document.createElement("span");
      companyChip.className = "chip";
      companyChip.setAttribute("dir", "auto");
      var bIcon = document.createElement("i");
      bIcon.className = "fa-solid fa-building";
      companyChip.appendChild(bIcon);
      companyChip.appendChild(document.createTextNode(" " + r.company));
      row.appendChild(companyChip);
    }

    if (r.case_doc_no) {
      var caseChip = document.createElement("span");
      caseChip.className = "chip";
      var cIcon = document.createElement("i");
      cIcon.className = "fa-solid fa-folder-open";
      caseChip.appendChild(cIcon);
      var caseText = document.createElement("span");
      caseText.className = "mono";
      caseText.textContent = " " + r.case_doc_no;
      caseChip.appendChild(caseText);
      row.appendChild(caseChip);
    }

    if (r.note) {
      var note = document.createElement("span");
      note.className = "hb-note";
      note.setAttribute("dir", "auto");
      note.textContent = r.note;
      row.appendChild(note);
    }

    return row;
  }

  function setHeadText(text) {
    // Rebuild the head text node after the leading <i> icon, leaving the
    // icon itself untouched — see the template's own markup for
    // #dueDayPreviewHead, which carries the icon alone so this file never
    // has to re-create it. `childNodes.length > 1` (not `> 0`) is the part
    // that matters: on the VERY FIRST call the icon is the head's ONLY
    // child, and trimming down to "nothing left" would delete that icon
    // right along with it — this stops one strictly before it, at "the icon
    // alone", every time, on the first call and on every one after it.
    while (headEl.childNodes.length > 1) headEl.removeChild(headEl.lastChild);
    headEl.appendChild(document.createTextNode(" " + text));
  }

  function render(rows) {
    listEl.innerHTML = "";
    if (!rows || !rows.length) {
      setHeadText(emptyText);
      panel.hidden = false;
      return;
    }
    setHeadText(headingText);
    rows.forEach(function (r) { listEl.appendChild(renderRow(r)); });
    panel.hidden = false;
  }

  input.addEventListener("change", function () {
    var raw = input.value || "";
    if (!raw.trim()) { clearPanel(); return; }
    var myToken = ++token;
    fetch(url + "?due_at=" + encodeURIComponent(raw))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (myToken !== token) return; // a newer pick has already superseded this
        if (!data || !data.ok) { clearPanel(); return; }
        render(data.reminders || []);
      })
      .catch(function () {
        if (myToken === token) clearPanel();
      });
  });
})();
