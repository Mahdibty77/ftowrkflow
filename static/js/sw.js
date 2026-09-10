/*
 * Service worker: exists ONLY to (a) make the site installable as a PWA and
 * (b) speed up repeat loads of STATIC assets (CSS/JS/icons) via a plain
 * cache-first strategy.
 *
 * Deliberately does NOT touch anything else. This is a live workflow app —
 * case status, inboxes, pricing — caching a page or an API response would
 * mean a Technical/Supply/Commercial seat could act on stale data without
 * knowing it. Only requests under STATIC_PREFIX are ever intercepted; every
 * other request (every page, every /ajax/, /tool/, /cases/ call) is left to
 * go straight to the network, untouched, exactly as if this file did not
 * exist.
 *
 * Bump CACHE_NAME (not the whole file) when static assets change shape in a
 * way that needs a clean slate; ordinary asset edits already bust their own
 * cache entry via the "?v=" query strings this app already appends to
 * <script>/<link> tags (see base.html) — a new "?v=" is a new cache key.
 */
(function () {
  "use strict";

  var CACHE_NAME = "ft-static-v1";
  var STATIC_PREFIX = "/static/";

  self.addEventListener("install", function (event) {
    self.skipWaiting();
  });

  self.addEventListener("activate", function (event) {
    event.waitUntil(
      caches.keys().then(function (names) {
        return Promise.all(
          names
            .filter(function (n) { return n !== CACHE_NAME; })
            .map(function (n) { return caches.delete(n); })
        );
      }).then(function () {
        return self.clients.claim();
      })
    );
  });

  self.addEventListener("fetch", function (event) {
    var req = event.request;
    if (req.method !== "GET") return;

    var url = new URL(req.url);
    if (url.origin !== self.location.origin) return;
    if (url.pathname.indexOf(STATIC_PREFIX) !== 0) return;

    event.respondWith(
      caches.open(CACHE_NAME).then(function (cache) {
        return cache.match(req).then(function (cached) {
          if (cached) return cached;
          return fetch(req).then(function (res) {
            if (res && res.ok) cache.put(req, res.clone());
            return res;
          });
        });
      })
    );
  });
})();
