/*
 * virtual_scroll_engine.js  (variable-height edition)
 * ------------------------------------------------------------
 * Fast virtual scroll with a permanently sticky header inside the table.
 *
 * Unlike the previous fixed-34px engine, this one MEASURES each row's real
 * height (rows grow when BRAND/TIME/remark wrap). Heights are cached and the
 * cumulative offset table is rebuilt whenever a row's height changes. This
 * makes the scrollbar length exact, so scrolling reaches the true bottom and
 * stops there (no bounce) while still letting rows grow with their content.
 */
(function (window, document) {
    'use strict';

    let allRowsArray = [];
    let filterMap = {};         // key -> predicate(tr); all AND-combined
    let filteredRows = null;    // cached subset when any filter is active
    let totalColumnsCount = 0;
    const EST_ROW_HEIGHT = 34;  // estimate before a row has been measured
    const BUFFER_PX = 400;      // render this many px above & below the viewport
    const BOTTOM_PAD = 8;
    let isTicking = false;
    let eaTopGap = 0;  // Extra top pad so EA panel can sit under the sticky header

    // height cache keyed by the row's virtual index, plus a prefix-sum offsets[]
    let heights = [];           // heights[i] = measured (or estimated) px of row i
    let offsets = [];           // offsets[i] = cumulative top of row i; offsets[n] = total

    function hasFilters() { for (var k in filterMap) { if (filterMap[k]) return true; } return false; }
    function passesAll(tr) {
        for (var k in filterMap) {
            var fn = filterMap[k];
            if (!fn) continue;
            try { if (!fn(tr)) return false; } catch (e) {}
        }
        return true;
    }
    function viewRows() { return filteredRows || allRowsArray; }
    function recomputeFilter() {
        filteredRows = hasFilters() ? allRowsArray.filter(passesAll) : null;
    }

    // (Re)build the offsets prefix-sum from the current heights[] over viewRows().
    function rebuildOffsets() {
        const rows = viewRows();
        const n = rows.length;
        if (heights.length !== n) {
            // resize heights to match current view, seeding new entries with the estimate
            const next = new Array(n);
            for (let i = 0; i < n; i++) next[i] = heights[i] != null ? heights[i] : EST_ROW_HEIGHT;
            heights = next;
        }
        offsets = new Array(n + 1);
        offsets[0] = 0;
        for (let i = 0; i < n; i++) offsets[i + 1] = offsets[i] + (heights[i] || EST_ROW_HEIGHT);
    }

    function totalHeight() {
        return (offsets.length ? offsets[offsets.length - 1] : 0) + BOTTOM_PAD;
    }

    function setSpacerHeight() {
        const spacer = document.getElementById('virtual-scroll-spacer');
        if (spacer) spacer.style.height = (totalHeight() + (eaTopGap || 0)) + 'px';
    }

    // Binary search: first row whose cumulative BOTTOM exceeds y.
    function rowAtOffset(y) {
        let lo = 0, hi = offsets.length - 1;
        if (y <= 0) return 0;
        while (lo < hi) {
            const mid = (lo + hi) >> 1;
            if (offsets[mid + 1] <= y) lo = mid + 1; else hi = mid;
        }
        return lo;
    }

    function initVirtualScroll() {
        const template = document.getElementById('raw-excel-template');
        const viewport = document.getElementById('virtual-scroll-viewport');
        const tbody = document.getElementById('virtual-scroll-tbody');
        const spacer = document.getElementById('virtual-scroll-spacer');
        const headerRow = document.getElementById('virtual-scroll-header-row');

        if (!template || !viewport || !tbody || !headerRow || !spacer) return;

        const tempDiv = document.createElement('tbody');
        tempDiv.innerHTML = template.innerHTML;

        const allRows = tempDiv.querySelectorAll('tr.row');
        if (allRows.length === 0) return;

        // Build the header from the first row's cells.
        const firstRow = allRows[0];
        const cells = firstRow.querySelectorAll('td');
        totalColumnsCount = cells.length;
        let headerHtml = '';
        cells.forEach((cell, idx) => {
            const colName = cell.getAttribute('data-col-name') || '';
            const displayName = cell.getAttribute('data-display-name') || colName;
            headerHtml += `<th id="col-${idx}" class="col" data-col-name="${colName}" data-display-name="${displayName}">${displayName}</th>`;
        });
        headerRow.innerHTML = headerHtml;

        allRowsArray = Array.from(allRows).map((tr, index) => {
            tr.setAttribute('data-virtual-index', index);
            // Stripe by stable data index — never by DOM nth-child (virtual
            // scroll + top spacer would flip odd/even on every scroll).
            tr.classList.toggle('vs-row-odd', index % 2 === 0);
            tr.classList.toggle('vs-row-even', index % 2 === 1);
            return tr;
        });

        heights = new Array(allRowsArray.length).fill(EST_ROW_HEIGHT);
        rebuildOffsets();
        setSpacerHeight();

        viewport.addEventListener('scroll', () => {
            if (!isTicking) {
                window.requestAnimationFrame(() => {
                    executeRender(viewport.scrollTop);
                    isTicking = false;
                });
                isTicking = true;
            }
        }, { passive: true });

        executeRender(0);
    }

    // After rows are placed in the DOM, read their real heights. If any differ
    // from the cache, update the cache + offsets + spacer and keep the row under
    // the viewport's top edge stable (so the view doesn't jump when a tall row
    // above gets measured).
    function measureAndReconcile(viewport, renderedStart, renderedEnd) {
        const rows = viewRows();
        let changed = false;
        for (let i = renderedStart; i < renderedEnd; i++) {
            const tr = rows[i];
            if (!tr || !tr.offsetParent && tr.offsetHeight === 0) continue;
            const h = tr.offsetHeight || EST_ROW_HEIGHT;
            if (Math.abs((heights[i] || 0) - h) > 0.5) {
                heights[i] = h;
                changed = true;
            }
        }
        if (changed) {
            const prevTop = viewport.scrollTop;
            // anchor row = first visible row before re-layout
            const anchorIdx = rowAtOffset(prevTop);
            const anchorDelta = prevTop - offsets[anchorIdx];
            rebuildOffsets();
            setSpacerHeight();
            // restore scroll so the anchor row stays put
            const newTop = offsets[anchorIdx] + anchorDelta;
            if (Math.abs(newTop - prevTop) > 0.5) viewport.scrollTop = newTop;
        }
        return changed;
    }

    function applyViewStripe(tr, viewIndex) {
        // Visible-list parity so filtered views still alternate cleanly while
        // staying stable across scroll (same viewIndex → same stripe forever).
        if (!tr) return;
        tr.classList.toggle('vs-row-odd', viewIndex % 2 === 0);
        tr.classList.toggle('vs-row-even', viewIndex % 2 === 1);
    }

    function appendPadRow(fragment, padPx) {
        const spacerRow = document.createElement('tr');
        spacerRow.className = 'vs-pad-row';
        spacerRow.style.height = padPx + 'px';
        spacerRow.style.pointerEvents = 'none';
        const spacerCell = document.createElement('td');
        spacerCell.colSpan = totalColumnsCount;
        spacerCell.style.padding = '0';
        spacerCell.style.border = 'none';
        spacerCell.style.background = 'transparent';
        spacerRow.appendChild(spacerCell);
        fragment.appendChild(spacerRow);
    }

        function executeRender(scrollTop) {
        const viewport = document.getElementById('virtual-scroll-viewport');
        const tbody = document.getElementById('virtual-scroll-tbody');
        if (!viewport || !tbody) return;

        // Keep pad-row colspan in sync when columns are injected later
        // (e.g. SERVICE PRICE after Attach) — a stale count collapses the
        // new column under table-layout:fixed.
        if (allRowsArray.length) {
            const n = allRowsArray[0].querySelectorAll('td').length;
            if (n > 0) totalColumnsCount = n;
        }

        const rows = viewRows();
        const n = rows.length;
        if (offsets.length !== n + 1) { rebuildOffsets(); setSpacerHeight(); }

        const viewportHeight = viewport.clientHeight;
        const top = scrollTop - BUFFER_PX;
        const bottom = scrollTop + viewportHeight + BUFFER_PX;

        let startIndex = rowAtOffset(Math.max(0, top));
        let endIndex = rowAtOffset(Math.max(0, bottom)) + 1;
        if (startIndex < 0) startIndex = 0;
        if (endIndex > n) endIndex = n;
        if (startIndex > n) startIndex = n;

        // Top spacer height = cumulative top of the first rendered row
        // (+ optional EA gap so the floating panel can sit under the header).
        const topPad = (offsets[startIndex] || 0) + (eaTopGap || 0);

        tbody.innerHTML = '';
        const fragment = document.createDocumentFragment();

        if (topPad > 0) appendPadRow(fragment, topPad);

        for (let i = startIndex; i < endIndex; i++) {
            applyViewStripe(rows[i], i);
            fragment.appendChild(rows[i]);
        }
        tbody.appendChild(fragment);

        const table = document.getElementById('virtual-scroll-table');
        if (window.enableResizableColumns && table.dataset.resizableReady !== '1') {
            window.enableResizableColumns(table);
        }

        // Auto-size any textareas in the just-rendered rows (they can only be
        // measured once attached to the DOM). Consumers register via
        // VirtualScrollEngine.onRender(fn).
        runRenderHooks(startIndex, endIndex);

        // Measure real heights now that the rows are in the DOM, then reconcile.
        // If heights changed, re-render once more so the spacer/window match.
        const didChange = measureAndReconcile(viewport, startIndex, endIndex);
        if (didChange) {
            // Recompute the window against the corrected offsets (cheap, no loop).
            const st = viewport.scrollTop;
            const t2 = st - BUFFER_PX, b2 = st + viewport.clientHeight + BUFFER_PX;
            let s2 = rowAtOffset(Math.max(0, t2));
            let e2 = rowAtOffset(Math.max(0, b2)) + 1;
            if (s2 < 0) s2 = 0; if (e2 > n) e2 = n; if (s2 > n) s2 = n;
            const pad2 = offsets[s2] || 0;
            tbody.innerHTML = '';
            const frag2 = document.createDocumentFragment();
            if (pad2 > 0) appendPadRow(frag2, pad2);
            for (let i = s2; i < e2; i++) {
                applyViewStripe(rows[i], i);
                frag2.appendChild(rows[i]);
            }
            tbody.appendChild(frag2);
            runRenderHooks(s2, e2);
            // Re-measure after sizing in the second pass (no further re-render to
            // avoid loops; offsets are close enough now).
            measureAndReconcile(viewport, s2, e2);
        }
    }

    // ---- render hooks ----
    let renderHooks = [];
    function runRenderHooks(start, end) {
        if (!renderHooks.length) return;
        const rows = viewRows();
        for (let h = 0; h < renderHooks.length; h++) {
            try { renderHooks[h](rows, start, end); } catch (e) {}
        }
    }

    // Public: a row grew/shrank (e.g. a textarea wrapped to another line). We
    // must NOT re-render the tbody here — the row is already in the DOM and the
    // user may be typing in it; clearing innerHTML would steal focus mid-type.
    // We only update the height cache, the prefix-sum offsets and the spacer so
    // the scrollbar length stays exact. The already-rendered rows keep their
    // natural flow, so the growth is shown immediately without any reflow of the
    // virtual window.
    let pendingMeasure = null;
    function remeasureRow(tr) {
        if (!tr) return;
        const rows = viewRows();
        const idx = rows.indexOf(tr);
        if (idx < 0) return;
        const h = tr.offsetHeight || EST_ROW_HEIGHT;
        if (Math.abs((heights[idx] || 0) - h) > 0.5) {
            heights[idx] = h;
            // Coalesce rapid keystrokes into a single offset rebuild per frame.
            if (pendingMeasure) return;
            pendingMeasure = window.requestAnimationFrame(function () {
                pendingMeasure = null;
                rebuildOffsets();
                setSpacerHeight();
                // NOTE: deliberately no executeRender() — keep DOM + focus intact.
            });
        }
    }

    window.VirtualScrollEngine = {
        init: initVirtualScroll,
        refresh: () => {
            recomputeFilter();
            rebuildOffsets();
            setSpacerHeight();
            const viewport = document.getElementById('virtual-scroll-viewport');
            if (viewport) executeRender(viewport.scrollTop);
        },
        getRows: () => allRowsArray,
        getVisibleRows: () => viewRows(),
        remeasureRow: remeasureRow,
        onRender: (fn) => { if (typeof fn === 'function') renderHooks.push(fn); },
        setEaTopGap: (px) => {
            const next = Math.max(0, Number(px) || 0);
            if (Math.abs(next - eaTopGap) < 0.5) return;
            eaTopGap = next;
            setSpacerHeight();
            const vp = document.getElementById('virtual-scroll-viewport');
            if (vp) executeRender(vp.scrollTop);
        },
        getEaTopGap: () => eaTopGap,
        setFilter: (fn) => {
            if (typeof fn === 'function') filterMap['_main'] = fn; else delete filterMap['_main'];
            recomputeFilter(); rebuildOffsets(); setSpacerHeight();
            const vp = document.getElementById('virtual-scroll-viewport');
            if (vp) { vp.scrollTop = 0; executeRender(0); }
        },
        addFilter: (key, fn) => {
            if (typeof fn === 'function') filterMap[key] = fn; else delete filterMap[key];
            recomputeFilter(); rebuildOffsets(); setSpacerHeight();
            const vp = document.getElementById('virtual-scroll-viewport');
            if (vp) { vp.scrollTop = 0; executeRender(0); }
        },
        removeFilter: (key) => {
            delete filterMap[key];
            recomputeFilter(); rebuildOffsets(); setSpacerHeight();
            const vp = document.getElementById('virtual-scroll-viewport');
            if (vp) { vp.scrollTop = 0; executeRender(0); }
        }
    };

    document.addEventListener('DOMContentLoaded', initVirtualScroll);
})(window, document);
