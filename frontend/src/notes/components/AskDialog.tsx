import { useEffect, useMemo, useState } from 'react';
import { tr } from "../../i18n";
import { onAsk, answer, type AskRequest } from '../lib/templater/ask';
import { prepareQuery, fuzzySearch } from '../lib/fuzzy';
import Icon from './Icon';

/**
 * The dialog a template asks its questions through.
 *
 * One component for both kinds: a line of text, or a list to choose from.
 * Cancelling answers with null, which is what the templates check for when they
 * decide to leave a field out.
 */
export default function AskDialog() {
  const [req, setReq] = useState<AskRequest | null>(null);
  const [value, setValue] = useState('');
  const [sel, setSel] = useState(0);
  const [q, setQ] = useState('');

  useEffect(() => {
    onAsk((r) => {
      setReq(r);
      setValue(r?.fallback ?? '');
      setQ('');
      setSel(0);
    });
    return () => onAsk(null);
  }, []);

  const options = useMemo(() => {
    if (!req?.options) return [];
    if (!q.trim()) return req.options;
    const query = prepareQuery(q);
    return req.options.filter((o) => fuzzySearch(query, o.label));
  }, [req, q]);

  if (!req) return null;

  const cancel = () => answer(null);

  return (
    <div className="modal-bg" onClick={cancel}>
      <div className="modal ask-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="ask-label">{req.label || (req.kind === 'text' ? 'Eingabe' : 'Auswahl')}</div>
        {req.kind === 'text' ? (
          req.multiline ? (
            <textarea
              autoFocus
              rows={4}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') cancel();
                if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) answer(value);
              }}
            />
          ) : (
            <input
              autoFocus
              value={value}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') cancel();
                if (e.key === 'Enter') answer(value);
              }}
            />
          )
        ) : (
          <>
            <input
              autoFocus
              placeholder={tr("notes_dialog.filter")}
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setSel(0);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Escape') cancel();
                else if (e.key === 'ArrowDown') { e.preventDefault(); setSel((i) => Math.min(i + 1, options.length - 1)); }
                else if (e.key === 'ArrowUp') { e.preventDefault(); setSel((i) => Math.max(i - 1, 0)); }
                else if (e.key === 'Enter' && options[sel]) answer(options[sel].value);
              }}
            />
            <div className="palette-list">
              {options.map((o, i) => (
                <div
                  key={`${o.label}-${i}`}
                  className={`palette-item ${i === sel ? 'sel' : ''}`}
                  onMouseEnter={() => setSel(i)}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    answer(o.value);
                  }}
                >
                  {o.label}
                </div>
              ))}
              {!options.length && <div className="palette-item">{tr("notes_dialog.nothing_chosen")}</div>}
            </div>
          </>
        )}
        {req.kind === 'text' && (
          <div className="event-actions">
            <span className="grow" />
            <button className="tool-btn" onClick={cancel}><Icon name="x" size={15} /> {tr("notes_dialog.cancel")}</button>
            <button className="primary" onClick={() => answer(value)}><Icon name="check" size={15} /> {tr("notes_dialog.take_it")}</button>
          </div>
        )}
      </div>
    </div>
  );
}
