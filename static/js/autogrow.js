/* Auto-growing comment boxes — shared by every page in the platform.

   Lives in its own file rather than inside ui.js because the two pages that
   need it most do not load ui.js at all: itemcoder/tool_case.html and
   itemcoder/table.html are standalone documents that do not extend base.html.
   Pulling all of ui.js onto them would drag in the case filters, the inline
   confirm panels and the version chips, none of which those pages have any use
   for. One implementation, loaded wherever it is wanted, is the alternative to
   two copies that drift.

   Exposes window.FTAutoGrow(textarea) — see the note at the end of the file.
*/
/* ============================================================ comment boxes
   A textarea in this application is never a "value" field: it is the box where
   somebody writes the reason a case was burned, the note that goes on the audit
   trail, the comment attached to a row. Those were authored at rows="2" or
   rows="3", so anything past a sentence disappeared behind an inner scrollbar
   and the writer could see only the line they were on. Reported by the owner:
   the box must GROW with the text, and must not start that small in the first
   place.

   Both halves are done here, once, for every textarea on the page rather than
   per template — including the comment box in the inline confirm panel above,
   which does not exist in any template at all (it is built by JavaScript when
   the burn / cancel / send / return button is first drawn).

   HOW BIG IS "AS BIG AS THERE IS ROOM FOR". Growth has to stop somewhere: a box
   that keeps growing pushes its own Confirm button off the bottom of the screen
   and the panel becomes unusable, which is worse than the scrollbar. The limit
   is therefore taken from the geometry of the panel the box actually sits in,
   not from a fixed number of rows or a share of the viewport. Two measurements
   of that panel, both in pixels off the live layout:

       HERE      = window height
                   - the box's own distance from the top of the window
                   - everything the panel draws BELOW the box (its Confirm /
                     Post comment / Submit row, hints, errors)
                   - one text row, so the panel never sits flush on the edge

       SCREENFUL = the same sum with the panel scrolled to the top of the window
                   instead, i.e. counting what the panel draws ABOVE the box
                   rather than where the page happens to be scrolled right now.
                   The panel is then exactly one screen tall.

   HERE is the better limit and is the one normally used: the box grows until the
   panel's last control would touch the bottom edge, and every button stays where
   the writer can click it without scrolling the page at all. On a 1366x768
   laptop the burn dialog gets 438px that way — 24 rows — with Confirm landing at
   y=750.

   But HERE is only worth having while it leaves the box room to grow. A box far
   enough down the page has almost none: the Timeline "Add a comment" box was
   pinned at the height it opened at, and the overtime note could grow by a row
   and a half, both of them straight back to the inner scrollbar this module
   exists to remove. So HERE is kept only while it is at least twice the height
   the box opens at (below, that is half of HERE, so this is nearly always true);
   otherwise the box falls back to SCREENFUL, and the page scrolls to follow the
   caret as the writer types, exactly as it always has. Nothing is ever out of
   reach that way either: at SCREENFUL the whole panel, Confirm included, fits on
   one screen. Timeline 702px, overtime 507px, row comments 613px.

   The STARTING height is half of HERE, rounded down to whole rows and never
   under six — the box opens comfortably large, still has as much room again to
   grow into, and opening a panel never by itself pushes that panel's own button
   off the screen. A box revealed below the fold (a tab the user has not scrolled
   to) has no useful position to measure and takes half of SCREENFUL instead.

   BOTH limits are settled once, when the box is first shown, and then kept. They
   must not follow the box around: typing scrolls the page to keep the caret in
   view, which moves the box, so an allowance re-measured on every keystroke
   shrinks exactly as the box grows and pins the box at its opening height. Only
   a window resize invalidates them.

   NOT changed by any of this: what is submitted. No name, no value, no required
   flag, no validation is touched — only the pixel height of the box. dir="auto"
   / `unicode-bidi: plaintext` (see the end of static/css/app.css) is likewise
   untouched, so a Persian comment keeps reading in the order it was typed at
   every size.
   =========================================================================== */
