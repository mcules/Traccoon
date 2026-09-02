export { renderDqlBlock, renderResult, errorEl } from './render';
export { inlineMarkdown, valueHtml, escapeHtml } from './format';
export { renderJsBlock, invalidateDataviewCache } from './dvjs';
export { renderTasksBlock } from './tasks';
export { renderInlineDataview, renderInlineDataviewWhenReady, inlineFieldEl, inlineQueryEl, inlineQueryOf, inlineFieldRegex } from './inline';
export { ensurePluginSettings, dataviewSettings, tasksSettings } from './settings';
export * from './values';

import { renderDqlBlock } from './render';
import { renderJsBlock } from './dvjs';
import { renderTasksBlock } from './tasks';
import { renderInlineDataviewWhenReady } from './inline';

/** Replace every ```dataview / ```dataviewjs code block inside `root` with its
 *  rendered result. Used by the reading view after markdown is in the DOM. */
export function renderDataviewBlocks(root: HTMLElement, path: string | null): void {
  const blocks = root.querySelectorAll<HTMLElement>(
    'pre > code.language-dataview, pre > code.language-dataviewjs, pre > code.language-tasks',
  );
  for (const codeEl of blocks) {
    const pre = codeEl.parentElement;
    if (!pre) continue;
    const code = codeEl.textContent ?? '';
    if (codeEl.classList.contains('language-dataviewjs')) pre.replaceWith(renderJsBlock(code, path));
    else if (codeEl.classList.contains('language-tasks')) pre.replaceWith(renderTasksBlock(code));
    else pre.replaceWith(renderDqlBlock(code, path));
  }
  // Inline queries and inline fields live in ordinary prose, not in blocks.
  renderInlineDataviewWhenReady(root, path);
}
