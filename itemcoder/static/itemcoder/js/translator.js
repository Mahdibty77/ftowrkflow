/*
 * translator.js
 * ------------------------------------------------------------
 * Responsibility:
 * - Translate only the Final Arranged Text column seprator English and Persian.
 * - Read translations from data_translation.json using the row Group/Type.
 * - Always translate from the original server-generated English HTML, never from
 *   the currently displayed translated text. This prevents stale translations
 *   such as "لوله" staying on screen after JSON changes to "لولبه".
 * - Preserve backend highlight spans/colors while translating text nodes only.
 */
(function (window, document) {
    'use strict';

    document.addEventListener('DOMContentLoaded', function () {
        const langSwitch = document.querySelector('.lang-switch');
        if (!langSwitch) {
            console.error('Language switch element not found.');
            return;
        }

        const jsonUrl = langSwitch.getAttribute('data-json-url');
        let translationData = null;
        let translationPromise = null;

        /**
         * Load translation JSON.
         *
         * cache: 'no-store' keeps development/admin edits visible after refresh and
         * avoids browser static-file caching hiding JSON changes.
         */
        function loadTranslationJSON(forceReload = false) {
            if (!forceReload && translationData) return Promise.resolve(translationData);
            if (!forceReload && translationPromise) return translationPromise;

            const separator = jsonUrl.includes('?') ? '&' : '?';
            const url = `${jsonUrl}${separator}v=${Date.now()}`;

            translationPromise = fetch(url, { cache: 'no-store' })
                .then(response => {
                    if (!response.ok) throw new Error('HTTP ' + response.status);
                    return response.text();
                })
                .then(text => {
                    if (!text.trim()) throw new Error('Translation JSON file is empty.');
                    translationData = JSON.parse(text);
                    return translationData;
                })
                .catch(err => {
                    translationPromise = null;
                    console.error('Error loading translation JSON:', err);
                    throw err;
                });

            return translationPromise;
        }

        /**
         * Store the server-generated English HTML for a Final Arranged Text cell.
         *
         * The first stored value is used as the stable source for all future
         * translations. When row_processor receives a new backend response, it can
         * call window.TranslationManager.setOriginalHtml(cell, html) to refresh it.
         */
        function ensureOriginalHtml(targetCell) {
            if (!targetCell) return '';
            if (!targetCell.dataset.originalHtml) {
                targetCell.dataset.originalHtml = targetCell.innerHTML;
            }
            return targetCell.dataset.originalHtml;
        }

        /**
         * Explicitly refresh the English source HTML for one cell.
         * Used after AJAX updates one row's Final Arranged Text.
         */
        function setOriginalHtml(targetCell, html) {
            if (!targetCell) return;
            targetCell.dataset.originalHtml = html || '';
        }

        /**
         * Read the current Group and Type values from a table row.
         */
        function getRowGroupType(row) {
            const groupCell = row.querySelector('td[data-col-name="Group"]');
            const typeCell = row.querySelector('td[data-col-name="Type"]');

            return {
                // Prefer select.value only — never textContent of a <select>
                // (that concatenates every option label into one fake group).
                group: (groupCell?.querySelector('select')
                    ? (groupCell.querySelector('select').value || '').trim()
                    : (groupCell?.textContent || '').trim()) || '',
                type: (typeCell?.querySelector('select')
                    ? (typeCell.querySelector('select').value || '').trim()
                    : (typeCell?.textContent || '').trim()) || ''
            };
        }

        /**
         * Build the translation dictionary for one row.
         *
         * Primary source: data.group[group][type]
         * Fallback: merge all dictionaries under data.group[group] so group-level
         * words can still translate if the row type is different or empty.
         */
        function getTranslationMap(groupValue, typeValue) {
            const groupObj = translationData?.group?.[groupValue];
            if (!groupObj) return null;

            const merged = {};
            Object.values(groupObj).forEach(value => {
                if (value && typeof value === 'object' && !Array.isArray(value)) {
                    Object.assign(merged, value);
                }
            });

            if (groupObj[typeValue] && typeof groupObj[typeValue] === 'object') {
                Object.assign(merged, groupObj[typeValue]);
            }

            return Object.keys(merged).length ? merged : null;
        }

        /**
         * Sort longer keys first so "Seamless" is translated before shorter parts,
         * and technical tokens like "Gr." are handled safely inside "Gr.X42".
         */
        function sortedEntries(map) {
            return Object.entries(map).sort((a, b) => b[0].length - a[0].length);
        }

        /**
         * Apply a dictionary to text nodes only, preserving all highlight spans.
         */
        function translateHtmlTextNodes(targetCell, typeObj, lang) {
            const walker = document.createTreeWalker(targetCell, NodeFilter.SHOW_TEXT, null);
            const textNodes = [];
            while (walker.nextNode()) textNodes.push(walker.currentNode);

            const entries = sortedEntries(typeObj);

            textNodes.forEach(node => {
                let text = node.nodeValue;
                entries.forEach(([key, val]) => {
                    const from = lang === 'fa' ? key : val;
                    const to = lang === 'fa' ? val : key;
                    if (from && to && text.includes(from)) {
                        text = text.split(from).join(to);
                    }
                });
                node.nodeValue = text;
            });
        }


        /**
         * Return true when a text fragment contains Persian/Arabic letters.
         */
        function hasPersianText(text) {
            return /[\u0600-\u06FF]/.test(text || '');
        }

        /**
         * Apply Persian-specific token fixes to text nodes only.
         *
         * Important:
         * - Generic labels such as sch/thk must stay in normal Persian order:
         *   "رده: 5s" and "ضخامت(میلیمتر) : 1.65".
         * - Grade is the only intentionally reversed technical label because the
         *   requested Persian display is "TP316L : گرید" inside parentheses.
         * - Inch values are stored as "1 for Persian view so browser BiDi does
         *   not visually flip them back to an incorrect order.
         */
        function normalizePersianTextNodes(targetCell) {
            const walker = document.createTreeWalker(targetCell, NodeFilter.SHOW_TEXT, null);
            const textNodes = [];
            while (walker.nextNode()) textNodes.push(walker.currentNode);

            textNodes.forEach(node => {
                let text = node.nodeValue;

                // 1" -> "1 for Persian view only.
                text = text.replace(/(\d+(?:\.\d+)?)"/g, '"$1');

                // Keep translated thickness labels compact: ضخامت (میلیمتر) -> ضخامت(میلیمتر)
                text = text.replace(/([\u0600-\u06FF]+)\s+\(([^)]*[\u0600-\u06FF][^)]*)\)/g, '$1($2)');

                // Normalize label/value spacing for Persian labels.
                text = text.replace(/(رده)\s*:\s*([^,]+)/g, '$1: $2');
                text = text.replace(/(ضخامت\([^)]*\))\s*:\s*([^,]+)/g, '$1 : $2');

                // Only grade is displayed as "value : گرید".
                text = text.replace(/گرید\s*:\s*([A-Za-z0-9.\/_-]+)/g, '$1 : گرید');

                node.nodeValue = text;
            });
        }

        /**
         * Escape text-node content before rebuilding HTML fragments.
         */
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        /**
         * Serialize a DOM node back to HTML without losing highlight spans.
         */
        function serializeNode(node) {
            if (node.nodeType === Node.TEXT_NODE) return escapeHtml(node.nodeValue || '');
            if (node.outerHTML) return node.outerHTML;
            const div = document.createElement('div');
            div.appendChild(node.cloneNode(true));
            return div.innerHTML;
        }

        /**
         * Detect the separator that the backend used in Final Arranged Text.
         *
         * The Persian view must respect final_arrange.json.  Therefore, if the
         * English source uses comma, Persian uses comma; if the English source
         * uses spaces, Persian also uses spaces.  We only normalize comma spacing
         * for readability; we do not force comma when the backend did not use it.
         */
        function detectOriginalSeparator(originalHtml) {
            const tmp = document.createElement('div');
            tmp.innerHTML = originalHtml || '';
            const text = tmp.textContent || '';
            return text.includes(',') ? ' , ' : ' ';
        }

        /**
         * Split translated Final Arranged HTML into logical feature parts.
         *
         * This function is careful with custom templates from final_arrange.json:
         * - A pure comma or pure whitespace text node is a feature boundary.
         * - Glue text such as " / (" or ")" stays attached to the neighboring
         *   feature, so "material / (grade_material)" remains one part.
         * - Highlight spans are preserved exactly as generated by the backend.
         */
        function splitLogicalHtmlParts(targetCell) {
            const parts = [];
            let current = [];

            function currentText() {
                return current.join('').replace(/<[^>]*>/g, '').trim();
            }

            function pushPart() {
                if (currentText()) {
                    parts.push(current.join('').trim());
                }
                current = [];
            }

            function addHtml(html) {
                if (html !== undefined && html !== null && String(html).length) {
                    current.push(String(html));
                }
            }

            function splitPlainText(text) {
                if (!text) return;

                // Comma is always a hard feature separator.
                if (text.includes(',')) {
                    const chunks = text.split(/(,)/);
                    chunks.forEach(chunk => {
                        if (!chunk) return;
                        if (chunk === ',') {
                            pushPart();
                        } else {
                            splitPlainText(chunk);
                        }
                    });
                    return;
                }

                const trimmed = text.trim();
                if (!trimmed) {
                    // Pure whitespace seprator backend feature spans means boundary.
                    pushPart();
                    return;
                }

                // Template glue should not create a feature boundary.
                const glueLike = /^[\s]*(?:[\/_(\[\{<]|\)|\]|\}|>)/.test(text) || /(?:[\/_(\[\{<])\s*$/.test(text);
                if (glueLike) {
                    addHtml(escapeHtml(text));
                    return;
                }

                // Normal text after an existing part means a boundary first when
                // the backend separator is whitespace.
                if (/^\s/.test(text) && currentText()) {
                    pushPart();
                }
                addHtml(escapeHtml(trimmed));
                if (/\s$/.test(text)) {
                    pushPart();
                }
            }

            Array.from(targetCell.childNodes).forEach(node => {
                if (node.nodeType === Node.TEXT_NODE) {
                    splitPlainText(node.nodeValue || '');
                } else {
                    addHtml(serializeNode(node));
                }
            });

            pushPart();
            return parts;
        }

        /**
         * Return true when a final-arrange part is a physical feature such as
         * schedule or thickness. These must stay in original physical order.
         */
        function isPhysicalPart(html) {
            const text = html.replace(/<[^>]*>/g, '');
            return /(\bsch\b|رده\s*:|:\s*رده|\bthk\s*\(|ضخامت\s*\()/i.test(text);
        }

        /**
         * Reorder parts for Persian display in a stable way.
         *
         * Source order example:
         * pipe, size, production, material type, material/grade, standard, sch, thk
         *
         * Persian display:
         * sch, thk, standard, material/grade, pipe, size, production, material type
         *
         * Physical items keep their own original order, while the remaining
         * technical tail is reversed before the first descriptive block.
         */
        function reorderPartsForPersian(parts) {
            if (!Array.isArray(parts) || parts.length <= 4) return parts;
            const firstDescriptiveBlock = parts.slice(0, 4);
            const technicalTail = parts.slice(4);
            const physicalParts = technicalTail.filter(isPhysicalPart);
            const nonPhysicalTail = technicalTail.filter(part => !isPhysicalPart(part)).reverse();
            return physicalParts.concat(nonPhysicalTail, firstDescriptiveBlock);
        }

        /**
         * Fix technical Persian label/value fragments before final rendering.
         *
         * Required display rules:
         * - Grade is shown as:      TP316L : گرید
         * - Schedule is shown as:   5s : رده
         * - Thickness stays as:     ضخامت(میلیمتر) : 1.65
         * - Inch sizes are shown as "1 so browser BiDi does not flip them.
         */
        function normalizePersianHtmlString(html) {
            let out = html || '';

            // Compact translated parenthesized units: ضخامت (میلیمتر) -> ضخامت(میلیمتر)
            out = out.replace(/([\u0600-\u06FF]+)\s+\(([^)]*[\u0600-\u06FF][^)]*)\)/g, '$1($2)');

            // Grade label/value: گرید: TP316L -> TP316L : گرید
            out = out.replace(/گرید\s*:\s*([A-Za-z0-9.\/_-]+)/g, '$1 : گرید');

            // Schedule label/value: رده: 5s -> 5s : رده
            out = out.replace(/رده\s*:\s*([A-Za-z0-9.\/_-]+)/g, '$1 : رده');

            // Thickness label/value stays label first.
            out = out.replace(/ضخامت\s*\(([^)]*)\)\s*:\s*/g, 'ضخامت($1) : ');

            return out.replace(/[ \t]{2,}/g, ' ').trim();
        }

        /**
         * Format the translated final-arrange text for Persian display.
         *
         * Strategy (kept deliberately simple to avoid the previous jumbling):
         * - Keep the parts in their ORIGINAL order (do NOT reorder features).
         * - Wrap every logical part in <bdi> so a Latin/technical token such as
         *   API 5L GR.B or 1/2" keeps its own internal left-to-right order while
         *   the whole comma list flows right-to-left.
         * - Join with the same separator the backend used (comma vs space).
         */
        function formatPersianDisplayText(targetCell, originalHtml) {
            const parts = splitLogicalHtmlParts(targetCell)
                .map(part => normalizePersianHtmlString(part))
                .filter(part => part && part.replace(/<[^>]*>/g, '').trim().length);

            if (!parts.length) return;

            const separator = detectOriginalSeparator(originalHtml);
            targetCell.innerHTML = parts
                .map(part => '<bdi>' + part + '</bdi>')
                .join(separator);
        }

        /**
         * Keep mixed Persian/technical text stable.
         *
         * We keep direction LTR so technical values like 1" and standards do not
         * flip visually, while aligning right for Persian readability.
         */
        function applyLanguageDirection(targetCell, lang) {
            if (lang === 'fa') {
                // RTL flow for the whole list; each <bdi> part keeps its own
                // internal direction so Latin/technical tokens stay correct.
                targetCell.style.direction = 'rtl';
                targetCell.style.unicodeBidi = 'isolate';
                targetCell.style.textAlign = 'right';
            } else {
                targetCell.style.direction = 'ltr';
                targetCell.style.unicodeBidi = 'isolate';
                targetCell.style.textAlign = 'left';
            }
        }

        /**
         * Translate one table row's Final Arranged Text cell.
         */
        function translateSingleRowInternal(row, lang) {
            if (!translationData || !row) return;

            const targetCell = row.querySelector('td[data-col-name="Final Arranged Text"]');
            if (!targetCell) return;
            // Editable FTCO DISCRIPTION — do not overwrite user plain text.
            if (targetCell.dataset.ftcoDescEditable === '1'
                || targetCell.querySelector('textarea.ftco-desc-textarea, textarea.ftco-self-textarea')) {
                return;
            }

            const { group, type } = getRowGroupType(row);
            const typeObj = getTranslationMap(group, type);

            const originalHtml = ensureOriginalHtml(targetCell);
            targetCell.innerHTML = originalHtml;

            if (lang === 'fa' && typeObj) {
                translateHtmlTextNodes(targetCell, typeObj, lang);
                formatPersianDisplayText(targetCell, originalHtml);
            }

            applyLanguageDirection(targetCell, lang);
        }

        /**
         * Translate ALL rows in the table — including those NOT currently
         * rendered by the virtual scroll engine. We pull the full row set from
         * the engine (getRows) so scrolling never reveals untranslated rows.
         */
        let currentLang = (langSwitch && langSwitch.value) || 'en';
        function allTableRows() {
            const eng = window.VirtualScrollEngine;
            if (eng && eng.getRows) return eng.getRows();
            const c = document.getElementById('excel-table-container');
            return c ? Array.prototype.slice.call(c.querySelectorAll('tbody tr')) : [];
        }
        function translateColumn6(lang) {
            currentLang = lang;
            allTableRows().forEach(row => { translateSingleRowInternal(row, lang); });
        }

        // When the engine renders a new window of rows (on scroll), make sure
        // those freshly-attached rows match the currently selected language.
        if (window.VirtualScrollEngine && window.VirtualScrollEngine.onRender) {
            window.VirtualScrollEngine.onRender(function (rows, start, end) {
                if (currentLang === 'en') return; // English = original, nothing to do
                for (let i = start; i < end; i++) {
                    if (rows[i]) translateSingleRowInternal(rows[i], currentLang);
                }
            });
        }

        langSwitch.addEventListener('change', function () {
            const lang = this.value;
            loadTranslationJSON(true).then(() => translateColumn6(lang));
        });

        // Public API used by row_processor.js after an AJAX row update.
        window.TranslationManager = {
            setOriginalHtml,
            translateSingleRow(row, lang) {
                return loadTranslationJSON().then(() => translateSingleRowInternal(row, lang));
            },
            translateAll(lang) {
                return loadTranslationJSON(true).then(() => translateColumn6(lang));
            }
        };

        // Seed original HTML for every Final Arranged Text cell so save/reload
        // keeps backend highlight colours (tool_save reads dataset.originalHtml).
        function seedOriginalHtml() {
            allTableRows().forEach(function (row) {
                const cell = row.querySelector('td[data-col-name="Final Arranged Text"]');
                if (cell && !cell.dataset.originalHtml) {
                    cell.dataset.originalHtml = cell.innerHTML;
                }
            });
        }
        setTimeout(seedOriginalHtml, 0);
        if (window.VirtualScrollEngine && window.VirtualScrollEngine.onRender) {
            window.VirtualScrollEngine.onRender(function (rows, start, end) {
                for (let i = start; i < end; i++) {
                    const cell = rows[i] && rows[i].querySelector('td[data-col-name="Final Arranged Text"]');
                    if (cell && !cell.dataset.originalHtml) {
                        cell.dataset.originalHtml = cell.innerHTML;
                    }
                }
            });
        }

        // Backward-compatible names used by older code paths.
        window.translateColumn6 = (lang) => window.TranslationManager.translateAll(lang);
        window.translateSingleRow = (row, lang) => window.TranslationManager.translateSingleRow(row, lang);
    });
})(window, document);
