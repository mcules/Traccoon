import { useEffect, useMemo, useState } from 'react';
import { useStore } from '../lib/store';
import { api } from '../lib/api';
import { insertTemplate } from '../lib/templater/run';
import { prepareQuery, fuzzySearch } from '../lib/fuzzy';

/**
 * Pick a template and drop it in at the caret.
 *
 * The list comes from the vault's own template folder, and the text is filled
 * in server-side (dates, the note title) before it arrives — the same pass the
 * daily note goes through, so a template behaves identically whichever way it
 * is used.
 */
export default function TemplatePicker() {
  const open = useStore((s) => s.templatePickerOpen);
  const setOpen = useStore((s) => s.setTemplatePicker);
  const activePath = useStore((s) => s.activePath);
  const notify = useStore((s) => s.notify);
  const [items, setItems] = useState<Array<{ path: string; name: string }>>([]);
  const [q, setQ] = useState('');
  const [sel, setSel] = useState(0);

  useEffect(() => {
    if (!open) return;
    setQ('');
    setSel(0);
    api
      .templates()
      .then((r) => setItems(r.templates))
      .catch(() => setItems([]));
  }, [open]);

  const shown = useMemo(() => {
    if (!q.trim()) return items.slice(0, 50);
    const query = prepareQuery(q);
    return items
      .map((t) => ({ item: t, match: fuzzySearch(query, t.name) }))
      .filter((r): r is { item: { path: string; name: string }; match: NonNullable<typeof r.match> } => !!r.match)
      .sort((a, b) => b.match.score - a.match.score)
      .slice(0, 50)
      .map((r) => r.item);
  }, [items, q]);

  if (!open) return null;

  const insert = async (tpl: { path: string; name: string }) => {
    setOpen(false);
    try {
      // Runs the template rather than only substituting dates: the vault's
      // templates ask questions, and those need the dialog.
      await insertTemplate(tpl.path);
    } catch (e: any) {
      notify(e.message);
    }
  };

  return (
    <div className="modal-bg" onClick={() => setOpen(false)}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <input
          className="palette-input"
          autoFocus
          placeholder="Vorlage einfügen…"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setSel(0);
          }}
          onKeyDown={(e) => {
            if (e.key === 'Escape') setOpen(false);
            else if (e.key === 'ArrowDown') {
              e.preventDefault();
              setSel((i) => Math.min(i + 1, shown.length - 1));
            } else if (e.key === 'ArrowUp') {
              e.preventDefault();
              setSel((i) => Math.max(i - 1, 0));
            } else if (e.key === 'Enter' && shown[sel]) {
              e.preventDefault();
              void insert(shown[sel]);
            }
          }}
        />
        <div className="palette-list">
          {shown.map((t, i) => (
            <div
              key={t.path}
              className={`palette-item ${i === sel ? 'sel' : ''}`}
              onMouseEnter={() => setSel(i)}
              onMouseDown={(e) => {
                e.preventDefault();
                void insert(t);
              }}
            >
              <span>{t.name}</span>
            </div>
          ))}
          {!shown.length && <div className="palette-item">Keine Vorlage gefunden</div>}
        </div>
      </div>
    </div>
  );
}
