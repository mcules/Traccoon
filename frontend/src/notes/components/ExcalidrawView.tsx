import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { tr, language } from "../../i18n";
import { useStore } from '../lib/store';
import { api } from '../lib/api';
import Icon from './Icon';

/**
 * A drawing, drawn — by Excalidraw itself.
 *
 * The library that made these files is the only thing that renders them the way
 * they were meant to look, down to the hand-drawn strokes, so it draws them
 * here too. It is large, so it is not part of the app: the browser fetches it
 * the first time a drawing is opened and never again.
 *
 * Saving keeps the file's own form — a compressed drawing stays compressed, the
 * note around it stays untouched — and is conditional on the version that was
 * opened, like every other write in this app.
 */
const Excalidraw = lazy(async () => {
  const [mod] = await Promise.all([import('@excalidraw/excalidraw'), import('@excalidraw/excalidraw/index.css')]);
  return { default: mod.Excalidraw };
});

// The fonts ship with the library; without this it would look for them on a
// CDN, which the content policy does not allow and which would be a request to
// somebody else's server for every drawing opened.
(window as unknown as { EXCALIDRAW_ASSET_PATH?: string }).EXCALIDRAW_ASSET_PATH = '/excalidraw/';

interface Scene {
  elements: unknown[];
  appState: Record<string, unknown>;
  files?: Record<string, unknown>;
}

export default function ExcalidrawView() {
  const path = useStore((s) => s.activePath);
  const notify = useStore((s) => s.notify);
  const [scene, setScene] = useState<Scene | null>(null);
  const [error, setError] = useState('');
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const hashRef = useRef('');
  const latest = useRef<Scene | null>(null);

  useEffect(() => {
    if (!path) return;
    let alive = true;
    setScene(null);
    setError('');
    setDirty(false);
    api
      .excalidrawScene(path)
      .then((r) => {
        if (!alive) return;
        hashRef.current = r.hash;
        latest.current = r.scene as Scene;
        setScene(r.scene as Scene);
      })
      .catch((e) => alive && setError(e.message || tr("notes_drawing.unreadable")));
    return () => {
      alive = false;
    };
  }, [path]);

  const save = useCallback(async () => {
    if (!path || !latest.current || saving) return;
    setSaving(true);
    try {
      const r = await api.excalidrawSave(path, latest.current, hashRef.current);
      hashRef.current = r.hash;
      setDirty(false);
      notify('Zeichnung gespeichert');
    } catch (e: any) {
      // 409: the file moved on while this was open. Say so rather than
      // deciding on the reader's behalf whose version wins.
      notify(e?.status === 409 ? tr("notes_drawing.changed_elsewhere") : e.message || tr("common.save_failed"));
    } finally {
      setSaving(false);
    }
  }, [path, saving, notify]);

  // Ctrl+S saves here too, so the reflex works in a drawing as in a note.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
        e.preventDefault();
        void save();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [save]);

  if (error) return <div className="excalidraw-view"><div className="base-error">{error}</div></div>;
  if (!scene) return <div className="excalidraw-view"><div className="base-empty-state">{tr("notes_drawing.loading")}</div></div>;

  return (
    <div className="excalidraw-view">
      <div className="exc-bar">
        <Icon name="pen" size={15} />
        <div className="exc-title">{path?.split('/').pop()?.replace(/\.excalidraw(\.md)?$/, '')}</div>
        <span className="grow" />
        {dirty && <span className="exc-dirty">{tr("notes_drawing.unsaved")}</span>}
        <button className="tool-btn exc-save" disabled={!dirty || saving} onClick={() => void save()}>
          <Icon name="check" size={15} />
          {tr("common.save")}
        </button>
      </div>
      <div className="exc-stage">
        <Suspense fallback={<div className="base-empty-state">{tr("notes_drawing.tool_loading")}</div>}>
          <Excalidraw
            initialData={{ elements: scene.elements as never, appState: scene.appState as never, files: scene.files as never, scrollToContent: true }}
            langCode={language()}
            onChange={(elements: readonly unknown[], appState: unknown, files: unknown) => {
              const next = {
                elements: [...elements],
                appState: appState as Record<string, unknown>,
                files: files as Record<string, unknown>,
              };
              const before = latest.current;
              latest.current = next;
              // Excalidraw reports a change for a mouse move over the canvas
              // too; only a different set of elements is an edit.
              if (before && JSON.stringify(before.elements) !== JSON.stringify(next.elements)) setDirty(true);
            }}
          />
        </Suspense>
      </div>
    </div>
  );
}
