/* Tool guard: throw the user out the instant their case/side is closed.
 *
 * On a split (Internal & External) case, Final-Approving one side CANCELS the
 * other side immediately. Someone building the TO (Technical) or PI (Supply)
 * on that just-cancelled side must be ejected from the tool right away — not
 * only blocked when they eventually press Save. We poll a tiny status endpoint
 * and, the moment the side/case turns terminal, block the screen and redirect.
 */
(function () {
  "use strict";

  var cfg = window.FT_TOOL_SAVE || {};
  var url = cfg.statusUrl;
  if (!url) return;

  // READ-ONLY (View) is exempt from the kickout, and that is not a softening
  // of it — it is the difference between the two things the endpoint answers.
  // tool_for_case_status reports active:false for EVERY terminal status, so a
  // poll left running here throws a viewer straight back out of a
  // FINAL_CLOSED / BURNED / CANCELLED case — precisely the cases the Archive's
  // View control opens, i.e. the whole reason the viewer exists. The kickout
  // is there to stop somebody EDITING a case that has moved on; a viewer
  // cannot edit (every field is locked below, and the save endpoint refuses
  // this seat regardless — see _may_build_form in bridge.py), so there is
  // nothing here for it to prevent.
  //
  // The flag is the SERVER's read_only — the exact negation of the
  // authorisation that guards the save — rendered into FT_TOOL_SAVE and onto
  // <body>. It is not a URL parameter and not something the visitor can set,
  // so a seat that can actually write always has readOnly === false and keeps
  // the poll, unchanged.
  if (cfg.readOnly === true
      || (document.body
          && document.body.getAttribute("data-read-only") === "1")) return;

  var POLL_MS = 6000;   // near-immediate without hammering the server
  var stopped = false;

  function ejected(reason, redirect) {
    if (stopped) return;
    stopped = true;

    var go = function () { window.location.href = redirect || "/"; };

    var ov = document.createElement("div");
    ov.setAttribute("role", "alertdialog");
    // Named so the read-only sweep in the second block can leave it alone: the
    // overlay is appended to <body>, outside every LOOK_ONLY container, so
    // without this its "Return to case" button — the ONE control it has — is
    // disabled by that sweep. The class is set before the node is appended, so
    // there is no window in which a sweep could see the button unparented.
    ov.className = "tg-eject-overlay";
    ov.style.cssText =
      "position:fixed;inset:0;z-index:2147483647;background:rgba(15,23,42,.74);" +
      "display:flex;align-items:center;justify-content:center;padding:1.5rem;" +
      "backdrop-filter:saturate(120%) blur(2px);";

    var box = document.createElement("div");
    box.style.cssText =
      "max-width:32rem;width:100%;background:#fff;border-radius:16px;" +
      "padding:1.5rem 1.7rem;box-shadow:0 24px 70px rgba(0,0,0,.4);text-align:center;" +
      "font-family:inherit;";
    box.innerHTML =
      '<div style="font-size:2.4rem;line-height:1;margin-bottom:.55rem;">&#9940;</div>' +
      '<h2 style="margin:0 0 .5rem;font-size:1.18rem;color:#b91c1c;font-weight:800;">' +
      'This case is no longer available</h2>' +
      '<p class="tg-reason" style="margin:0 0 1.2rem;color:#334155;font-size:.94rem;line-height:1.55;"></p>' +
      '<button type="button" class="tg-ok" style="border:0;background:#0b42a8;color:#fff;' +
      'padding:.6rem 1.5rem;border-radius:9px;font-size:.92rem;font-weight:700;cursor:pointer;">' +
      'Return to case</button>';
    box.querySelector(".tg-reason").textContent =
      reason || "Editing has been closed for this case.";
    ov.appendChild(box);

    // Freeze the tool behind the overlay so nothing else can be typed/saved.
    document.documentElement.style.overflow = "hidden";
    (document.body || document.documentElement).appendChild(ov);
    box.querySelector(".tg-ok").addEventListener("click", go);

    // Never leave the user stuck on a dead case, even if they ignore the button.
    setTimeout(go, 6000);
  }

  function tick() {
    if (stopped) return;
    fetch(url, { headers: { "X-Requested-With": "XMLHttpRequest" }, cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (d && d.active === false) ejected(d.reason, d.redirect);
      })
      .catch(function () { /* transient network hiccup: keep polling */ });
  }

  var timer = setInterval(tick, POLL_MS);
  window.addEventListener("beforeunload", function () { clearInterval(timer); });
  // Check soon after load too, in case it was cancelled a moment ago.
  setTimeout(tick, 1500);
})();

/* Read-only (View) mode ----------------------------------------------------
 *
 * A unit that no longer holds the case may open its own TO/PI to look at it.
 * The rule that makes that safe lives on the SERVER: bridge._may_build_form
 * decides both that this page is read-only and that this seat's save is
 * refused, so nothing below is load-bearing for security — a crafted POST is
 * turned away whether or not this file runs. What this does is keep the page
 * honest, so a viewer never types a number that could not be stored.
 *
 * The rule is an ALLOW-LIST, not a list of things to switch off. The grid grows
 * new writers over time (brand bulk-apply, Qty x6, the assistant, per-row
 * margins, the service-price toggle) and a writer forgotten in a deny-list is a
 * cell that looks editable; a filter forgotten in an allow-list is only a
 * filter that stops working, which is visible and harmless. So: everything
 * interactive is locked except the controls that change WHAT IS SHOWN.
 */
