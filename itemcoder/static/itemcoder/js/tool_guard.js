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

  var POLL_MS = 6000;   // near-immediate without hammering the server
  var stopped = false;

  function ejected(reason, redirect) {
    if (stopped) return;
    stopped = true;

    var go = function () { window.location.href = redirect || "/"; };

    var ov = document.createElement("div");
    ov.setAttribute("role", "alertdialog");
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
