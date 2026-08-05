/* pipe_tools.js — Technical Offer helper calculators for piping engineers.
 *
 * Tools (opened from the Technical tools panel):
 *   1) NPS ↔ DN  — ASME B36.10 / B36.19 steel sizes (lookup table)
 *   2) Polymer DN — PE / PP / PVC common metric sizes (lookup)
 *   3) Schedule → wall thickness (ASME B36.10 carbon steel)
 *   4) Unit converter — pressure / length / temperature / mass (math)
 *
 * Data strategy:
 *   - Standard pipe sizes & schedules → embedded lookup tables (same idea as CSV/JSON)
 *   - Unit conversions → pure formulas (no table needed)
 */
(function (window, document) {
  'use strict';

  /* ── ASME NPS / DN / OD (mm) — steel pipe ─────────────────────────────── */
  // nps: nominal inch label, dn: DN number, od: outside diameter mm
  var STEEL_SIZES = [
    { nps: '⅛',   npsNum: 0.125, dn: 6,   od: 10.3 },
    { nps: '¼',   npsNum: 0.25,  dn: 8,   od: 13.7 },
    { nps: '⅜',   npsNum: 0.375, dn: 10,  od: 17.1 },
    { nps: '½',   npsNum: 0.5,   dn: 15,  od: 21.3 },
    { nps: '¾',   npsNum: 0.75,  dn: 20,  od: 26.7 },
    { nps: '1',   npsNum: 1,     dn: 25,  od: 33.4 },
    { nps: '1¼',  npsNum: 1.25,  dn: 32,  od: 42.2 },
    { nps: '1½',  npsNum: 1.5,   dn: 40,  od: 48.3 },
    { nps: '2',   npsNum: 2,     dn: 50,  od: 60.3 },
    { nps: '2½',  npsNum: 2.5,   dn: 65,  od: 73.0 },
    { nps: '3',   npsNum: 3,     dn: 80,  od: 88.9 },
    { nps: '3½',  npsNum: 3.5,   dn: 90,  od: 101.6 },
    { nps: '4',   npsNum: 4,     dn: 100, od: 114.3 },
    { nps: '5',   npsNum: 5,     dn: 125, od: 141.3 },
    { nps: '6',   npsNum: 6,     dn: 150, od: 168.3 },
    { nps: '8',   npsNum: 8,     dn: 200, od: 219.1 },
    { nps: '10',  npsNum: 10,    dn: 250, od: 273.0 },
    { nps: '12',  npsNum: 12,    dn: 300, od: 323.9 },
    { nps: '14',  npsNum: 14,    dn: 350, od: 355.6 },
    { nps: '16',  npsNum: 16,    dn: 400, od: 406.4 },
    { nps: '18',  npsNum: 18,    dn: 450, od: 457.0 },
    { nps: '20',  npsNum: 20,    dn: 500, od: 508.0 },
    { nps: '24',  npsNum: 24,    dn: 600, od: 610.0 },
    { nps: '26',  npsNum: 26,    dn: 650, od: 660.0 },
    { nps: '28',  npsNum: 28,    dn: 700, od: 711.0 },
    { nps: '30',  npsNum: 30,    dn: 750, od: 762.0 },
    { nps: '32',  npsNum: 32,    dn: 800, od: 813.0 },
    { nps: '36',  npsNum: 36,    dn: 900, od: 914.0 },
    { nps: '40',  npsNum: 40,    dn: 1000, od: 1016.0 },
    { nps: '42',  npsNum: 42,    dn: 1050, od: 1067.0 },
    { nps: '48',  npsNum: 48,    dn: 1200, od: 1219.0 }
  ];

  /* ── Polymer metric DN (ISO / EN common) — OD = DN for PE/PP/PVC ─────── */
  var POLYMER_DN = [
    16, 20, 25, 32, 40, 50, 63, 75, 90, 110, 125, 140, 160, 180, 200,
    225, 250, 280, 315, 355, 400, 450, 500, 560, 630, 710, 800, 900, 1000, 1200
  ];

  var POLYMER_SDR = [7.4, 9, 11, 13.6, 17, 17.6, 21, 26, 33, 41];

  /* ── Schedule wall thickness (mm) keyed by NPS number — ASME B36.10 ──── */
  // Only common schedules; empty string = not standard for that size
  var SCH_THK = {
    // npsNum: { sch5s, sch10, sch10s, sch20, sch30, sch40, sch40s, sch60, sch80, sch80s, sch100, sch120, sch140, sch160, xxs }
    0.5:  { '5S': 1.65, '10': 2.11, '10S': 2.11, '40': 2.77, '40S': 2.77, '80': 3.73, '80S': 3.73, '160': 4.78, 'XXS': 7.47 },
    0.75: { '5S': 1.65, '10': 2.11, '10S': 2.11, '40': 2.87, '40S': 2.87, '80': 3.91, '80S': 3.91, '160': 5.56, 'XXS': 7.82 },
    1:    { '5S': 1.65, '10': 2.77, '10S': 2.77, '40': 3.38, '40S': 3.38, '80': 4.55, '80S': 4.55, '160': 6.35, 'XXS': 9.09 },
    1.25: { '5S': 1.65, '10': 2.77, '10S': 2.77, '40': 3.56, '40S': 3.56, '80': 4.85, '80S': 4.85, '160': 6.35, 'XXS': 9.70 },
    1.5:  { '5S': 1.65, '10': 2.77, '10S': 2.77, '40': 3.68, '40S': 3.68, '80': 5.08, '80S': 5.08, '160': 7.14, 'XXS': 10.15 },
    2:    { '5S': 1.65, '10': 2.77, '10S': 2.77, '40': 3.91, '40S': 3.91, '80': 5.54, '80S': 5.54, '160': 8.74, 'XXS': 11.07 },
    2.5:  { '5S': 2.11, '10': 3.05, '10S': 3.05, '40': 5.16, '40S': 5.16, '80': 7.01, '80S': 7.01, '160': 9.53, 'XXS': 14.02 },
    3:    { '5S': 2.11, '10': 3.05, '10S': 3.05, '40': 5.49, '40S': 5.49, '80': 7.62, '80S': 7.62, '160': 11.13, 'XXS': 15.24 },
    3.5:  { '5S': 2.11, '10': 3.05, '10S': 3.05, '40': 5.74, '40S': 5.74, '80': 8.08, '80S': 8.08, 'XXS': 16.15 },
    4:    { '5S': 2.11, '10': 3.05, '10S': 3.05, '40': 6.02, '40S': 6.02, '80': 8.56, '80S': 8.56, '120': 11.13, '160': 13.49, 'XXS': 17.12 },
    5:    { '5S': 2.77, '10': 3.40, '10S': 3.40, '40': 6.55, '40S': 6.55, '80': 9.53, '80S': 9.53, '120': 12.70, '160': 15.88, 'XXS': 19.05 },
    6:    { '5S': 2.77, '10': 3.40, '10S': 3.40, '40': 7.11, '40S': 7.11, '80': 10.97, '80S': 10.97, '120': 14.27, '160': 18.26, 'XXS': 21.95 },
    8:    { '5S': 2.77, '10': 3.76, '10S': 3.76, '20': 6.35, '30': 7.04, '40': 8.18, '40S': 8.18, '60': 10.31, '80': 12.70, '80S': 12.70, '100': 15.09, '120': 18.26, '140': 20.62, '160': 23.01, 'XXS': 22.23 },
    10:   { '5S': 3.40, '10': 4.19, '10S': 4.19, '20': 6.35, '30': 7.80, '40': 9.27, '40S': 9.27, '60': 12.70, '80': 15.09, '80S': 12.70, '100': 18.26, '120': 21.44, '140': 25.40, '160': 28.58, 'XXS': 25.40 },
    12:   { '5S': 3.96, '10': 4.57, '10S': 4.57, '20': 6.35, '30': 8.38, '40': 10.31, '40S': 9.53, '60': 14.27, '80': 17.48, '80S': 12.70, '100': 21.44, '120': 25.40, '140': 28.58, '160': 33.32, 'XXS': 25.40 },
    14:   { '10': 6.35, '10S': 4.78, '20': 7.92, '30': 9.53, '40': 11.13, '40S': 9.53, '60': 15.09, '80': 19.05, '80S': 12.70, '100': 23.83, '120': 27.79, '140': 31.75, '160': 35.71 },
    16:   { '10': 6.35, '10S': 4.78, '20': 7.92, '30': 9.53, '40': 12.70, '40S': 9.53, '60': 16.66, '80': 21.44, '80S': 12.70, '100': 26.19, '120': 30.96, '140': 36.53, '160': 40.49 },
    18:   { '10': 6.35, '10S': 4.78, '20': 7.92, '30': 11.13, '40': 14.27, '40S': 9.53, '60': 19.05, '80': 23.83, '80S': 12.70, '100': 29.36, '120': 34.93, '140': 39.67, '160': 45.24 },
    20:   { '10': 6.35, '10S': 4.78, '20': 9.53, '30': 12.70, '40': 15.09, '40S': 9.53, '60': 20.62, '80': 26.19, '80S': 12.70, '100': 32.54, '120': 38.10, '140': 44.45, '160': 50.01 },
    24:   { '10': 6.35, '10S': 5.54, '20': 9.53, '30': 14.27, '40': 17.48, '40S': 9.53, '60': 24.61, '80': 30.96, '80S': 12.70, '100': 38.89, '120': 45.24, '140': 52.37, '160': 59.54 }
  };

  var SCH_LIST = ['5S', '10', '10S', '20', '30', '40', '40S', '60', '80', '80S', '100', '120', '140', '160', 'XXS'];

  /* ── Unit conversion factors (to SI base) ─────────────────────────────── */
  var UNITS = {
    pressure: {
      label: 'Pressure',
      base: 'bar',
      opts: [
        { id: 'bar',  label: 'bar',  toBase: 1 },
        { id: 'psi',  label: 'psi',  toBase: 0.0689476 },
        { id: 'kpa',  label: 'kPa',  toBase: 0.01 },
        { id: 'mpa',  label: 'MPa',  toBase: 10 },
        { id: 'atm',  label: 'atm',  toBase: 1.01325 },
        { id: 'kgcm', label: 'kg/cm²', toBase: 0.980665 }
      ]
    },
    length: {
      label: 'Length',
      base: 'mm',
      opts: [
        { id: 'mm',  label: 'mm',  toBase: 1 },
        { id: 'cm',  label: 'cm',  toBase: 10 },
        { id: 'm',   label: 'm',   toBase: 1000 },
        { id: 'in',  label: 'in',  toBase: 25.4 },
        { id: 'ft',  label: 'ft',  toBase: 304.8 }
      ]
    },
    mass: {
      label: 'Mass / Weight',
      base: 'kg',
      opts: [
        { id: 'kg',  label: 'kg',   toBase: 1 },
        { id: 'g',   label: 'g',    toBase: 0.001 },
        { id: 'lb',  label: 'lb',   toBase: 0.453592 },
        { id: 'ton', label: 'tonne', toBase: 1000 }
      ]
    },
    temp: {
      label: 'Temperature',
      base: 'c',
      opts: [
        { id: 'c', label: '°C' },
        { id: 'f', label: '°F' },
        { id: 'k', label: 'K' }
      ]
    }
  };

  /* ── Valve trim chart (API 600 / API 602 standard trim numbers) ───────────
   * Trim = the wetted internal working parts of a valve (seat/seating surface,
   * disc or wedge/ball, and stem). The trim NUMBER is a shorthand for the
   * material set. Values below follow the common API 600 trim table; always
   * confirm against the specific manufacturer's trim chart for the exact grade. */
  var VALVE_TRIM = [
    { no: '1',  name: 'F6 (13Cr)',            seat: '410 SS (13Cr)',        wedge: '410 SS (13Cr)', stem: '410 SS (13Cr)', temp: '≤ 425 °C', service: 'General service, non-corrosive' },
    { no: '2',  name: '304 (18-8)',           seat: '304 SS',               wedge: '304 SS',        stem: '304 SS',        temp: '≤ 540 °C', service: 'Mildly corrosive fluids' },
    { no: '3',  name: 'F310',                 seat: '310 SS',               wedge: '310 SS',        stem: '310 SS',        temp: '≤ 815 °C', service: 'High-temperature service' },
    { no: '4',  name: 'Hardfaced',            seat: 'Stellite (hardfaced)', wedge: 'Stellite',      stem: '410 SS',        temp: '≤ 540 °C', service: 'Erosive flow / high ΔP' },
    { no: '5',  name: '410 + Hardfaced',      seat: 'Stellite on 410',      wedge: '410 SS',        stem: '410 SS',        temp: '≤ 540 °C', service: 'Steam / erosive' },
    { no: '8',  name: '410 + Hardfaced seat', seat: 'Stellite on 410',      wedge: '410 SS',        stem: '410 SS',        temp: '≤ 540 °C', service: 'Steam, boiler feed' },
    { no: '9',  name: 'Monel',                seat: 'Monel',                wedge: 'Monel',         stem: 'Monel',         temp: '≤ 480 °C', service: 'Salt water / halogens' },
    { no: '10', name: '316 (18-8-Mo)',        seat: '316 SS',               wedge: '316 SS',        stem: '316 SS',        temp: '≤ 540 °C', service: 'Corrosive fluids' },
    { no: '12', name: '316 + Hardfaced',      seat: 'Stellite on 316',      wedge: '316 SS',        stem: '316 SS',        temp: '≤ 540 °C', service: 'Corrosive + erosive' },
    { no: '13', name: 'Alloy 20',             seat: 'Alloy 20',             wedge: 'Alloy 20',      stem: 'Alloy 20',      temp: '≤ 425 °C', service: 'Sulphuric acid / acids' },
    { no: '14', name: '304 + Hardfaced',      seat: 'Stellite on 304',      wedge: '304 SS',        stem: '304 SS',        temp: '≤ 540 °C', service: 'Corrosive + erosive' },
    { no: '15', name: '316 + Hardfaced',      seat: 'Stellite on 316',      wedge: '316 SS',        stem: '316 SS',        temp: '≤ 540 °C', service: 'Corrosive + erosive' },
    { no: '16', name: 'Hard-Cr 13Cr',         seat: 'Hard-chrome 13Cr',     wedge: '13Cr',          stem: '13Cr',          temp: '≤ 425 °C', service: 'Wear / abrasion resistant' },
    { no: '18', name: 'Alloy 20 + Hardfaced', seat: 'Stellite on Alloy 20', wedge: 'Alloy 20',      stem: 'Alloy 20',      temp: '≤ 425 °C', service: 'Acids + erosive' }
  ];

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function fmt(n, digits) {
    if (!isFinite(n)) return '—';
    var d = digits == null ? 2 : digits;
    var s = Number(n).toFixed(d);
    return s.replace(/\.?0+$/, '') || '0';
  }

  function parseNum(raw) {
    var s = String(raw || '').trim().replace(/,/g, '').replace(/"/g, '');
    if (!s) return NaN;
    // fractions like 1-1/2 or 1 1/2 or 1/2
    var m = s.match(/^(\d+)\s*[- ]\s*(\d+)\s*\/\s*(\d+)$/);
    if (m) return parseFloat(m[1]) + parseFloat(m[2]) / parseFloat(m[3]);
    m = s.match(/^(\d+)\s*\/\s*(\d+)$/);
    if (m) return parseFloat(m[1]) / parseFloat(m[2]);
    return parseFloat(s);
  }

  function findSteelByNps(npsNum) {
    for (var i = 0; i < STEEL_SIZES.length; i++) {
      if (Math.abs(STEEL_SIZES[i].npsNum - npsNum) < 0.001) return STEEL_SIZES[i];
    }
    return null;
  }

  function findSteelByDn(dn) {
    for (var i = 0; i < STEEL_SIZES.length; i++) {
      if (STEEL_SIZES[i].dn === dn) return STEEL_SIZES[i];
    }
    return null;
  }

  function findSteelByOd(od) {
    var best = null, bestDiff = Infinity;
    for (var i = 0; i < STEEL_SIZES.length; i++) {
      var d = Math.abs(STEEL_SIZES[i].od - od);
      if (d < bestDiff) { bestDiff = d; best = STEEL_SIZES[i]; }
    }
    return (best && bestDiff <= 1.5) ? best : null;
  }

  function tempToC(v, from) {
    if (from === 'c') return v;
    if (from === 'f') return (v - 32) * 5 / 9;
    if (from === 'k') return v - 273.15;
    return NaN;
  }

  function tempFromC(c, to) {
    if (to === 'c') return c;
    if (to === 'f') return c * 9 / 5 + 32;
    if (to === 'k') return c + 273.15;
    return NaN;
  }

  /* ── Modal shell ──────────────────────────────────────────────────────── */
  function ensureModal() {
    var el = document.getElementById('ft-pipe-tools-modal');
    if (el) return el;
    el = document.createElement('div');
    el.id = 'ft-pipe-tools-modal';
    el.className = 'ft-pt-overlay';
    el.hidden = true;
    el.innerHTML =
      '<div class="ft-pt-dialog" role="dialog" aria-modal="true" aria-labelledby="ft-pt-title">' +
        '<div class="ft-pt-head">' +
          '<h3 id="ft-pt-title"><i class="fa-solid fa-calculator"></i> <span>Pipe tools</span></h3>' +
          '<button type="button" class="ft-pt-close" aria-label="Close"><i class="fa-solid fa-xmark"></i></button>' +
        '</div>' +
        '<div class="ft-pt-tabs" role="tablist">' +
          '<button type="button" class="ft-pt-tab active" data-tab="nps" role="tab">NPS ↔ DN</button>' +
          '<button type="button" class="ft-pt-tab" data-tab="polymer" role="tab">Polymer</button>' +
          '<button type="button" class="ft-pt-tab" data-tab="sch" role="tab">Schedule</button>' +
          '<button type="button" class="ft-pt-tab" data-tab="units" role="tab">Units</button>' +
          '<button type="button" class="ft-pt-tab" data-tab="valvetrim" role="tab">Valve trim</button>' +
        '</div>' +
        '<div class="ft-pt-body">' +
          '<div class="ft-pt-pane" data-pane="nps"></div>' +
          '<div class="ft-pt-pane" data-pane="polymer" hidden></div>' +
          '<div class="ft-pt-pane" data-pane="sch" hidden></div>' +
          '<div class="ft-pt-pane" data-pane="units" hidden></div>' +
          '<div class="ft-pt-pane" data-pane="valvetrim" hidden></div>' +
        '</div>' +
      '</div>';
    document.body.appendChild(el);

    el.querySelector('.ft-pt-close').addEventListener('click', closeModal);
    el.addEventListener('click', function (e) {
      if (e.target === el) closeModal();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && !el.hidden) closeModal();
    });
    el.querySelectorAll('.ft-pt-tab').forEach(function (tab) {
      tab.addEventListener('click', function () {
        switchTab(tab.getAttribute('data-tab'));
      });
    });

    buildNpsPane(el.querySelector('[data-pane="nps"]'));
    buildPolymerPane(el.querySelector('[data-pane="polymer"]'));
    buildSchPane(el.querySelector('[data-pane="sch"]'));
    buildUnitsPane(el.querySelector('[data-pane="units"]'));
    buildValveTrimPane(el.querySelector('[data-pane="valvetrim"]'));
    return el;
  }

  function openModal(tab) {
    var el = ensureModal();
    el.hidden = false;
    document.body.classList.add('ft-pt-open');
    if (tab) switchTab(tab);
    var first = el.querySelector('.ft-pt-pane:not([hidden]) input, .ft-pt-pane:not([hidden]) select');
    if (first) setTimeout(function () { first.focus(); first.select && first.select(); }, 40);
  }

  function closeModal() {
    var el = document.getElementById('ft-pipe-tools-modal');
    if (el) el.hidden = true;
    document.body.classList.remove('ft-pt-open');
  }

  function switchTab(name) {
    var el = document.getElementById('ft-pipe-tools-modal');
    if (!el) return;
    el.querySelectorAll('.ft-pt-tab').forEach(function (t) {
      t.classList.toggle('active', t.getAttribute('data-tab') === name);
    });
    el.querySelectorAll('.ft-pt-pane').forEach(function (p) {
      p.hidden = p.getAttribute('data-pane') !== name;
    });
    var titles = {
      nps: 'NPS ↔ DN (steel)',
      polymer: 'Polymer DN / SDR',
      sch: 'Schedule → thickness',
      units: 'Unit converter',
      valvetrim: 'Valve trim chart'
    };
    var titleSpan = el.querySelector('#ft-pt-title span');
    if (titleSpan) titleSpan.textContent = titles[name] || 'Pipe tools';
  }

  function resultCard(html) {
    return '<div class="ft-pt-result">' + html + '</div>';
  }

  function copyBtn(text) {
    return '<button type="button" class="ft-pt-copy" data-copy="' + esc(text) + '" title="Copy">' +
      '<i class="fa-solid fa-copy"></i></button>';
  }

  function wireCopy(root) {
    root.addEventListener('click', function (e) {
      var btn = e.target.closest ? e.target.closest('.ft-pt-copy') : null;
      if (!btn) return;
      var txt = btn.getAttribute('data-copy') || '';
      if (!txt) return;
      function done() {
        btn.classList.add('ok');
        setTimeout(function () { btn.classList.remove('ok'); }, 800);
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(txt).then(done, done);
      } else {
        try {
          var ta = document.createElement('textarea');
          ta.value = txt; document.body.appendChild(ta); ta.select();
          document.execCommand('copy'); ta.remove();
        } catch (_e) {}
        done();
      }
    });
  }

  /* ── Tab 1: NPS ↔ DN ──────────────────────────────────────────────────── */
  function buildNpsPane(pane) {
    var opts = STEEL_SIZES.map(function (s) {
      return '<option value="' + s.npsNum + '">NPS ' + esc(s.nps) + '  →  DN ' + s.dn + '</option>';
    }).join('');
    pane.innerHTML =
      '<p class="ft-pt-hint">ASME B36.10 / B36.19 steel pipe. Type NPS, DN or OD — or pick from the list.</p>' +
      '<div class="ft-pt-grid3">' +
        '<div class="ft-pt-field"><label>NPS (inch)</label>' +
          '<input type="text" id="pt-nps" placeholder="e.g. 2 or 1½" autocomplete="off"></div>' +
        '<div class="ft-pt-field"><label>DN</label>' +
          '<input type="text" id="pt-dn" placeholder="e.g. 50" autocomplete="off"></div>' +
        '<div class="ft-pt-field"><label>OD (mm)</label>' +
          '<input type="text" id="pt-od" placeholder="e.g. 60.3" autocomplete="off"></div>' +
      '</div>' +
      '<div class="ft-pt-field"><label>Quick pick</label>' +
        '<select id="pt-nps-pick"><option value="">— select size —</option>' + opts + '</select></div>' +
      '<div id="pt-nps-out"></div>';
    wireCopy(pane);

    var npsIn = pane.querySelector('#pt-nps');
    var dnIn = pane.querySelector('#pt-dn');
    var odIn = pane.querySelector('#pt-od');
    var pick = pane.querySelector('#pt-nps-pick');
    var out = pane.querySelector('#pt-nps-out');
    var lock = false;

    function show(row) {
      if (!row) {
        out.innerHTML = resultCard('<span class="ft-pt-miss">No matching standard size.</span>');
        return;
      }
      var line = 'NPS ' + row.nps + '  =  DN ' + row.dn + '  ·  OD ' + fmt(row.od, 1) + ' mm';
      out.innerHTML = resultCard(
        '<div class="ft-pt-kv">' +
          '<div><b>NPS</b><span>' + esc(row.nps) + '"</span></div>' +
          '<div><b>DN</b><span>' + row.dn + '</span></div>' +
          '<div><b>OD</b><span>' + fmt(row.od, 1) + ' mm</span></div>' +
        '</div>' +
        '<div class="ft-pt-line">' + esc(line) + ' ' + copyBtn(line) + '</div>'
      );
    }

    function fill(row, source) {
      if (!row) { show(null); return; }
      lock = true;
      if (source !== 'nps') npsIn.value = row.nps;
      if (source !== 'dn') dnIn.value = String(row.dn);
      if (source !== 'od') odIn.value = fmt(row.od, 1);
      pick.value = String(row.npsNum);
      lock = false;
      show(row);
    }

    npsIn.addEventListener('input', function () {
      if (lock) return;
      var n = parseNum(npsIn.value);
      fill(isFinite(n) ? findSteelByNps(n) : null, 'nps');
    });
    dnIn.addEventListener('input', function () {
      if (lock) return;
      var n = parseInt(dnIn.value, 10);
      fill(isFinite(n) ? findSteelByDn(n) : null, 'dn');
    });
    odIn.addEventListener('input', function () {
      if (lock) return;
      var n = parseNum(odIn.value);
      fill(isFinite(n) ? findSteelByOd(n) : null, 'od');
    });
    pick.addEventListener('change', function () {
      if (!pick.value) return;
      fill(findSteelByNps(parseFloat(pick.value)), 'pick');
    });
  }

  /* ── Tab 2: Polymer DN / SDR ──────────────────────────────────────────── */
  function buildPolymerPane(pane) {
    var dnOpts = POLYMER_DN.map(function (d) {
      return '<option value="' + d + '">DN / OD ' + d + ' mm</option>';
    }).join('');
    var sdrOpts = POLYMER_SDR.map(function (s) {
      return '<option value="' + s + '">SDR ' + s + '</option>';
    }).join('');
    pane.innerHTML =
      '<p class="ft-pt-hint">Metric polymer pipe (PE / PP / PVC). OD equals DN. Wall = OD ÷ SDR.</p>' +
      '<div class="ft-pt-grid2">' +
        '<div class="ft-pt-field"><label>DN / OD (mm)</label>' +
          '<select id="pt-poly-dn">' + dnOpts + '</select></div>' +
        '<div class="ft-pt-field"><label>SDR</label>' +
          '<select id="pt-poly-sdr">' + sdrOpts + '</select></div>' +
      '</div>' +
      '<div class="ft-pt-field"><label>Or type OD (mm)</label>' +
        '<input type="text" id="pt-poly-od" placeholder="e.g. 110" autocomplete="off"></div>' +
      '<div id="pt-poly-out"></div>';
    wireCopy(pane);

    var dnSel = pane.querySelector('#pt-poly-dn');
    var sdrSel = pane.querySelector('#pt-poly-sdr');
    var odIn = pane.querySelector('#pt-poly-od');
    var out = pane.querySelector('#pt-poly-out');

    function calc() {
      var od = parseNum(odIn.value);
      if (!isFinite(od) || od <= 0) od = parseFloat(dnSel.value);
      var sdr = parseFloat(sdrSel.value);
      if (!isFinite(od) || !isFinite(sdr) || sdr <= 0) {
        out.innerHTML = '';
        return;
      }
      var wall = od / sdr;
      var id = od - 2 * wall;
      if (id < 0) id = 0;
      var line = 'DN/OD ' + fmt(od, 1) + ' mm · SDR ' + sdr + ' · wall ' + fmt(wall, 2) + ' mm · ID ≈ ' + fmt(id, 2) + ' mm';
      out.innerHTML = resultCard(
        '<div class="ft-pt-kv">' +
          '<div><b>OD</b><span>' + fmt(od, 1) + ' mm</span></div>' +
          '<div><b>Wall</b><span>' + fmt(wall, 2) + ' mm</span></div>' +
          '<div><b>ID ≈</b><span>' + fmt(id, 2) + ' mm</span></div>' +
        '</div>' +
        '<div class="ft-pt-line">' + esc(line) + ' ' + copyBtn(line) + '</div>' +
        '<p class="ft-pt-note">ID is approximate (no manufacturing tolerance). Check the product datasheet for exact values.</p>'
      );
    }

    dnSel.addEventListener('change', function () {
      odIn.value = dnSel.value;
      calc();
    });
    sdrSel.addEventListener('change', calc);
    odIn.addEventListener('input', calc);
    odIn.value = dnSel.value;
    calc();
  }

  /* ── Tab 3: Schedule → thickness ──────────────────────────────────────── */
  function buildSchPane(pane) {
    var sizeOpts = STEEL_SIZES.filter(function (s) { return SCH_THK[s.npsNum]; }).map(function (s) {
      return '<option value="' + s.npsNum + '">NPS ' + esc(s.nps) + ' (DN ' + s.dn + ')</option>';
    }).join('');
    var schOpts = SCH_LIST.map(function (s) {
      return '<option value="' + s + '"' + (s === '40' ? ' selected' : '') + '>Sch ' + s + '</option>';
    }).join('');
    pane.innerHTML =
      '<p class="ft-pt-hint">ASME B36.10 wall thickness for carbon steel. Stainless Sch 5S/10S/40S/80S included where standard.</p>' +
      '<div class="ft-pt-grid2">' +
        '<div class="ft-pt-field"><label>Pipe size</label>' +
          '<select id="pt-sch-size">' + sizeOpts + '</select></div>' +
        '<div class="ft-pt-field"><label>Schedule</label>' +
          '<select id="pt-sch-sch">' + schOpts + '</select></div>' +
      '</div>' +
      '<div id="pt-sch-out"></div>';
    wireCopy(pane);

    var sizeSel = pane.querySelector('#pt-sch-size');
    var schSel = pane.querySelector('#pt-sch-sch');
    var out = pane.querySelector('#pt-sch-out');

    function calc() {
      var npsNum = parseFloat(sizeSel.value);
      var sch = schSel.value;
      var row = findSteelByNps(npsNum);
      var map = SCH_THK[npsNum] || {};
      var thk = map[sch];
      if (!row || thk == null) {
        out.innerHTML = resultCard('<span class="ft-pt-miss">Sch ' + esc(sch) + ' is not standard for this size.</span>');
        return;
      }
      var id = row.od - 2 * thk;
      var line = 'NPS ' + row.nps + ' Sch ' + sch + ' · OD ' + fmt(row.od, 1) + ' mm · wall ' + fmt(thk, 2) + ' mm · ID ' + fmt(id, 2) + ' mm';
      // Also list other available schedules for this size
      var others = SCH_LIST.filter(function (s) { return map[s] != null; }).map(function (s) {
        return '<button type="button" class="ft-pt-chip' + (s === sch ? ' on' : '') + '" data-sch="' + s + '">Sch ' + s + ': ' + fmt(map[s], 2) + '</button>';
      }).join('');
      out.innerHTML = resultCard(
        '<div class="ft-pt-kv">' +
          '<div><b>OD</b><span>' + fmt(row.od, 1) + ' mm</span></div>' +
          '<div><b>Wall</b><span>' + fmt(thk, 2) + ' mm</span></div>' +
          '<div><b>ID</b><span>' + fmt(id, 2) + ' mm</span></div>' +
        '</div>' +
        '<div class="ft-pt-line">' + esc(line) + ' ' + copyBtn(line) + '</div>' +
        '<div class="ft-pt-chips">' + others + '</div>'
      );
      out.querySelectorAll('.ft-pt-chip').forEach(function (btn) {
        btn.addEventListener('click', function () {
          schSel.value = btn.getAttribute('data-sch');
          calc();
        });
      });
    }

    sizeSel.addEventListener('change', calc);
    schSel.addEventListener('change', calc);
    calc();
  }

  /* ── Tab 4: Unit converter ────────────────────────────────────────────── */
  function buildUnitsPane(pane) {
    var catOpts = Object.keys(UNITS).map(function (k) {
      return '<option value="' + k + '">' + esc(UNITS[k].label) + '</option>';
    }).join('');
    pane.innerHTML =
      '<p class="ft-pt-hint">Live conversion. Change category, type a value — result updates instantly.</p>' +
      '<div class="ft-pt-field"><label>Category</label>' +
        '<select id="pt-u-cat">' + catOpts + '</select></div>' +
      '<div class="ft-pt-grid2">' +
        '<div class="ft-pt-field"><label>From</label>' +
          '<div class="ft-pt-unitrow">' +
            '<input type="text" id="pt-u-from" value="1" autocomplete="off">' +
            '<select id="pt-u-from-u"></select>' +
          '</div></div>' +
        '<div class="ft-pt-field"><label>To</label>' +
          '<div class="ft-pt-unitrow">' +
            '<input type="text" id="pt-u-to" readonly>' +
            '<select id="pt-u-to-u"></select>' +
          '</div></div>' +
      '</div>' +
      '<div id="pt-u-out"></div>' +
      '<button type="button" class="ft-pt-swap" id="pt-u-swap"><i class="fa-solid fa-right-left"></i> Swap units</button>';
    wireCopy(pane);

    var catSel = pane.querySelector('#pt-u-cat');
    var fromIn = pane.querySelector('#pt-u-from');
    var toIn = pane.querySelector('#pt-u-to');
    var fromU = pane.querySelector('#pt-u-from-u');
    var toU = pane.querySelector('#pt-u-to-u');
    var out = pane.querySelector('#pt-u-out');
    var swap = pane.querySelector('#pt-u-swap');

    function fillUnitSelects() {
      var cat = UNITS[catSel.value];
      var html = cat.opts.map(function (o) {
        return '<option value="' + o.id + '">' + esc(o.label) + '</option>';
      }).join('');
      fromU.innerHTML = html;
      toU.innerHTML = html;
      if (cat.opts.length > 1) toU.value = cat.opts[1].id;
      calc();
    }

    function calc() {
      var cat = UNITS[catSel.value];
      var v = parseNum(fromIn.value);
      if (!isFinite(v)) { toIn.value = ''; out.innerHTML = ''; return; }
      var result;
      if (catSel.value === 'temp') {
        result = tempFromC(tempToC(v, fromU.value), toU.value);
      } else {
        var fromOpt = cat.opts.filter(function (o) { return o.id === fromU.value; })[0];
        var toOpt = cat.opts.filter(function (o) { return o.id === toU.value; })[0];
        if (!fromOpt || !toOpt) return;
        result = v * fromOpt.toBase / toOpt.toBase;
      }
      var digits = Math.abs(result) >= 100 ? 2 : (Math.abs(result) >= 1 ? 4 : 6);
      toIn.value = fmt(result, digits);
      var fromLabel = cat.opts.filter(function (o) { return o.id === fromU.value; })[0].label;
      var toLabel = cat.opts.filter(function (o) { return o.id === toU.value; })[0].label;
      var line = fmt(v, 6) + ' ' + fromLabel + ' = ' + fmt(result, digits) + ' ' + toLabel;
      out.innerHTML = resultCard(
        '<div class="ft-pt-line">' + esc(line) + ' ' + copyBtn(line) + '</div>'
      );
    }

    catSel.addEventListener('change', fillUnitSelects);
    fromIn.addEventListener('input', calc);
    fromU.addEventListener('change', calc);
    toU.addEventListener('change', calc);
    swap.addEventListener('click', function () {
      var a = fromU.value, b = toU.value;
      fromU.value = b; toU.value = a;
      fromIn.value = toIn.value || fromIn.value;
      calc();
    });
    fillUnitSelects();
  }

  /* ── Tab 5: Valve trim chart ──────────────────────────────────────────── */
  function buildValveTrimPane(pane) {
    pane.innerHTML =
      '<p class="ft-pt-hint">API 600 valve <b>trim numbers</b> — the material set of the wetted working parts ' +
      '(seat / disc-wedge / stem). Search by trim no., material or service.</p>' +
      '<div class="ft-pt-field"><label>Search trim</label>' +
        '<input type="text" id="pt-vt-q" placeholder="e.g. 8, 316, hardfaced, steam" autocomplete="off"></div>' +
      '<div id="pt-vt-out" class="ft-pt-vt-out"></div>' +
      '<p class="ft-pt-note">Reference only — always confirm the exact grade against the valve maker\u2019s ' +
      'trim chart and the project material spec.</p>';
    wireCopy(pane);

    var q = pane.querySelector('#pt-vt-q');
    var out = pane.querySelector('#pt-vt-out');

    function matches(t, needle) {
      if (!needle) return true;
      var hay = ('trim' + t.no + ' ' + t.no + ' ' + t.name + ' ' + t.seat + ' ' + t.wedge + ' ' +
                 t.stem + ' ' + t.temp + ' ' + t.service).toLowerCase();
      return needle.toLowerCase().split(/\s+/).every(function (w) { return hay.indexOf(w) >= 0; });
    }

    function render() {
      var needle = (q.value || '').trim();
      var list = VALVE_TRIM.filter(function (t) { return matches(t, needle); });
      if (!list.length) {
        out.innerHTML = resultCard('<span class="ft-pt-miss">No trim matches this search.</span>');
        return;
      }
      var rows = list.map(function (t) {
        var trimLabel = 'trim' + t.no;
        var line = trimLabel + ' (' + t.name + ') · seat ' + t.seat + ' · disc/wedge ' +
                   t.wedge + ' · stem ' + t.stem + ' · ' + t.temp;
        return '<tr>' +
          '<td class="ft-pt-vt-no">' + esc(trimLabel) + '</td>' +
          '<td>' + esc(t.name) + '</td>' +
          '<td>' + esc(t.seat) + '</td>' +
          '<td>' + esc(t.wedge) + '</td>' +
          '<td>' + esc(t.stem) + '</td>' +
          '<td>' + esc(t.temp) + '</td>' +
          '<td class="ft-pt-vt-svc">' + esc(t.service) + '</td>' +
          '<td>' + copyBtn(line) + '</td>' +
        '</tr>';
      }).join('');
      out.innerHTML =
        '<div class="ft-pt-vt-scroll"><table class="ft-pt-vt-table">' +
        '<thead><tr><th>Trim</th><th>Identification</th><th>Seat</th><th>Disc / Wedge</th>' +
        '<th>Stem</th><th>Max temp</th><th>Typical service</th><th></th></tr></thead>' +
        '<tbody>' + rows + '</tbody></table></div>';
    }

    q.addEventListener('input', render);
    render();
  }

  /* ── Wire panel buttons ───────────────────────────────────────────────── */
  function wire() {
    var openers = document.querySelectorAll('[data-pipe-tool]');
    if (!openers.length) return;
    openers.forEach(function (btn) {
      if (btn.dataset.ptReady === '1') return;
      btn.dataset.ptReady = '1';
      btn.addEventListener('click', function () {
        openModal(btn.getAttribute('data-pipe-tool') || 'nps');
      });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    setTimeout(wire, 0);
  });

  window.FTPipeTools = { open: openModal, close: closeModal };
})(window, document);