(function () {
  "use strict";

  var body = document.body;
  if (!body || body.getAttribute("data-read-only") !== "1") return;

  // Controls that only change what is DISPLAYED — never a stored value.
  // Deliberately narrow. ".ft-switch" on its own was too wide: the per-row
  // margin editor and the service-price feature are switches too, and those
  // reveal writable cells. Only the switches that sit inside a ".to-metric"
  // card are row filters ("show only rows without a code / with a problem /
  // rounded by Qty x6"), so that is what the selector says.
  //
  // Everything the PI needs is listed explicitly for the same reason the TO's
  // entries are: the Proforma's toolbar mixes its filter with its writers in
  // one strip (#pi-pricing-bar also hosts the price-list combo, the bulk
  // Manual price / Brand / Delivery-time appliers and — moved into it at
  // build time — the whole calculation & margin card), so the STRIP must never
  // be exempted. Only the two parts of it that decide what is shown are.
  var LOOK_ONLY = [
    "#to-filter-bar",         // Group & feature filter (TO)
    ".to-metric .ft-switch",  // the "show only these rows" switches
    "#ft-pipe-tools-modal",   // NPS/DN, schedule, unit converters — lookups
    "[data-pipe-tool]",       // the tiles that open them
    // ── PI (Proforma) ──
    // The three chips only show/hide the panel each names; opening a panel
    // reveals nothing that is not already locked, so the "Calculation & margin"
    // chip is as safe as the other two — the card it opens stays dead.
    ".pi-chips",
    // The Group & feature filter panel, in full: the group combo, the per
    // feature combos, "Clear all", and the row-filter cards (deleted / added /
    // no unit price / no brand / no time / not suppliable / with remark /
    // similar codes). Every control inside it ends in
    // VirtualScrollEngine.addFilter or removeFilter — it changes which rows are
    // rendered and never touches a cell. Nothing that writes is built into this
    // container: the price list, Manual unit price, Brand and Delivery-time
    // appliers live in #pi-panel-pricing, and margins, the FX conversion and
    // the per-row margin editor live in #calculation-control-card. Both of
    // those stay locked.
    "#pi-panel-filter",
    ".lang-switch",
    "#lang-toggle-btn",       // document language toggle (display only)
    ".tool-bar",              // the bar itself (Back to case)
    // The kickout overlay from the first block in this file. It is appended to
    // <body>, so nothing else here would spare its only button.
    ".tg-eject-overlay"
  ].join(",");

  var FIELDS = "input,textarea,select,button,[contenteditable]";
  var locking = false;

  function lockOne(el) {
    if (!el || (el.closest && el.closest(LOOK_ONLY))) return;
    var tag = (el.tagName || "").toLowerCase();
    if (el.getAttribute && el.getAttribute("contenteditable") === "true") {
      el.setAttribute("contenteditable", "false");
    }
    // readOnly (not disabled) on text fields: a disabled field drops out of the
    // tab order and is skipped by some of the layout/measuring passes, which
    // would make the viewer's grid lay out differently from the owner's.
    // readOnly keeps the cell looking and measuring exactly as it does for the
    // unit that holds the case — "renders exactly as it does now".
    if (tag === "textarea"
        || (tag === "input" && !/^(checkbox|radio|button|submit|file)$/i
            .test(el.type || "text"))) {
      if (!el.readOnly) el.readOnly = true;
    } else if (tag === "input" || tag === "select" || tag === "button") {
      // These have no readOnly to set.
      if (!el.disabled) el.disabled = true;
    }
    if (el.setAttribute) el.setAttribute("aria-readonly", "true");
  }

  // Re-locks EVERY time, never "already done". pi_pricing.js legitimately
  // re-enables the UNIT PRICE / TIME inputs after its own pass (it is written
  // for the unit that owns the case), so a one-shot sweep left exactly those
  // two columns writable. Locking is idempotent and cheap — the virtual-scroll
  // engine only ever keeps a window of rows in the DOM.
  function lock() {
    if (locking) return;
    locking = true;
    try {
      var els = document.querySelectorAll(FIELDS);
      for (var i = 0; i < els.length; i++) lockOne(els[i]);
    } catch (e) {
      /* never let this break the page */
    }
    locking = false;
    // Discard the mutations this sweep just produced, or the observer would
    // schedule another sweep for our own writes, forever.
    if (obs) obs.takeRecords();
  }

  // A readOnly textarea still fires paste/drop handlers in some engines, and
  // several of the grid's own key handlers write the cell directly rather than
  // through the field's value, so swallow those events at capture.
  var GRID = "#excel-table-container";
  ["paste", "drop", "cut", "beforeinput"].forEach(function (evt) {
    document.addEventListener(evt, function (e) {
      var t = e.target;
      if (t && t.closest && t.closest(GRID)) {
        e.preventDefault();
        e.stopPropagation();
      }
    }, true);
  });

  // The last line of defence in the page: whatever is about to receive input is
  // locked at the moment it is reached, so even a field re-enabled a
  // millisecond ago cannot be typed into.
  ["focusin", "pointerdown"].forEach(function (evt) {
    document.addEventListener(evt, function (e) {
      lockOne(e.target);
    }, true);
  });

  var obs = null;
  var pending = null;
  obs = new MutationObserver(function () {
    if (locking || pending) return;
    pending = setTimeout(function () { pending = null; lock(); }, 40);
  });
  obs.observe(document.documentElement, {
    childList: true,
    subtree: true,
    // Attributes too: the un-locking above is an attribute change, not a new
    // node, so a childList-only observer never saw it.
    attributes: true,
    attributeFilter: ["readonly", "disabled", "contenteditable"]
  });

  lock();
  // Late mounts: pricing, service price and the assistant build their controls
  // after their own data loads, well after DOMContentLoaded.
  [0, 300, 900, 2000, 4000].forEach(function (ms) { setTimeout(lock, ms); });
})();
