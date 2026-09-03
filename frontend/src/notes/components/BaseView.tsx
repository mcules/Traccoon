import { useEffect, useMemo, useState } from 'react';
import { tr, language } from "../../i18n";
import { useStore } from '../lib/store';
import { api, type BaseResult, type BaseRow } from '../lib/api';
import Icon from './Icon';

/**
 * A base, shown.
 *
 * The file says which notes belong together and what about them is worth
 * seeing; the server works that out and this shows it. Three layouts, as the
 * format allows: a table for comparing, cards for browsing, a list for reading.
 *
 * Sorting by a column header is this view's own, not the file's: it answers a
 * question one has while looking ("who is left?") and is gone on the next
 * visit, which is exactly how the desktop app treats it too — the file keeps
 * the order it was given.
 */

type SortState = { id: string; dir: 'asc' | 'desc' } | null;

/** How one value is put on screen. */
function Cell({ value, onOpen }: { value: unknown; onOpen: (path: string) => void }) {
  if (value === null || value === undefined || value === '') return <span className="base-empty">—</span>;

  if (typeof value === 'boolean') {
    return (
      <span className={`base-check ${value ? 'on' : ''}`}>
        <Icon name={value ? 'check-square' : 'square'} size={15} />
      </span>
    );
  }
  if (Array.isArray(value)) {
    return (
      <span className="base-list">
        {value.map((v, i) => (
          <span key={i}>
            {i > 0 && ', '}
            <Cell value={v} onOpen={onOpen} />
          </span>
        ))}
      </span>
    );
  }
  if (value && typeof value === 'object') {
    const o = value as Record<string, unknown>;
    if (o.kind === 'link') {
      const path = String(o.path ?? o.target ?? '');
      const label = String(o.display ?? o.target ?? path).replace(/\.(md|markdown)$/, '');
      return (
        <a
          className="internal-link"
          onClick={(e) => {
            e.preventDefault();
            onOpen(path);
          }}
        >
          {label}
        </a>
      );
    }
    if (o.kind === 'date') {
      const d = new Date(Number(o.ts));
      return <span>{o.hasTime ? d.toLocaleString(language()) : d.toLocaleDateString(language())}</span>;
    }
    return <span>{JSON.stringify(value)}</span>;
  }
  return <span>{String(value)}</span>;
}

const text = (v: unknown): string => {
  if (v === null || v === undefined) return '';
  if (typeof v === 'object') {
    const o = v as Record<string, unknown>;
    if (o.kind === 'link') return String(o.display ?? o.target ?? '');
    if (o.kind === 'date') return String(o.ts);
    if (Array.isArray(v)) return v.map(text).join(', ');
  }
  return String(v);
};

function compareValues(a: unknown, b: unknown): number {
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  if (typeof a === 'boolean' || typeof b === 'boolean') return Number(!!a) - Number(!!b);
  return text(a).localeCompare(text(b), language(), { numeric: true });
}

