/* ===========================================================================
   Company directory — the ONE behaviour this section adds of its own:
   switching the tab strip on a company detail page.

   Everything else on those two screens is already behaviour this platform
   has: the two searchable filter combos and the instant row-hiding pass on
   the directory list are static/js/ui.js reading `data-combo` /
   `data-filter-for` (this file does not touch either), the rows open a page
   with a plain onclick the way the case archive's rows do, and the contact
   form is a plain server-rendered Django form whose one cross-field rule is
   enforced in marketing/forms.py::ContactForm.clean() — deliberately with no
   JavaScript counterpart here, so the rule cannot be skipped by turning
   scripting off.

   Why a file at all, rather than the inline script the case detail page uses
   for its own strip: that script also drives the internal/external side
   switcher and the scroll-preserving swap those tall panels need, neither of
   which exists here. This is the small half of it, kept out of the template
   so the markup stays markup.

   Self-guarding, like marketing/js/chart_interact.js: if the strip is not on
   the page (the directory list, or any other page that loads this file), it
   does nothing at all.
   =========================================================================== */
(function () {
  "use strict";

  var strip = document.getElementById("companyTabs");
  if (!strip) return;

  var tabs = strip.querySelectorAll(".tab");
  if (!tabs.length) return;

  function activate(tab) {
    var name = tab.getAttribute("data-tab");
    if (!name) return;
    Array.prototype.forEach.call(tabs, function (t) { t.classList.remove("active"); });
    // Panels are addressed by id (panel-<name>), the same convention
    // cases/_side_section.html uses, so a panel that is not on the page
    // simply is not found and nothing throws.
    document.querySelectorAll(".tab-panel").forEach(function (p) { p.classList.remove("active"); });
    tab.classList.add("active");
    var panel = document.getElementById("panel-" + name);
    if (panel) panel.classList.add("active");
  }

  Array.prototype.forEach.call(tabs, function (tab) {
    tab.addEventListener("click", function () { activate(tab); });
    // The strip is made of <div>s (the platform's own .tabs/.tab markup, not
    // buttons), so keyboard users get the same two keys a button would give
    // them for free. role/tabindex are set here rather than in the template
    // for the same reason the click handler is: they are only true once this
    // script has made the strip interactive.
    tab.setAttribute("role", "button");
    tab.setAttribute("tabindex", "0");
    tab.addEventListener("keydown", function (e) {
      if (e.key !== "Enter" && e.key !== " ") return;
      e.preventDefault();
      activate(tab);
    });
  });
})();
