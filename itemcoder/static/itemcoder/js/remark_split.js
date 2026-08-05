/* remark_split.js — REMARK "Old / New" split (point 5).
 *
 * Once a proforma-remark round has happened for a row (server sets
 * data-remark-split="1" and data-prev-remark="<own last committed remark>"),
 * the REMARK cell shows:
 *     Prev  : <old remark>   (read-only — the side's own last committed remark)
 *     New   : [ editable textarea ]   (the existing ریمارک textarea)
 *
 * TO only — when the supplier remark is still UNHANDLED (data-pf-pending="1",
 * or a PF remark column value that differs from data-pf-ack), New shows
 * Reject / Confirm, and Remark New + Revision stay locked until one is clicked:
 *   • Reject  — paste Prev into New, unlock, run coding with New + revision
 *   • Confirm — paste PF remark into New, unlock, run coding with New + revision
 *
 * PI — same Old/New UI when a prior remark exists, but NO Reject/Confirm buttons.
 */
(function (window, document) {
  'use strict';

  var REMARK = 'ریمارک';
  var REVISION = 'اصلاحیه';

  function isTO() {
    return ((window.FT_TOOL_SAVE && window.FT_TOOL_SAVE.kind) || '').toUpperCase() === 'TO';
  }

  function isPI() {
    return ((window.FT_TOOL_SAVE && window.FT_TOOL_SAVE.kind) || '').toUpperCase() === 'PI';
  }

  function pfRemarkOf(tr) {
    var fromAttr = (tr.getAttribute('data-pf-text') || '').trim();
    if (fromAttr) return fromAttr;
    var td = tr.querySelector('td[data-col-name="proforma remark"]');
    return td ? (td.textContent || '').trim() : '';
  }

  function prevRemarkOf(tr, cell) {
    var prev = (tr.getAttribute('data-prev-remark') || '').trim();
    if (prev) return prev;
    if (cell) {
      var ov = cell.querySelector('.rmk-old-val');
      if (ov) return (ov.textContent || '').trim();
    }
    return '';
  }

  function rowNeedsPfResolve(tr) {
    if (!tr || !isTO()) return false;
    if (tr.getAttribute('data-pf-pending') === '1') return true;
    var pf = pfRemarkOf(tr);
    if (!pf) return false;
    var ack = (tr.getAttribute('data-pf-ack') || '').trim();
    return pf !== ack;
  }

  function setRemarkRevisionLocked(tr, locked) {
    [REMARK, REVISION].forEach(function (col) {
      var cell = tr.querySelector('td[data-col-name="' + col + '"]');
      if (!cell) return;
      var ta = cell.querySelector('textarea');
      if (ta) {
        ta.readOnly = !!locked;
        ta.disabled = !!locked;
      }
      if (locked) cell.setAttribute('data-locked', '1');
      else cell.removeAttribute('data-locked');
    });
  }

  function clearCode(tr) {
    var codeCell = tr.querySelector('td[data-col-name="کد"]');
    if (codeCell) codeCell.textContent = '';
  }

  function triggerCoding(tr, remarkOverride, revisionOverride) {
    if (!tr) return;
    var remarkTa = tr.querySelector('td[data-col-name="' + REMARK + '"] textarea');
    var revTa = tr.querySelector('td[data-col-name="' + REVISION + '"] textarea');
    var remark = (remarkOverride != null)
      ? String(remarkOverride)
      : (remarkTa ? (remarkTa.value || '').trim() : '');
    var revision = (revisionOverride != null)
      ? String(revisionOverride)
      : (revTa ? (revTa.value || '').trim() : '');

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
    if (!tr || !rowNeedsPfResolve(tr)) return;
    var pfText = pfRemarkOf(tr);
    var cell = tr.querySelector('td[data-col-name="' + REMARK + '"]');
    var ta = cell ? cell.querySelector('textarea') : null;
    var revTa = tr.querySelector('td[data-col-name="' + REVISION + '"] textarea');

    // Unlock first so New / Revision are writable and their values are reliable.
    setRemarkRevisionLocked(tr, false);

    // Snapshot revision NOW (same value Reject and Confirm must both apply).
    var revisionSnapshot = revTa ? String(revTa.value || '') : '';

    var newRemark = '';
    if (mode === 'confirm') {
      // Confirm: paste the Proforma remark into New.
      newRemark = pfText;
    } else {
      // Reject: paste the previous (Prev) remark into New.
      newRemark = prevRemarkOf(tr, cell);
    }
    if (ta) {
      ta.value = newRemark;
      try { ta.dispatchEvent(new Event('input', { bubbles: true })); } catch (_e) {}
    }

    if (pfText) tr.setAttribute('data-pf-ack', pfText);
    tr.removeAttribute('data-pf-pending');
    tr.removeAttribute('data-pf-text');

    if (cell) {
      var actions = cell.querySelector('.rmk-pf-actions');
      if (actions) actions.remove();
    }

    // Run regex / coding with the filled New remark AND the existing revision.
    triggerCoding(tr, newRemark, revisionSnapshot);
  }

  function ensureActions(tr, cell) {
    var actions = cell.querySelector('.rmk-pf-actions');
    if (!actions) {
      actions = document.createElement('div');
      actions.className = 'rmk-pf-actions';
      var rejectBtn = document.createElement('button');
      rejectBtn.type = 'button';
      rejectBtn.className = 'rmk-pf-btn rmk-pf-reject';
      rejectBtn.textContent = 'Reject';
      rejectBtn.title = 'Copy previous remark into New, then run coding with revision';
      var confirmBtn = document.createElement('button');
      confirmBtn.type = 'button';
      confirmBtn.className = 'rmk-pf-btn rmk-pf-confirm';
      confirmBtn.textContent = 'Confirm';
      confirmBtn.title = 'Copy PF remark into New, then run coding with revision';
      actions.appendChild(rejectBtn);
      actions.appendChild(confirmBtn);
      var ta = cell.querySelector('textarea');
      var newTag = cell.querySelector('.rmk-tag-new');
      if (newTag && newTag.parentNode === cell) {
        if (newTag.nextSibling) cell.insertBefore(actions, newTag.nextSibling);
        else cell.appendChild(actions);
      } else if (ta) {
        cell.insertBefore(actions, ta);
      } else {
        cell.appendChild(actions);
      }
    }
    // Always (re)bind — virtual-scroll clones via innerHTML drop listeners.
    var rejectBtn = actions.querySelector('.rmk-pf-reject');
    var confirmBtn = actions.querySelector('.rmk-pf-confirm');
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
    var needsResolve = rowNeedsPfResolve(tr);
    if (needsResolve) {
      tr.setAttribute('data-pf-pending', '1');
      tr.setAttribute('data-remark-split', '1');
      var pf = pfRemarkOf(tr);
      if (pf) tr.setAttribute('data-pf-text', pf);
      clearCode(tr);
    }

    if (tr.getAttribute('data-remark-split') !== '1') return;

    var cell = tr.querySelector('td[data-col-name="' + REMARK + '"]');
    if (!cell) return;
    var prev = tr.getAttribute('data-prev-remark') || '';
    var ta = cell.querySelector('textarea');

    if (!cell.querySelector('.rmk-old')) {
      var old = document.createElement('div');
      old.className = 'rmk-old';
      old.title = 'Previous remark (read-only)';
      var lbl = document.createElement('span');
      lbl.className = 'rmk-tag';
      lbl.textContent = 'Prev';
      var val = document.createElement('span');
      val.className = 'rmk-old-val';
      val.textContent = prev;
      old.appendChild(lbl);
      old.appendChild(val);

      var newTag = document.createElement('span');
      newTag.className = 'rmk-tag rmk-tag-new';
      newTag.textContent = 'New';

      cell.insertBefore(old, cell.firstChild);
      if (ta) cell.insertBefore(newTag, ta);
      cell.classList.add('rmk-split');
    }

    // Reject/Confirm only on TO for unhandled PF remarks. PI gets Old/New only.
    if (needsResolve) {
      ensureActions(tr, cell);
      setRemarkRevisionLocked(tr, true);
    } else {
      var stale = cell.querySelector('.rmk-pf-actions');
      if (stale) stale.remove();
      if (isTO()) setRemarkRevisionLocked(tr, false);
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
    // TO and PI both get the Old/New remark UI; only TO gets Reject/Confirm.
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
    // VSE also boots on DOMContentLoaded; retry so we decorate after rows exist
    // and after the PF remark column has been injected (TO).
    setTimeout(boot, 0);
    setTimeout(boot, 60);
    setTimeout(boot, 250);
  });
})(window, document);
