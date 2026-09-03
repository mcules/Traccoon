import { useEffect, useMemo, useRef, useState } from 'react';
import { tr } from "../../i18n";
import { useStore } from '../lib/store';
import { renderMarkdown } from '../lib/markdown';
import { api } from '../lib/api';
import { splitSlides } from '../lib/slides';
import Icon from './Icon';

/**
 * A note, shown one slide at a time.
 *
 * The note stays a note: `---` on a line of its own is where one slide ends and
 * the next begins, which is exactly what a horizontal rule already means when
 * reading it top to bottom. The frontmatter is not a slide, and a lone `---`
 * directly under it is the block's closing line, not a break.
 *
 * Arrow keys, space and a tap on either half of the screen move; Escape leaves.
 */
export default function Presentation() {
  const path = useStore((s) => s.presenting);
  const close = useStore((s) => s.setPresenting);
  const content = useStore((s) => s.content);
  const activePath = useStore((s) => s.activePath);
  const [source, setSource] = useState('');
  const [i, setI] = useState(0);
  const [html, setHtml] = useState('');
  const hostRef = useRef<HTMLDivElement>(null);

  // The open note is already in memory; any other one is fetched.
  useEffect(() => {
    if (!path) return;
    setI(0);
    if (path === activePath) {
      setSource(content);
      return;
    }
    api
      .read(path)
      .then((r) => setSource(r.content))
      .catch(() => setSource(tr("notes_embed.unreadable")));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);

  const slides = useMemo(() => splitSlides(source), [source]);
  const count = slides.length;

  useEffect(() => {
    if (!path) return;
    let alive = true;
    renderMarkdown(slides[Math.min(i, count - 1)] ?? '', { rawUrl: (p) => api.rawUrl(p) })
      .then((h) => alive && setHtml(h))
      .catch(() => alive && setHtml(''));
    return () => {
      alive = false;
    };
  }, [slides, i, count, path]);

  useEffect(() => {
    if (!path) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { close(null); return; }
      if (['ArrowRight', 'ArrowDown', 'PageDown', ' '].includes(e.key)) {
        e.preventDefault();
        setI((n) => Math.min(n + 1, count - 1));
      } else if (['ArrowLeft', 'ArrowUp', 'PageUp'].includes(e.key)) {
        e.preventDefault();
        setI((n) => Math.max(n - 1, 0));
      } else if (e.key === 'Home') setI(0);
      else if (e.key === 'End') setI(count - 1);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [path, count, close]);

  if (!path) return null;

  const tap = (e: React.MouseEvent) => {
    // Left third goes back, the rest forwards — the same halves a reader's
    // thumbs land on when holding a phone.
    const x = e.clientX - (e.currentTarget as HTMLElement).getBoundingClientRect().left;
    const w = (e.currentTarget as HTMLElement).clientWidth;
    if (x < w / 3) setI((n) => Math.max(n - 1, 0));
    else setI((n) => Math.min(n + 1, count - 1));
  };

  return (
    <div className="presentation">
      <div className="pres-bar">
        <div className="pres-title">{path.split('/').pop()?.replace(/\.(md|markdown)$/, '')}</div>
        <div className="pres-count">
          {i + 1} / {count}
        </div>
        <button className="tool-btn" title={tr("notes_presentation.leave")} onClick={() => close(null)}>
          <Icon name="x" size={18} />
        </button>
      </div>
      <div className="pres-stage" onClick={tap}>
        <div
          className="pres-slide markdown-preview"
          ref={hostRef}
          dangerouslySetInnerHTML={{ __html: html }}
        />
      </div>
      <div className="pres-progress">
        <div className="pres-progress-fill" style={{ width: `${((i + 1) / count) * 100}%` }} />
      </div>
    </div>
  );
}
