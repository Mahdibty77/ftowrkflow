/* Combined, Excel-style searchable filter dropdowns for the data-admin grids.
 *
 * Each filter only offers values still reachable given the OTHER active filters
 * (fetched live from the distinct-values API). Selecting a value narrows the
 * siblings; pressing Delete (or clearing the text) removes that filter.
 *
 * Usage:
 *   var GF = buildGridFilters({
 *     container: document.getElementById("filters"),
 *     distinctUrl: "/.../distinct/",
 *     onChange: function () { reloadGrid(); }
 *   });
 *   GF.getFilters();   // -> { "c3": "C.S", ... }
 *
 * Markup expected inside container:
 *   <div class="combo fcombo" data-col="3">
 *     <input class="combo-input" placeholder="All ...">
 *     <div class="combo-list"></div>
 *   </div>
 */
/* Display helper shared by all grids: upper-cases values, and renders the
 * "no/absent" variant "(no)coating" / "$coating$" / "$nocoating$" as "NO COATING". */
window.displayVal = function (v) {
  if (v == null) return "";
  var s = String(v).trim();
  var m = s.match(/^\(no\)\s*(.*)$/i);
  if (m) return "NO " + (m[1] || "").toUpperCase();
  var d = s.match(/^\$(.*)\$$/);
  if (d) {
    // "$coating$" → NO COATING; "$nocoating$" → NO COATING (strip leading "no")
    var inner = String(d[1] || "").replace(/^no\s*/i, "").trim() || String(d[1] || "");
    return "NO " + inner.toUpperCase();
  }
  return s.toUpperCase();
};

window.buildGridFilters = function (opts) {
  var container = opts.container;
  var distinctUrl = opts.distinctUrl;
  var onChange = opts.onChange || function () {};
  if (!container) return { getFilters: function () { return {}; } };

  var combos = [];
  // Client cache: same sibling-filters → reuse options (avoids repeat DISTINCT).
  var optCache = {};

  function qs(o) {
    return Object.keys(o).map(function (k) {
      return encodeURIComponent(k) + "=" + encodeURIComponent(o[k]);
    }).join("&");
  }

  function getFilters(exceptCol) {
    var f = {};
    combos.forEach(function (c) {
      if (c.value && c.col !== exceptCol) f["c" + c.col] = c.value;
    });
    return f;
  }

  function cacheKey(c) {
    var f = getFilters(c.col);
    var parts = Object.keys(f).sort().map(function (k) { return k + "=" + f[k]; });
    return String(c.col) + "|" + parts.join("&");
  }

  function showLoading(c) {
    c.list.innerHTML = "";
    var e = document.createElement("div");
    e.className = "combo-empty combo-loading";
    e.textContent = "Loading…";
    c.list.appendChild(e);
    c.list.classList.add("open");
  }

  function refreshOptions(c) {
    var key = cacheKey(c);
    if (Object.prototype.hasOwnProperty.call(optCache, key)) {
      c.options = optCache[key];
      c._cacheKey = key;
      return Promise.resolve(c.options);
    }
    if (c._inflight && c._inflightKey === key) return c._inflight;

    var params = getFilters(c.col);
    params.target = c.col;
    var p = fetch(distinctUrl + "?" + qs(params))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var vals = d.values || [];
        optCache[key] = vals;
        c.options = vals;
        c._cacheKey = key;
        return vals;
      })
      .catch(function () {
        c.options = c.options || [];
        return c.options;
      })
      .finally(function () {
        if (c._inflightKey === key) { c._inflight = null; c._inflightKey = ""; }
      });
    c._inflight = p;
    c._inflightKey = key;
    return p;
  }

  function invalidateOptCache() {
    optCache = {};
    combos.forEach(function (c) { c._cacheKey = ""; });
  }

  function render(c, filter) {
    var f = (filter || "").toLowerCase();
    c.list.innerHTML = "";
    var matches = (c.options || []).filter(function (v) {
      return !f || String(v).toLowerCase().indexOf(f) !== -1;
    });
    if (!matches.length) {
      var e = document.createElement("div");
      e.className = "combo-empty";
      e.textContent = "No matches";
      c.list.appendChild(e);
      return;
    }
    matches.slice(0, 1000).forEach(function (v) {
      var row = document.createElement("div");
      row.className = "combo-opt";
      var n = document.createElement("span");
      n.textContent = window.displayVal ? window.displayVal(v) : v;
      row.appendChild(n);
      row.addEventListener("mousedown", function (e) {
        e.preventDefault();
        commit(c, v);
      });
      c.list.appendChild(row);
    });
  }

  function commit(c, value) {
    c.value = value;
    c.input.value = window.displayVal ? window.displayVal(value) : value;
    c.list.classList.remove("open");
    invalidateOptCache();
    onChange();
  }

  function clear(c) {
    if (!c.value && !c.input.value) return;
    c.value = "";
    c.input.value = "";
    c.list.classList.remove("open");
    invalidateOptCache();
    onChange();
  }

  function clearAll() {
    var had = combos.some(function (c) { return c.value; });
    combos.forEach(function (c) { c.value = ""; c.input.value = ""; c.list.classList.remove("open"); });
    invalidateOptCache();
    if (had) onChange();
  }

  container.querySelectorAll(".fcombo").forEach(function (wrap) {
    var c = {
      col: parseInt(wrap.getAttribute("data-col"), 10),
      input: wrap.querySelector(".combo-input"),
      list: wrap.querySelector(".combo-list"),
      value: "",
      options: [],
      _cacheKey: "",
      _inflight: null,
      _inflightKey: ""
    };
    combos.push(c);

    c.input.addEventListener("focus", function () {
      var key = cacheKey(c);
      if (c._cacheKey === key && c.options && c.options.length) {
        render(c, "");
        c.list.classList.add("open");
        return;
      }
      showLoading(c);
      refreshOptions(c).then(function () {
        if (document.activeElement === c.input || c.list.classList.contains("open")) {
          render(c, c.input.value === (window.displayVal ? window.displayVal(c.value) : c.value) ? "" : c.input.value);
          c.list.classList.add("open");
        }
      });
    });
    c.input.addEventListener("input", function () {
      if (c.input.value === "" && c.value !== "") { clear(c); return; }
      render(c, c.input.value);
      c.list.classList.add("open");
    });
    c.input.addEventListener("keydown", function (e) {
      if (e.key === "Delete") { e.preventDefault(); clear(c); }
    });
    c.input.addEventListener("blur", function () {
      setTimeout(function () { c.list.classList.remove("open"); c.input.value = c.value || ""; }, 160);
    });
  });

  return { getFilters: function () { return getFilters(); }, clearAll: clearAll };
};
