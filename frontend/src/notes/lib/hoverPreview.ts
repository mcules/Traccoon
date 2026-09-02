import { api } from './api';
import { renderMarkdown } from './markdown';
import { extractEmbedSection } from './embedSection';
import { memo } from './dataview/cache';

/**
 * The note behind a link, shown on hover.
 *
 * Reading a dashboard means following links to see whether they are the one
 * meant — which costs a note switch each time, and loses where one was. The
 * preview answers the question without leaving the page.
 *
 * Deliberately shallow: what it renders never runs queries or embeds of its
 * own. A dashboard hovered over would otherwise start a chain of server work
 * for something the reader is about to move away from.
 */

let el: HTMLElement | null = null;
let timer: number | null = null;
let current: string | null = null;

const HOVER_DELAY = 320;

function box(): HTMLElement {
  if (el) return el;
  el = document.createElement('div');
  el.className = 'hover-popover';
  el.style.display = 'none';
  // Into the themed container, not onto the body: the colour tokens are defined
  // on `.theme-dark` / `.theme-light`, and outside it `var(--bg-primary)` is not
  // merely wrong but undefined — which makes the whole declaration invalid, so
  // the card came out with no background and no border, and one read the page
  // straight through it.
  (document.querySelector('.theme-dark, .theme-light') ?? document.body).appendChild(el);
  // Arriving on the card keeps it open; leaving it starts the countdown.
  el.addEventListener('mouseenter', cancelHide);
  el.addEventListener('mouseleave', hideSoon);
  return el;
}

/**
 * Closing, but not straight away.
 *
 * Between the link and the card there is a gap, and the pointer has to cross it
 * to reach the card. Closing on the first `mouseout` meant the card vanished
 * mid-way every time, so it could be read but never scrolled, never clicked,
 * never copied from. So leaving starts a short countdown that arriving on the
 * card cancels.
 */
let hideTimer: number | null = null;

function cancelHide(): void {
  if (hideTimer !== null) window.clearTimeout(hideTimer);
  hideTimer = null;
}

function hide(): void {
  cancelHide();
  current = null;
  if (timer !== null) window.clearTimeout(timer);
  timer = null;
  if (el) el.style.display = 'none';
}

/** Leaving: give the pointer time to arrive on the card. */
function hideSoon(): void {
  cancelHide();
  hideTimer = window.setTimeout(hide, 260);
}

function place(anchor: DOMRect): void {
  const b = box();
  b.style.display = 'block';
  const rect = b.getBoundingClientRect();
  const margin = 8;
  // The vertical distance is what the pointer has to cross, so it stays small;
  // the horizontal margin only keeps the card off the window edge.
  const gap = 2;
  let top = anchor.bottom + gap;
  // Not enough room below → above the link, the same flip the suggester uses.
  if (top + rect.height > window.innerHeight - margin) top = Math.max(margin, anchor.top - rect.height - gap);
  const left = Math.min(Math.max(margin, anchor.left), window.innerWidth - rect.width - margin);
  b.style.top = `${top}px`;
  b.style.left = `${left}px`;
}

async function show(target: string, anchor: DOMRect): Promise<void> {
  const note = await memo('hover', target.split('#')[0].trim(), async () => {
    try {
      const { path } = await api.resolve(target.split('#')[0].trim());
      if (!path) return null;
      const r = await api.read(path);
      return { path, content: typeof r === 'string' ? r : r.content };
    } catch {
      return null;
    }
  });
  if (!note || current !== target) return;
  const section = extractEmbedSection(note.content, target);
  const html = await renderMarkdown(section, { rawUrl: (p) => api.rawUrl(p) });
  if (current !== target) return;
  const b = box();
  b.innerHTML = `<div class="markdown-embed"><div class="markdown-embed-title">${note.path
    .split('/')
    .pop()
    ?.replace(/\.(md|markdown)$/i, '')}</div><div class="markdown-embed-content markdown-preview">${html}</div></div>`;
  place(anchor);
}

/** Start watching for link hovers. Returns a function that stops again. */
export function installHoverPreview(): () => void {
  const onOver = (e: MouseEvent) => {
    // Touch has no hover; a popover there would fight every tap.
    if (matchMedia('(pointer: coarse)').matches) return;
    const t = (e.target as HTMLElement | null)?.closest<HTMLElement>(
      'a.internal-link, .cm-hmd-internal-link, .tree-row[data-path], .search-result[data-path]',
    );
    if (!t) return;
    const target =
      t.dataset.wikilink ?? t.dataset.href ?? t.dataset.path ?? t.textContent?.trim() ?? '';
    if (!target || target === current) {
      // Back on the same link: whatever countdown was running is off.
      cancelHide();
      return;
    }
    hide();
    current = target;
    const rect = t.getBoundingClientRect();
    timer = window.setTimeout(() => void show(target, rect), HOVER_DELAY);
  };
  const onOut = (e: MouseEvent) => {
    const to = e.relatedTarget as HTMLElement | null;
    if (to && (to.closest('.hover-popover') || to.closest('a.internal-link'))) return;
    hideSoon();
  };
  // The page scrolling away under the card takes the card with it — but the
  // card scrolling inside itself is someone reading it, and that closed it too,
  // so a preview longer than its box could not be read past the first screen.
  const onScroll = (e: Event) => {
    const t = e.target as HTMLElement | null;
    if (t && typeof t.closest === 'function' && t.closest('.hover-popover')) return;
    hide();
  };
  document.addEventListener('mouseover', onOver);
  document.addEventListener('mouseout', onOut);
  window.addEventListener('scroll', onScroll, true);
  return () => {
    document.removeEventListener('mouseover', onOver);
    document.removeEventListener('mouseout', onOut);
    window.removeEventListener('scroll', onScroll, true);
    hide();
  };
}
