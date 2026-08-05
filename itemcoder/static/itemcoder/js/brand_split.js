/* brand_split.js — BRAND "Old / New" split (mirrors remark_split.js).
 *
 * TO — when Supply changed BRAND (data-brand-pending="1"):
 *     Prev  : <Technical's previous brand>   (read-only)
 *     New   : [ Supply's brand in textarea ]
 *   Reject  — paste Prev into New, unlock, run coding
 *   Confirm — keep New, unlock, run coding
 *   Brand New is locked while pending (remark stays editable).
 *
 * PI — Old/New UI when Technical's confirmed brand is being absorbed
 *   (data-brand-split="1"); no Reject/Confirm buttons.
 */
(function (window, document) {
  'use strict';

  var BRAND = 'BRAND';

  function isTO() {
    return ((window.FT_TOOL_SAVE && window.FT_TOOL_SAVE.kind) || '').toUpperCase() === 'TO';
  }

  function isPI() {
    return ((window.FT_TOOL_SAVE && window.FT_TOOL_SAVE.kind) || '').toUpperCase() === 'PI';
  }

  function piBrandOf(tr) {
    var fromAttr = (tr.getAttribute('data-brand-pf-text') || '');
    if (fromAttr !== '') return fromAttr;
    return '';
  }

  function prevBrandOf(tr, cell) {
    var prev = tr.getAttribute('data-prev-brand');
    if (prev != null && prev !== '') return prev;
    if (cell) {
      var ov = cell.querySelector('.brd-old-val');
      if (ov) return (ov.textContent || '');
    }
    return '';
  }

  function rowNeedsBrandResolve(tr) {
    if (!tr || !isTO()) return false;
    // User already Confirm/Reject'd this session — never re-open on scroll.
    if (tr.getAttribute('data-brand-resolved') === '1') return false;
    // Explicit pending from server / prior decorate.
    if (tr.getAttribute('data-brand-pending') === '1') return true;
    // After Confirm/Reject we clear data-brand-pf-text. Without that snapshot,
    // do NOT re-open the resolve UI on scroll (unlike comparing empty pf vs ack).
    if (!tr.hasAttribute('data-brand-pf-text')) return false;
    var pf = piBrandOf(tr);
    var ack = tr.getAttribute('data-brand-ack');
    if (ack == null) ack = '';
    return significantDiff(pf, ack) && tr.getAttribute('data-brand-split') === '1';
  }

  function significantDiff(a, b) {
    function ns(s) { return String(s || '').replace(/\s+/g, ''); }
    return ns(a) !== ns(b);
  }

  function setBrandLocked(tr, locked) {
    var cell = tr.querySelector('td[data-col-name="' + BRAND + '"]');
    if (!cell) return;
    var ta = cell.querySelector('textarea');
    if (ta) {
      ta.readOnly = !!locked;
      ta.disabled = !!locked;
    }
    if (locked) cell.setAttribute('data-locked', '1');
    else cell.removeAttribute('data-locked');
  }

  function clearCode(tr) {
    var codeCell = tr.querySelector('td[data-col-name="کد"]');
    if (codeCell) codeCell.textContent = '';
  }

  function triggerCoding(tr) {
    if (!tr) return;
    var remarkTa = tr.querySelector('td[data-col-name="ریمارک"] textarea');
    var revTa = tr.querySelector('td[data-col-name="اصلاحیه"] textarea');
    var remark = remarkTa ? (remarkTa.value || '').trim() : '';
    var revision = revTa ? (revTa.value || '').trim() : '';

    if (!window.RowProcessor || !window.RowProcessor.sendToProcessor) {
      if (window.FT_TABLE_UI && typeof window.FT_TABLE_UI.submitRow === 'function') {
        window.FT_TABLE_UI.submitRow(tr);
      }
      return;
    }
    var descCell = tr.querySelector('td[data-col-name="description"]');
    var text = ((descCell ? descCell.textContent : '') || '').trim();
    var group = tr.getAttribute('data-group') || '';
    var type = tr.getAttribute('data-type') || '';
    window.RowProcessor.sendToProcessor(text, group, type, tr, remark, revision, 'full');
  }

  function resolvePending(tr, mode) {
    if (!tr || !rowNeedsBrandResolve(tr)) return;
    var cell = tr.querySelector('td[data-col-name="' + BRAND + '"]');
    var ta = cell ? cell.querySelector('textarea') : null;
    var piBrand = piBrandOf(tr);

    setBrandLocked(tr, false);

    var newBrand = '';
    if (mode === 'confirm') {
      newBrand = ta ? String(ta.value || '') : piBrand;
    } else {
      newBrand = prevBrandOf(tr, cell);
    }
    if (ta) {
      ta.value = newBrand;
      try { ta.dispatchEvent(new Event('input', { bubbles: true })); } catch (_e) {}
    }

    // Ack the supplier brand that was answered, then drop pending markers so
    // virtual-scroll re-decorate does not bring Reject/Confirm back.
    tr.setAttribute('data-brand-ack', piBrand);
    tr.removeAttribute('data-brand-pending');
    tr.removeAttribute('data-brand-pf-text');
    tr.setAttribute('data-brand-baseline', newBrand);
    // Keep Old/New UI (split) but mark resolved for this session.
    tr.setAttribute('data-brand-resolved', '1');

    if (cell) {
      var actions = cell.querySelector('.brd-pf-actions');
      if (actions) actions.remove();
    }

    triggerCoding(tr);
  }

  function ensureActions(tr, cell) {
    var actions = cell.querySelector('.brd-pf-actions');
    if (!actions) {
      actions = document.createElement('div');
      actions.className = 'brd-pf-actions';
      var rejectBtn = document.createElement('button');
      rejectBtn.type = 'button';
      rejectBtn.className = 'brd-pf-btn brd-pf-reject';
      rejectBtn.textContent = 'Reject';
      rejectBtn.title = 'Copy previous brand into New, then run coding';
      var confirmBtn = document.createElement('button');
      confirmBtn.type = 'button';
      confirmBtn.className = 'brd-pf-btn brd-pf-confirm';
      confirmBtn.textContent = 'Confirm';
      confirmBtn.title = 'Keep New brand, then run coding';
      actions.appendChild(rejectBtn);
      actions.appendChild(confirmBtn);

      var head = cell.querySelector('.brd-new-head');
      if (!head) {
        head = document.createElement('div');
        head.className = 'brd-new-head';
        var newTag = cell.querySelector('.brd-tag-new');
        var ta = cell.querySelector('textarea');
        if (newTag) head.appendChild(newTag);
        else {
          newTag = document.createElement('span');
          newTag.className = 'brd-tag brd-tag-new';
          newTag.textContent = 'New';
          head.appendChild(newTag);
        }
        head.appendChild(actions);
        if (ta) cell.insertBefore(head, ta);
        else cell.appendChild(head);
      } else {
        head.appendChild(actions);
      }
    }
    var rejectBtn = actions.querySelector('.brd-pf-reject');
    var confirmBtn = actions.querySelector('.brd-pf-confirm');
    if (rejectBtn) {
      rejectBtn.onclick = function (e) {
        e.preventDefault();
        e.stopPropagation();
        resolvePending(tr, 'reject');
      };
    }
    if (confirmBtn) {
      confirmBtn.onclick = function (e) {
        e.preventDefault();
        e.stopPropagation();
        resolvePending(tr, 'confirm');
      };
    }
  }

  function decorate(tr) {
    if (!tr) return;
    // Session-resolved rows must never re-open Confirm/Reject on scroll.
    if (tr.getAttribute('data-brand-resolved') === '1') {
      tr.removeAttribute('data-brand-pending');
      tr.removeAttribute('data-brand-pf-text');
    }
    var needsResolve = rowNeedsBrandResolve(tr) && tr.getAttribute('data-brand-resolved') !== '1';
    if (needsResolve) {
      tr.setAttribute('data-brand-pending', '1');
      tr.setAttribute('data-brand-split', '1');
      var pf = piBrandOf(tr);
      if (pf !== '' || tr.hasAttribute('data-brand-pf-text')) {
        if (pf !== '') tr.setAttribute('data-brand-pf-text', pf);
      }
      clearCode(tr);
    }

    if (tr.getAttribute('data-brand-split') !== '1') return;

    var cell = tr.querySelector('td[data-col-name="' + BRAND + '"]');
    if (!cell) return;
    var prev = tr.getAttribute('data-prev-brand');
    if (prev == null) prev = '';
    var ta = cell.querySelector('textarea');

    if (!cell.querySelector('.brd-old')) {
      var old = document.createElement('div');
      old.className = 'brd-old';
      old.title = 'Previous brand (read-only)';
      var lbl = document.createElement('span');
      lbl.className = 'brd-tag';
      lbl.textContent = 'Prev';
      var val = document.createElement('span');
      val.className = 'brd-old-val';
      val.textContent = prev;
      old.appendChild(lbl);
      old.appendChild(val);

      var head = document.createElement('div');
      head.className = 'brd-new-head';
      var newTag = document.createElement('span');
      newTag.className = 'brd-tag brd-tag-new';
      newTag.textContent = 'New';
      head.appendChild(newTag);

      cell.insertBefore(old, cell.firstChild);
      if (ta) cell.insertBefore(head, ta);
      else cell.appendChild(head);
      cell.classList.add('brd-split');
    } else {
      // Keep Prev text in sync if attribute changed.
      var ov = cell.querySelector('.brd-old-val');
      if (ov && prev !== '' && !(ov.textContent || '')) ov.textContent = prev;
    }

    if (needsResolve) {
      ensureActions(tr, cell);
      setBrandLocked(tr, true);
    } else {
      var stale = cell.querySelector('.brd-pf-actions');
      if (stale) stale.remove();
      if (isTO()) setBrandLocked(tr, false);
    }
  }

  function rows() {
    var eng = window.VirtualScrollEngine;
    return (eng && eng.getRows) ? eng.getRows() : [];
  }

  function decorateAll() {
    rows().forEach(decorate);
  }

  function decorateTemplate() {
    var tpl = document.getElementById('raw-excel-template');
    if (!tpl || !tpl.content) return;
    tpl.content.querySelectorAll('tr.row').forEach(decorate);
  }

  function boot() {
    if (!isTO() && !isPI()) return;
    decorateTemplate();
    decorateAll();
    var eng = window.VirtualScrollEngine;
    if (eng && eng.onRender) {
      eng.onRender(function (allRows, start, end) {
        for (var i = start; i < end; i++) decorate(allRows[i]);
      });
      if (eng.refresh) eng.refresh();
    }
  }

  document.addEventListener('DOMContentLoaded', function () {
    setTimeout(boot, 0);
    setTimeout(boot, 60);
    setTimeout(boot, 250);
  });
})(window, document);