export default function BaseView() {
  const path = useStore((s) => s.activePath);
  const openFile = useStore((s) => s.openFile);
  const [data, setData] = useState<BaseResult | null>(null);
  const [viewName, setViewName] = useState<string | undefined>();
  const [error, setError] = useState('');
  const [sort, setSort] = useState<SortState>(null);

  useEffect(() => {
    setSort(null);
    setViewName(undefined);
  }, [path]);

  useEffect(() => {
    if (!path) return;
    let alive = true;
    setError('');
    api
      .baseView(path, viewName)
      .then((r) => alive && setData(r))
      .catch((e) => alive && setError(e.message || tr("notes_base.unreadable")));
    return () => {
      alive = false;
    };
  }, [path, viewName]);

  const sorted = useMemo(() => {
    if (!data) return [];
    if (!sort) return data.rows;
    const rows = [...data.rows].sort((a, b) => compareValues(a.values[sort.id], b.values[sort.id]));
    return sort.dir === 'desc' ? rows.reverse() : rows;
  }, [data, sort]);

  if (error) return <div className="base-view"><div className="base-error">{error}</div></div>;
  if (!data) return <div className="base-view"><div className="base-empty-state">{tr("notes_base.reading")}</div></div>;

  const open = (p: string) => void openFile(p);
  const nameColumn = data.columns.find((c) => c.id === 'file.name' || c.id === 'file.basename');

  const headerCell = (id: string, label: string) => (
    <th
      key={id}
      className={sort?.id === id ? `sorted ${sort.dir}` : ''}
      onClick={() =>
        setSort((s) => (s?.id === id ? (s.dir === 'asc' ? { id, dir: 'desc' } : null) : { id, dir: 'asc' }))
      }
    >
      {label}
      {sort?.id === id && <Icon name={sort.dir === 'asc' ? 'chevron-up' : 'chevron-down'} size={13} />}
    </th>
  );

  const bodyRow = (r: BaseRow) => (
    <tr key={r.path} onDoubleClick={() => open(r.path)}>
      {data.columns.map((c) => (
        <td key={c.id}>
          {c.id === nameColumn?.id ? (
            <a className="internal-link" onClick={() => open(r.path)}>
              {text(r.values[c.id]) || r.path}
            </a>
          ) : (
            <Cell value={r.values[c.id]} onOpen={open} />
          )}
        </td>
      ))}
    </tr>
  );

  const table = (rows: BaseRow[], withSummary: boolean) => (
    <table className="base-table">
      <thead>
        <tr>{data.columns.map((c) => headerCell(c.id, c.label))}</tr>
      </thead>
      <tbody>{rows.map(bodyRow)}</tbody>
      {withSummary && Object.keys(data.summaries).length > 0 && (
        <tfoot>
          <tr>
            {data.columns.map((c) => (
              <td key={c.id}>{data.summaries[c.id] ?? ''}</td>
            ))}
          </tr>
        </tfoot>
      )}
    </table>
  );

  const cards = (rows: BaseRow[]) => (
    <div className="base-cards">
      {rows.map((r) => (
        <div key={r.path} className="base-card" onClick={() => open(r.path)}>
          <div className="base-card-title">{text(r.values[nameColumn?.id ?? '']) || r.path.split('/').pop()}</div>
          {data.columns
            .filter((c) => c.id !== nameColumn?.id)
            .map((c) => (
              <div key={c.id} className="base-card-row">
                <span className="base-card-key">{c.label}</span>
                <Cell value={r.values[c.id]} onOpen={open} />
              </div>
            ))}
        </div>
      ))}
    </div>
  );

  const list = (rows: BaseRow[]) => (
    <ul className="base-list-view">
      {rows.map((r) => (
        <li key={r.path}>
          <a className="internal-link" onClick={() => open(r.path)}>
            {text(r.values[nameColumn?.id ?? '']) || r.path.split('/').pop()}
          </a>
        </li>
      ))}
    </ul>
  );

  const layout = (rows: BaseRow[], withSummary = false) =>
    data.view.type === 'cards' ? cards(rows) : data.view.type === 'list' ? list(rows) : table(rows, withSummary);

  return (
    <div className="base-view">
      <div className="base-head">
        {data.views.length > 1 ? (
          <div className="seg">
            {data.views.map((v) => (
              <button
                key={v.name}
                className={v.name === data.view.name ? 'active' : ''}
                onClick={() => setViewName(v.name)}
              >
                {v.name}
              </button>
            ))}
          </div>
        ) : (
          <div className="base-title">{data.view.name}</div>
        )}
        <span className="grow" />
        <div className="base-count">
          {data.total}
          {data.matched > data.total ? ` von ${data.matched}` : ''} Einträge
        </div>
      </div>

      {data.errors.length > 0 && (
        <div className="base-error">
          {data.errors.map((e, i) => (
            <div key={i}>{e}</div>
          ))}
        </div>
      )}

      <div className="base-body">
        {data.rows.length === 0 && <div className="base-empty-state">{tr("notes_base.no_note_matches")}</div>}
        {data.groups
          ? data.groups.map((g) => (
              <div key={g.key} className="base-group">
                <div className="base-group-head">
                  {g.key} <span className="base-count">{g.rows.length}</span>
                </div>
                {layout(g.rows)}
              </div>
            ))
          : data.rows.length > 0 && layout(sorted, true)}
      </div>
    </div>
  );
}