(function () {
  "use strict";

  // The export terms editor (cases/templates/cases/export/terms_editor.html)
  // is left alone on purpose: its boxes are sized to the printed document they
  // feed, and their height is part of that fixed geometry.
  var SKIP = ".terms-editor";

  function num(value) {
    var n = parseFloat(value);
    return isNaN(n) ? 0 : n;
  }

  // Height of ONE row of text for a given font. `line-height` on these boxes is
  // "normal", which getComputedStyle reports verbatim rather than in pixels, so
  // it is measured off an off-screen probe instead of guessed at. One probe per
  // distinct font, cached.
  var rowCache = {};
  function rowHeight(cs) {
    var key = cs.fontFamily + "|" + cs.fontSize + "|" + cs.fontWeight + "|" + cs.lineHeight;
    if (rowCache[key]) return rowCache[key];
    var probe = document.createElement("textarea");
    probe.rows = 1;
    probe.style.cssText = "position:absolute;left:-9999px;top:0;width:20rem;height:auto;" +
      "min-height:0;padding:0;border:0;overflow:hidden;resize:none;";
    probe.style.fontFamily = cs.fontFamily;
    probe.style.fontSize = cs.fontSize;
    probe.style.fontWeight = cs.fontWeight;
    probe.style.lineHeight = cs.lineHeight;
    document.body.appendChild(probe);
    var one = probe.scrollHeight;
    probe.value = "x\nx";
    var line = probe.scrollHeight - one;
    probe.remove();
    if (line <= 0) line = one > 0 ? one : Math.round(num(cs.fontSize) * 1.4);
    rowCache[key] = line;
    return line;
  }

  // The panel whose last control has to stay on screen while the box grows.
  // closest() returns the NEAREST matching ancestor, so the confirm panel wins
  // over the form it lives in, and a plain form is the fallback.
  function panelOf(ta) {
    return ta.closest(".confirm-panel,.inq-comment-left,.tl-side-comment,form") || ta.parentNode;
  }

  // Both limits are settled once, from where the box is when it is first shown.
  function limitsFor(ta, rect) {
    var cs = getComputedStyle(ta);
    var line = rowHeight(cs);
    // Everything but the text itself. Boxes are border-box (app.css `*`), and
    // scrollHeight already includes the padding but never the border.
    var pad = num(cs.paddingTop) + num(cs.paddingBottom);
    var border = num(cs.borderTopWidth) + num(cs.borderBottomWidth);
    function rows(n) { return n * line + pad + border; }

    var panel = panelOf(ta);
    var prect = panel.getBoundingClientRect();
    // Both are independent of the box's current height: the box's edges and the
    // panel's move together.
    var head = Math.max(rect.top - prect.top, 0);   // panel content above the box
    var tail = Math.max(prect.bottom - rect.bottom, 0);   // ... and below it
    var floor = rows(6);

    // Two candidate allowances, both taken from the panel's own geometry.
    var top = Math.max(rect.top, 0);
    // HERE: how far the box can grow without the page having to scroll at all —
    // the panel's last control lands exactly on the bottom edge of the window.
    var here = window.innerHeight - top - line - tail;
    // A SCREENFUL: how far it can grow if the panel is scrolled to the top of the
    // window first, which makes the panel exactly one screen tall. `head` is what
    // the panel draws above the box, so it is where the box's top edge would land;
    // if the panel is already scrolled past the top of the window, the box's own
    // top is the truthful, smaller number, because the room above it is gone.
    var screenful = window.innerHeight - Math.min(head, top) - line - tail;
    if (screenful < floor) screenful = window.innerHeight - Math.min(head, top) - line;
    if (screenful < floor) screenful = floor;

    // The STARTING height is half of what the box can have where it is, rounded
    // down to whole rows and never under six: it opens comfortably large and still
    // has as much room again to grow into, and opening a panel never by itself
    // pushes that panel's own Confirm button off the screen. A box revealed below
    // the fold (a tab the user has not scrolled to yet) has no useful position to
    // measure, and takes half of the screenful instead.
    var start = Math.max(floor, rows(Math.floor(((here >= floor ? here : screenful) / 2 - pad - border) / line)));

    // The MAXIMUM keeps the no-scrolling-at-all allowance for as long as that still
    // lets the box grow to twice the height it opened at — which is what keeps the
    // Confirm button of every panel in the upper half of a page visible, at every
    // size, with no page scroll (the burn dialog: 438px, 24 rows, Confirm at y=750
    // on a 1366x768 laptop). When it does not — a box revealed below the fold, or
    // one sitting so low that its opening height was set by the six-row floor — that
    // allowance would leave the box unable to grow at all, which is the scrollbar
    // this module exists to remove; those fall back to the screenful, and the page
    // scrolls to follow the caret as the writer types, exactly as it always has.
    var max = here >= 2 * start ? here : screenful;
    max = Math.max(max, start);
    return { max: Math.round(max), start: Math.round(start), border: border };
  }

  // Size `ta` to its content. Returns false while the box is still hidden (a
  // confirm panel that has not been opened yet measures as zero), so the caller
  // knows to try again when it is revealed.
  function grow(ta) {
    if (!ta.offsetParent && !ta.offsetHeight) return false;
    // The allowance is settled the first time the box is shown and then kept. It
    // must NOT follow the box around: typing scrolls the page to keep the caret in
    // view, which moves the box, so a re-measured allowance shrinks as the box grows
    // and the box ends up pinned at the height it opened at — with the scrollbar.
    // Only a window resize invalidates it (see the resize listener below).
    var lim = ta._ftGrowLimits;
    if (!lim) lim = ta._ftGrowLimits = limitsFor(ta, ta.getBoundingClientRect());
    // Collapsing to `auto` is the only way to let the box shrink again when text
    // is deleted. It can clamp the window's scroll offset if the document ends
    // up shorter for that instant, so the offset is put back; no paint happens
    // in between, which makes the round trip invisible.
    var scrolled = window.pageYOffset;
    // Measure with the scrollbar gone. A box that is currently scrolling has a
    // different text width from the same box once it has grown, so measuring it
    // as it stands reports the wrong number of wrapped rows — that is how a box
    // that had just been capped stayed capped for the next, shorter comment.
    ta.style.overflowY = "hidden";
    ta.style.height = "auto";
    var needed = ta.scrollHeight + lim.border;
    ta.style.height = Math.min(Math.max(needed, lim.start), lim.max) + "px";
    if (needed > lim.max) ta.style.overflowY = "auto";
    if (window.pageYOffset !== scrolled) window.scrollTo(0, scrolled);
    return true;
  }

  function eligible(ta) {
    return ta && ta.tagName === "TEXTAREA" && !ta.closest(SKIP);
  }

  // Give a starting height to every box that has not been given one yet: an
  // inline height is the record that a box has been sized. That is what picks up
  // the boxes revealed after load — the confirm panel, the Timeline tab, the
  // inquiry editor's comment card — the first time they are actually on screen.
  function sweep() {
    document.querySelectorAll("textarea").forEach(function (ta) {
      if (!eligible(ta) || ta.style.height) return;
      grow(ta);
    });
  }

  document.addEventListener("input", function (e) {
    if (eligible(e.target)) grow(e.target);
  }, true);
  // Panels are revealed by a click (and tabs by a click); this listener is on
  // document, so it runs after the handler that did the revealing.
  document.addEventListener("click", sweep);
  document.addEventListener("focusin", function (e) {
    if (eligible(e.target)) grow(e.target);
  });

  var pending = 0;
  window.addEventListener("resize", function () {
    // The allowance is a function of the window, so every sized box is measured
    // against the new one — once per frame, not once per resize event.
    if (pending) return;
    pending = requestAnimationFrame(function () {
      pending = 0;
      document.querySelectorAll("textarea").forEach(function (ta) {
        if (!eligible(ta) || !ta.style.height) return;
        ta._ftGrowLimits = null;
        grow(ta);
      });
    });
  });

  sweep();
  // Exposed for code that changes a box's CONTENT without the user typing, which
  // fires no `input` event and would otherwise leave the box at the height the old
  // text needed: the timeline comment box, emptied after its AJAX post succeeds
  // (cases/templates/cases/case_detail.html), and the inquiry editor's row-comment
  // box, emptied when a comment is confirmed (cases/templates/cases/edit_items.html).
  window.FTAutoGrow = function (ta) { return eligible(ta) ? grow(ta) : false; };
})();
