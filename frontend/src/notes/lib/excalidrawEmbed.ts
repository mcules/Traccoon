import { api } from './api';
import { tr } from "../../i18n";

/**
 * Painting a drawing where a note embeds one.
 *
 * `![[drawing.excalidraw]]` means "the picture goes here", so that is what
 * appears — the real drawing, exported to SVG by the library that made it, not
 * a link to go and look at it somewhere else.
 *
 * The library is large and is fetched only when a note actually contains such
 * an embed; a vault with no drawings never pays for it. Each drawing is
 * exported once per session and reused, because the same one tends to appear in
 * several notes.
 */

const cache = new Map<string, Promise<SVGElement | null>>();

async function exportSvg(target: string): Promise<SVGElement | null> {
  const { path } = await api.resolve(target);
  if (!path) return null;
  const [{ scene }, mod] = await Promise.all([
    api.excalidrawScene(path) as Promise<{ scene: { elements: unknown[]; appState: Record<string, unknown>; files?: Record<string, unknown> } }>,
    import('@excalidraw/excalidraw'),
  ]);
  if (!scene.elements.length) return null;
  return mod.exportToSvg({
    elements: scene.elements as never,
    appState: { ...scene.appState, exportBackground: false, exportWithDarkMode: false } as never,
    files: (scene.files ?? {}) as never,
  });
}

/** Fill in every `.excalidraw-embed` placeholder inside a rendered note. */
export function renderDrawingEmbeds(root: HTMLElement): void {
  for (const host of root.querySelectorAll<HTMLElement>('.excalidraw-embed:not([data-done])')) {
    const target = host.dataset.drawing;
    if (!target) continue;
    host.dataset.done = '1';
    host.textContent = tr("notes_drawing.loading");
    let job = cache.get(target);
    if (!job) {
      job = exportSvg(target).catch(() => null);
      cache.set(target, job);
    }
    void job.then((svg) => {
      host.textContent = '';
      if (!svg) {
        host.textContent = `Zeichnung „${target}" nicht gefunden`;
        host.classList.add('excalidraw-embed-missing');
        return;
      }
      // The export carries its own pixel size; letting it scale keeps a wide
      // drawing inside the note's column instead of pushing it sideways.
      const clone = svg.cloneNode(true) as SVGElement;
      clone.removeAttribute('width');
      clone.removeAttribute('height');
      clone.setAttribute('preserveAspectRatio', 'xMidYMid meet');
      host.append(clone);
    });
  }
}
