/*
 * item_flag.js — Item Code flag affordance.
 * TO: Technical Problem (wrench) — multi-select rows, then one shared reason.
 * PI: Not Suppliable (ban) — toggles immediately.
 *
 * tp-multi-2: pending selection stored on data-tp-pending; kind read live.
 */
(function (window, document) {
  'use strict';

  function toolKind() {
    return String((window.FT_TOOL_SAVE && window.FT_TOOL_SAVE.kind) || '').toUpperCase();
  }
  function isPI() { return toolKind() === 'PI'; }
  function isTO() { return toolKind() === 'TO'; }
  function disabled() {
    return isPI() && String((window.FT_TOOL_SAVE && window.FT_TOOL_SAVE.docKind) || '').toUpperCase() === 'TENDER';
  }

  var ROW_ISSUE = 'row-issue';
  var ROW_UNSUP = 'row-unsuppliable';
  var ATTR_ISSUE = 'data-issue';
  var ATTR_UNSUP = 'data-unsuppliable';
  var ATTR_PENDING = 'data-tp-pending';
  var ICON_WRENCH = 'fa-screwdriver-wrench';
  var ICON_WRENCH_BROKEN = 'fa-screwdriver-wrench-broken';
  var ICON_BAN = 'fa-ban';

  function flagIconHtml(flagged) {
    var ico;
    if (isPI()) ico = ICON_BAN;
    else ico = flagged ? ICON_WRENCH_BROKEN : ICON_WRENCH;
    return '<i class="fa-solid ' + ico + ' ic-flag-ico"></i>';
  }

  function rows() {
    return (window.VirtualScrollEngine && window.VirtualScrollEngine.getRows)
      ? window.VirtualScrollEngine.getRows() : [];
  }

  function itemNoOf(tr) {
    if (!tr) return '';
    var cell = tr.querySelector('td[data-col-name="Item Code"] .ic-num') ||
               tr.querySelector('td[data-col-name="Item Code"]') ||
               tr.querySelector('td[data-col-name="#"]');
    if (!cell) return '';
    var num = cell.querySelector('.ic-num');
    return ((num ? num.textContent : cell.textContent) || '').trim();
  }

  function clientNoOf(tr) {
    if (!tr) return '';
    var cell = tr.querySelector('td[data-col-name="#"]');
    if (!cell) return itemNoOf(tr);
    var t = cell.querySelector('.client-no-text');
    return ((t ? t.textContent : cell.textContent) || '').replace(/[−+\s]/g, '').trim() || itemNoOf(tr);
  }

  function sortNums(nums) {
    return nums.slice().sort(function (a, b) {
      var na = parseInt(String(a).replace(/\D/g, ''), 10);
      var nb = parseInt(String(b).replace(/\D/g, ''), 10);
      if (isFinite(na) && isFinite(nb) && na !== nb) return na - nb;
      return String(a).localeCompare(String(b), undefined, { numeric: true });
    });
  }

  function pendingRows() {
    return rows().filter(function (tr) {
      return tr.getAttribute(ATTR_PENDING) === '1'
        && tr.getAttribute(ATTR_ISSUE) !== '1';
    });
  }

  function formatHashList(trs) {
    var nums = [];
    (trs || []).forEach(function (tr) {
      var n = clientNoOf(tr);
      if (n && nums.indexOf(n) === -1) nums.push(n);
    });
    if (!nums.length) return '—';
    return '#' + sortNums(nums).join(',');
  }

  function setPendingVisual(tr, on) {
    if (!tr) return;
    if (on) tr.setAttribute(ATTR_PENDING, '1');
    else tr.removeAttribute(ATTR_PENDING);
    var box = tr.querySelector('.ic-box');
    if (box) box.classList.toggle('ic-tp-pending', !!on);
  }

  function decorate(tr) {
    if (!tr) return;
    var cell = tr.querySelector('td[data-col-name="Item Code"]');
    if (!cell) return;
    var attr = isPI() ? ATTR_UNSUP : ATTR_ISSUE;
    var rowClass = isPI() ? ROW_UNSUP : ROW_ISSUE;
    var flagged = tr.getAttribute(attr) === '1';
    var deleted = tr.getAttribute('data-deleted') === '1';
    var pending = !isPI() && tr.getAttribute(ATTR_PENDING) === '1' && !flagged;
    var label = isPI() ? 'NOT SUPPLIABLE' : 'Technical Problem';
    var box = cell.querySelector('.ic-box');
    if (box) {
      var flagEl = box.querySelector('.ic-flag');
      if (flagEl) flagEl.innerHTML = flagIconHtml(flagged);
      if (flagged) tr.classList.add(rowClass); else tr.classList.remove(rowClass);
    } else {
      var num = (cell.textContent || '').trim();
      cell.innerHTML = '<span class="ic-box" title="Click to flag: ' + label + '">' +
        '<span class="ic-num">' + num + '</span>' +
        '<span class="ic-flag">' + flagIconHtml(flagged) + '</span></span>';
      if (flagged) tr.classList.add(rowClass);
      box = cell.querySelector('.ic-box');
    }
    if (box) box.classList.toggle('ic-tp-pending', pending);

    var hashCell = tr.querySelector('td[data-col-name="#"]');
    if (hashCell) {
      var txt = hashCell.getAttribute('data-raw-hash') ||
        ((hashCell.querySelector('.client-no-text') || hashCell).textContent || '').replace(/[−+]/g, '').trim();
      hashCell.setAttribute('data-raw-hash', txt);
      var add = tr.getAttribute('data-added') === '1' && !deleted;
      hashCell.innerHTML = '<span class="client-no-text">' + txt + '</span>' +
        (deleted ? '<span class="row-mark row-mark-del" title="Deleted">−</span>' : '') +
        (add ? '<span class="row-mark row-mark-add" title="Added">+</span>' : '');
    }
    if (deleted) {
      tr.classList.add('row-soft-deleted');
      tr.querySelectorAll('input, textarea, select').forEach(function (el) {
        el.disabled = true; el.readOnly = true;
      });
    }
  }

  function decorateTemplate() {
    var tpl = document.getElementById('raw-excel-template');
    if (!tpl || !tpl.content) return;
    tpl.content.querySelectorAll('tr.row').forEach(decorate);
  }

  function decorateAll() {
    rows().forEach(decorate);
  }

  function reasonCard() { return document.getElementById('tp-reason-card'); }
  function reasonList() { return document.getElementById('tp-reason-list'); }
  function reasonInput() { return document.getElementById('tp-reason-input'); }

  function rebuildReasonList() {
    var list = reasonList();
    var card = reasonCard();
    if (!list || !card) return;
    list.innerHTML = '';

    var groups = {};
    var order = [];
    rows().forEach(function (tr) {
      if (tr.getAttribute(ATTR_ISSUE) !== '1') return;
      var reason = (tr.getAttribute('data-issue-reason') || '').trim();
      if (!reason) return;
      if (!groups[reason]) {
        groups[reason] = [];
        order.push(reason);
      }
      groups[reason].push(tr);
    });

    order.forEach(function (reason) {
      var groupTrs = groups[reason];
      var li = document.createElement('li');
      li.className = 'tp-reason-li';
      li.innerHTML =
        '<span class="tp-item"></span>' +
        '<span class="tp-text"></span>' +
        '<button type="button" class="tp-reason-remove" title="Remove this technical problem">' +
          '<i class="fa-solid fa-xmark"></i>' +
        '</button>';
      li.querySelector('.tp-item').textContent = formatHashList(groupTrs);
      li.querySelector('.tp-text').textContent = reason;
      li.querySelector('.tp-reason-remove').addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        groupTrs.slice().forEach(clearIssue);
        syncPendingUI();
      });
      list.appendChild(li);
    });

    var anyConfirmed = order.length > 0;
    var anyPending = pendingRows().length > 0;
    if (!anyConfirmed && !anyPending) {
      card.hidden = true;
      card.style.display = '';
      return;
    }
    card.hidden = false;
    card.style.display = '';
  }

  function stashFinalText(tr) {
    var cell = tr && tr.querySelector('td[data-col-name="Final Arranged Text"]');
    if (!cell) return;
    if (!cell.dataset.issuePrevHtml) {
      var ftcoTa = cell.querySelector('textarea.ftco-desc-textarea, textarea.ftco-self-textarea');
      if (ftcoTa) {
        cell.dataset.issuePrevHtml = ftcoTa.value || '';
        cell.dataset.issueWasFtcoEdit = '1';
      } else {
        cell.dataset.issuePrevHtml = cell.dataset.originalHtml || cell.innerHTML || '';
      }
    }
    if (!cell.dataset.originalHtml) {
      cell.dataset.originalHtml = cell.dataset.issuePrevHtml;
    }
    cell.innerHTML = '';
  }

  function plainFtcoFromHtml(html) {
    var s = String(html || '');
    var low = s.toLowerCase();
    if (low.indexOf('&lt;') !== -1
        && (low.indexOf('span') !== -1 || low.indexOf('bdi') !== -1 || low.indexOf('br') !== -1)) {
      try {
        var taUn = document.createElement('textarea');
        taUn.innerHTML = s;
        s = taUn.value;
      } catch (_e) {
        s = s.replace(/&lt;/gi, '<').replace(/&gt;/gi, '>').replace(/&amp;/gi, '&');
      }
    }
    return s
      .replace(/<br\s*\/?>/gi, ' ')
      .replace(/<[^>]+>/g, '')
      .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
      .replace(/&quot;/g, '"').replace(/&#39;/g, "'")
      .replace(/\s+/g, ' ').trim();
  }

  function restoreFinalText(tr) {
    var cell = tr && tr.querySelector('td[data-col-name="Final Arranged Text"]');
    if (!cell) return;
    if (cell.dataset.issuePrevHtml) {
      var editable = (window.FT_TOOL_SAVE
        && String(window.FT_TOOL_SAVE.kind || '').toUpperCase() === 'TO'
        && window.FT_TOOL_SAVE.requireFtcoCode === false)
        || cell.dataset.issueWasFtcoEdit === '1'
        || cell.dataset.ftcoDescEditable === '1';
      if (editable) {
        cell.innerHTML = '';
        var ta = document.createElement('textarea');
        ta.className = 'cell-input ftco-desc-textarea remark-revision-textarea';
        ta.value = plainFtcoFromHtml(cell.dataset.issuePrevHtml);
        ta.style.minHeight = '38px';
        ta.style.width = '100%';
        cell.appendChild(ta);
        cell.dataset.editable = '1';
        cell.dataset.ftcoDescEditable = '1';
        if ((ta.value || '').trim()) {
          cell.dataset.userEdited = '1';
          delete cell.dataset.originalHtml;
          if (tr) tr.setAttribute('data-ftco-user-edited', '1');
        }
        if (window.RowProcessor && window.RowProcessor.initFtcoDescEditable) {
          window.RowProcessor.initFtcoDescEditable();
        }
      } else {
        cell.innerHTML = cell.dataset.issuePrevHtml;
        cell.dataset.originalHtml = cell.dataset.issuePrevHtml;
      }
    }
    delete cell.dataset.issuePrevHtml;
    delete cell.dataset.issueWasFtcoEdit;
    delete cell.dataset.issueWasSelf;
  }

  function clearReasonError() {
    var err = document.getElementById('tp-reason-error');
    if (err) err.hidden = true;
  }

  function showReasonError(msg) {
    var err = document.getElementById('tp-reason-error');
    if (!err) return;
    err.textContent = msg;
    err.hidden = false;
  }

  function syncPendingUI() {
    var sel = pendingRows();
    var itemEl = document.getElementById('tp-reason-item');
    if (itemEl) itemEl.textContent = formatHashList(sel);
    var hint = document.getElementById('tp-reason-hint');
    if (hint) {
      hint.hidden = sel.length === 0;
      hint.textContent = sel.length
        ? ('Selected ' + sel.length + ' row(s). Write one reason, then Confirm — or click more wrenches to add/remove.')
        : '';
    }
    var card = reasonCard();
    if (card) {
      var anyConfirmed = false;
      rows().forEach(function (tr) {
        if (tr.getAttribute(ATTR_ISSUE) === '1'
            && (tr.getAttribute('data-issue-reason') || '').trim()) {
          anyConfirmed = true;
        }
      });
      if (sel.length || anyConfirmed) {
        card.hidden = false;
        card.style.display = '';
      } else {
        card.hidden = true;
        card.style.display = '';
      }
    }
    rebuildReasonList();
  }

  function clearPending() {
    pendingRows().forEach(function (tr) { setPendingVisual(tr, false); });
    var inp = reasonInput();
    if (inp) inp.value = '';
    clearReasonError();
    syncPendingUI();
  }

  function togglePending(tr) {
    if (!tr || tr.getAttribute('data-deleted') === '1') return;
    if (tr.getAttribute(ATTR_ISSUE) === '1') return;
    var on = tr.getAttribute(ATTR_PENDING) === '1';
    setPendingVisual(tr, !on);
    clearReasonError();
    syncPendingUI();
  }

  function applyIssue(tr, reason) {
    setPendingVisual(tr, false);
    tr.classList.add(ROW_ISSUE);
    tr.setAttribute(ATTR_ISSUE, '1');
    tr.setAttribute('data-issue-reason', reason);
    var box = tr.querySelector('.ic-box .ic-flag');
    if (box) box.innerHTML = flagIconHtml(true);
    var icBox = tr.querySelector('.ic-box');
    if (icBox) icBox.classList.remove('ic-tp-pending');
    stashFinalText(tr);
    clearReasonError();
    document.dispatchEvent(new CustomEvent('ft-flags-changed'));
  }

  function clearIssue(tr) {
    setPendingVisual(tr, false);
    tr.classList.remove(ROW_ISSUE);
    tr.setAttribute(ATTR_ISSUE, '0');
    tr.removeAttribute('data-issue-reason');
    var box = tr.querySelector('.ic-box .ic-flag');
    if (box) box.innerHTML = flagIconHtml(false);
    var icBox = tr.querySelector('.ic-box');
    if (icBox) icBox.classList.remove('ic-tp-pending');
    restoreFinalText(tr);
    document.dispatchEvent(new CustomEvent('ft-flags-changed'));
  }

  function wireReasonCard() {
    if (!isTO()) return;
    var confirmBtn = document.getElementById('tp-reason-confirm');
    var cancelBtn = document.getElementById('tp-reason-cancel');
    if (confirmBtn && confirmBtn.dataset.tpWired !== '1') {
      confirmBtn.dataset.tpWired = '1';
      confirmBtn.addEventListener('click', function () {
        var sel = pendingRows();
        if (!sel.length) {
          showReasonError('Select one or more rows with the wrench first.');
          return;
        }
        var reason = (reasonInput() && reasonInput().value || '').trim();
        if (!reason) {
          showReasonError('Please enter the technical problem detail before confirming.');
          if (reasonInput()) reasonInput().focus();
          return;
        }
        sel.forEach(function (tr) { applyIssue(tr, reason); });
        var inp = reasonInput();
        if (inp) inp.value = '';
        clearReasonError();
        syncPendingUI();
      });
    }
    if (cancelBtn && cancelBtn.dataset.tpWired !== '1') {
      cancelBtn.dataset.tpWired = '1';
      cancelBtn.addEventListener('click', function () { clearPending(); });
    }
  }

  function onTableClick(e) {
    var box = e.target.closest ? e.target.closest('.ic-box') : null;
    if (!box) return;
    var tr = box.closest('tr');
    if (!tr) return;
    if (tr.getAttribute('data-deleted') === '1') return;

    if (isTO()) {
      e.preventDefault();
      e.stopPropagation();
      // Confirmed → remove tag. Otherwise toggle multi-select (cell only).
      if (tr.getAttribute(ATTR_ISSUE) === '1') {
        clearIssue(tr);
        syncPendingUI();
        return;
      }
      togglePending(tr);
      return;
    }

    if (!isPI()) return;

    var on = tr.classList.toggle(ROW_UNSUP);
    tr.setAttribute(ATTR_UNSUP, on ? '1' : '0');
    var flagEl = box.querySelector('.ic-flag');
    if (flagEl) flagEl.innerHTML = flagIconHtml(on);
    document.dispatchEvent(new CustomEvent('ft-flags-changed'));
    document.dispatchEvent(new CustomEvent('ft-calc-refreshed'));
    if (typeof window.PIRecomputeGrandOnly === 'function') {
      try { window.PIRecomputeGrandOnly(); } catch (_e) {}
    }
  }

  function wire() {
    // Document-level capture so virtual-scroll re-renders never drop the handler.
    if (document.documentElement.dataset.icFlagWired === '1') return;
    document.documentElement.dataset.icFlagWired = '1';
    document.addEventListener('click', onTableClick, true);

    rows().forEach(function (tr) {
      var attr = isPI() ? ATTR_UNSUP : ATTR_ISSUE;
      var rowClass = isPI() ? ROW_UNSUP : ROW_ISSUE;
      if (tr.getAttribute(attr) === '1') {
        tr.classList.add(rowClass);
        if (isTO()) stashFinalText(tr);
      }
    });
  }

  function boot() {
    if (disabled()) return;
    decorateTemplate();
    decorateAll();
    wire();
    wireReasonCard();
    rebuildReasonList();
    if (window.VirtualScrollEngine && window.VirtualScrollEngine.onRender) {
      window.VirtualScrollEngine.onRender(function (allRows, start, end) {
        for (var i = start; i < end; i++) decorate(allRows[i]);
      });
      if (window.VirtualScrollEngine.render) window.VirtualScrollEngine.render();
      else if (window.VirtualScrollEngine.refresh) window.VirtualScrollEngine.refresh();
    }
  }

  // Load AFTER virtual_scroll_engine when possible; still tolerate either order.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      // Defer one tick so VirtualScrollEngine.init (also on DOMContentLoaded) finishes first
      // when this file is listed before the engine in the template.
      setTimeout(boot, 0);
    });
  } else {
    setTimeout(boot, 0);
  }
})(window, document);
