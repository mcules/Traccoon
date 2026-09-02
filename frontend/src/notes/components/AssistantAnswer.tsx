import { useEffect, useState } from 'react';
import { useStore } from '../lib/store';
import { renderMarkdown } from '../lib/markdown';
import { api } from '../lib/api';
import { openLightbox } from '../lib/imageLightbox';

/**
 * What the assistant said, read as markdown.
 *
 * It writes the same markdown the notes are written in, so it is rendered the
 * same way: headings, lists, emphasis, code — and `[[wikilinks]]` that open the
 * note they name. Showing the raw text instead turns a structured answer into a
 * wall of asterisks, which is exactly what one does not want to read on a phone
 * at eight in the morning.
 *
 * Embeds are deliberately not expanded here. A chat line saying `![[drawing]]`
 * should stay a reference; pulling a whole note or a picture into the sidebar
 * would push the answer off the screen.
 */
export default function AssistantAnswer({ text, className }: { text: string; className: string }) {
  const openWikilink = useStore((s) => s.openWikilink);
  const [html, setHtml] = useState('');

  useEffect(() => {
    let alive = true;
    renderMarkdown(text, { rawUrl: (p) => api.rawUrl(p) })
      .then((h) => alive && setHtml(h))
      .catch(() => alive && setHtml(''));
    return () => {
      alive = false;
    };
  }, [text]);

  const onClick = (e: React.MouseEvent) => {
    const img = e.target as HTMLElement;
    if (img instanceof HTMLImageElement) {
      e.preventDefault();
      openLightbox(img.currentSrc || img.src, img.alt);
      return;
    }
    const link = (e.target as HTMLElement).closest('[data-wikilink]') as HTMLElement | null;
    if (!link) return;
    e.preventDefault();
    const target = link.getAttribute('data-wikilink');
    if (target) openWikilink(target);
  };

  // Until the render is through, the plain text stands there — that is the
  // same words, just without the shape, and better than an empty box.
  return html ? (
    <div className={`${className} markdown-preview`} onClick={onClick} dangerouslySetInnerHTML={{ __html: html }} />
  ) : (
    <div className={className}>{text}</div>
  );
}
