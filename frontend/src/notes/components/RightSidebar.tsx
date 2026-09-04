import { useEffect, useMemo, useState } from 'react';
import { tr } from "../../i18n";
import { panelResizeHandler, restorePanelWidth } from '../lib/panelResize';
import { useStore } from '../lib/store';
import { api, type NoteMatches } from '../lib/api';
import { outline } from '../lib/markdown';
import TagsPanel from './TagsPanel';
import Icon from './Icon';

const MD_RE = /\.(md|markdown)$/i;
const name = (p: string) => p.split('/').pop()?.replace(MD_RE, '') ?? p;

const TABS = [
  { id: 'backlinks', icon: 'link', title: 'Backlinks' },
  { id: 'outgoing', icon: 'arrow-up-right', title: 'Outgoing links' },
  { id: 'tags', icon: 'hash', title: 'Tags' },
  { id: 'outline', icon: 'list', title: 'Gliederung' },
  { id: 'properties', icon: 'file-text', title: 'Eigenschaften' },
] as const;

// Der Assistent stand hier einmal als sechster Reiter. Er haengt jetzt im Kopf
// des Hauses und geht ueber jeder Seite auf, auch ueber dieser — ein zweiter
// Weg zu derselben Unterhaltung waere nur ein zweiter Ort, an dem man nachsieht.

function Section({
  title,
  count,
  open,
  onToggle,
  children,
}: {
  title: string;
  count: number;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <>
      <div className="section-head" onClick={onToggle}>
        <Icon name={open ? 'chevron-down' : 'chevron-right'} size={14} />
        <span>{title}</span>
        <span className="count">{count}</span>
      </div>
      {open && children}
    </>
  );
}

/** Settle a fast-changing value — typing must not re-run a vault-wide search
 *  on every keystroke, which is three requests each time. */
function useSettled<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setSettled(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);
  return settled;
}

/** Linked mentions (backlinks index) + Unlinked mentions (plain-text title hits). */
function BacklinksPanel() {
  const activePath = useStore((s) => s.activePath);
  const content = useStore((s) => s.content);
  const openFile = useStore((s) => s.openFile);
  const [linked, setLinked] = useState<string[]>([]);
  /** Text around each mention, so a backlink shows what it says, not just who said it. */
  const [linkedContexts, setLinkedContexts] = useState<Record<string, NoteMatches>>({});
  const [unlinked, setUnlinked] = useState<NoteMatches[]>([]);
  const [openLinked, setOpenLinked] = useState(true);
  const [openUnlinked, setOpenUnlinked] = useState(true);
  const settledContent = useSettled(content, 900);
  // A fresh array is a new dependency even when it holds the same paths, which
  // re-ran the vault-wide search several times per note. Compare the contents.
  const linkedKey = linked.join('|');

  useEffect(() => {
    if (!activePath || !MD_RE.test(activePath)) {
      setLinked([]);
      return;
    }
    api.backlinks(activePath).then((r) => setLinked(r.backlinks)).catch(() => setLinked([]));
  }, [activePath, settledContent]);

  // "links to X" said nothing a reader could use — the sentence around the link
  // is the whole reason to look at a backlink list. The endpoint that already
  // serves the unlinked mentions provides it.
  useEffect(() => {
    const paths = linkedKey ? linkedKey.split('|') : [];
    if (!activePath || !paths.length || !openLinked) {
      setLinkedContexts({});
      return;
    }
    let stale = false;
    api
      .searchMatches(name(activePath), paths.slice(0, 50), false, true)
      .then(({ matches }) => {
        if (stale) return;
        const map: Record<string, NoteMatches> = {};
        for (const m of matches) map[m.path] = m;
        setLinkedContexts(map);
      })
      .catch(() => {});
    return () => {
      stale = true;
    };
  }, [activePath, linkedKey, openLinked]);

  useEffect(() => {
    if (!activePath || !MD_RE.test(activePath) || !openUnlinked) {
      setUnlinked([]);
      return;
    }
    let stale = false;
    const title = name(activePath);
    (async () => {
      try {
        const { hits } = await api.search(title, 100);
        const linkedSet = new Set(linkedKey ? linkedKey.split('|') : []);
        const candidates = hits
          .filter((h) => h.path !== activePath && !linkedSet.has(h.path) && MD_RE.test(h.path))
          .slice(0, 30)
          .map((h) => h.path);
        if (!candidates.length) {
          if (!stale) setUnlinked([]);
          return;
        }
        const { matches } = await api.searchMatches(title, candidates, false, true);
        if (!stale) setUnlinked(matches.filter((m) => m.count > 0));
      } catch {
        if (!stale) setUnlinked([]);
      }
    })();
    return () => {
      stale = true;
    };
    // Only while the section is open: a vault-wide search plus per-note match
    // contexts is the most expensive thing this sidebar does.
  }, [activePath, linkedKey, openUnlinked]);

  return (
    <>
      <div className="nav-header">
        <span className="nav-title">
          {activePath && MD_RE.test(activePath) ? `Backlinks for ${name(activePath)}` : 'Backlinks'}
        </span>
      </div>
      <div className="sidebar-body">
        <Section title={tr("notes_panel.linked_mentions")} count={linked.length} open={openLinked} onToggle={() => setOpenLinked(!openLinked)}>
          {linked.length === 0 && <div className="panel-item">{tr("notes_panel.no_backlinks")}</div>}
          {linked.map((b) => (
            <div key={b} className="mention-box">
              <div className="mention-src" onClick={() => openFile(b)}>
                {name(b)}
              </div>
              {linkedContexts[b]?.contexts.slice(0, 2).map((c, i) => (
                <div key={i} style={{ color: 'var(--text-muted)' }}>
                  {c.pre && '…'}
                  {c.text}
                  {c.post && '…'}
                </div>
              )) ?? <div style={{ color: 'var(--text-faint)' }}>verlinkt auf {name(activePath ?? '')}</div>}
            </div>
          ))}
        </Section>
        <Section
          title={tr("notes_panel.unlinked_mentions")}
          count={unlinked.length}
          open={openUnlinked}
          onToggle={() => setOpenUnlinked(!openUnlinked)}
        >
          {unlinked.length === 0 && <div className="panel-item">{tr("notes_panel.no_unlinked")}</div>}
          {unlinked.map((m) => (
            <div key={m.path} className="mention-box">
              <div className="mention-src" onClick={() => openFile(m.path)}>
                {name(m.path)}
              </div>
              {m.contexts[0] && (
                <div style={{ color: 'var(--text-muted)' }}>
                  {m.contexts[0].pre && '…'}
                  {m.contexts[0].text}
                  {m.contexts[0].post && '…'}
                </div>
              )}
            </div>
          ))}
        </Section>
      </div>
    </>
  );
}

/** Every wikilink in the active note, resolved (exists) or not (click creates). */
function OutgoingPanel() {
  const activePath = useStore((s) => s.activePath);
  const content = useStore((s) => s.content);
  const openWikilink = useStore((s) => s.openWikilink);
  const [resolved, setResolved] = useState<Record<string, string | null>>({});

  const targets = useMemo(() => {
    const out: string[] = [];
    const seen = new Set<string>();
    for (const m of content.matchAll(/!?\[\[([^\]]+?)\]\]/g)) {
      const t = m[1].split('|')[0].split('#')[0].trim();
      if (t && !seen.has(t)) {
        seen.add(t);
        out.push(t);
      }
    }
    return out.slice(0, 200);
  }, [content]);

  useEffect(() => {
    let stale = false;
    Promise.all(
      targets.map((t) =>
        api
          .resolve(t)
          .then((r) => [t, r.path] as const)
          .catch(() => [t, null] as const),
      ),
    ).then((pairs) => {
      if (!stale) setResolved(Object.fromEntries(pairs));
    });
    return () => {
      stale = true;
    };
  }, [targets]);

  const links = targets.filter((t) => resolved[t] !== undefined && resolved[t] !== null);
  // /api/resolve only knows notes — an unresolved attachment embed (image/pdf/…)
  // is NOT a "create this note" candidate, so keep those out of the list.
  const unresolved = targets.filter(
    (t) => resolved[t] === null && !/\.(png|jpe?g|gif|svg|webp|bmp|ico|pdf|mp3|mp4|mov|zip)$/i.test(t),
  );

  return (
    <>
      <div className="nav-header">
        <span className="nav-title">
          {activePath && MD_RE.test(activePath) ? `Outgoing links from ${name(activePath)}` : 'Outgoing links'}
        </span>
      </div>
      <div className="sidebar-body">
        <div className="section-head" style={{ cursor: 'default' }}>
          <span>{tr("notes_panel.links")}</span>
          <span className="count">{links.length}</span>
        </div>
        {links.length === 0 && <div className="panel-item">{tr("notes_panel.no_outgoing")}</div>}
        {links.map((t) => (
          <div key={t} className="outgoing-item" onClick={() => openWikilink(t)} title={resolved[t] ?? t}>
            <Icon name="file-text" size={14} />
            <span>{t}</span>
          </div>
        ))}
        {unresolved.length > 0 && (
          <div className="section-head" style={{ cursor: 'default' }}>
            <span>{tr("notes_panel.unresolved")}</span>
            <span className="count">{unresolved.length}</span>
          </div>
        )}
        {unresolved.map((t) => (
          <div key={t} className="outgoing-item unresolved" onClick={() => openWikilink(t)} title={tr("notes_panel.not_created_yet")}>
            <Icon name="file-plus" size={14} />
            <span>{t}</span>
          </div>
        ))}
      </div>
    </>
  );
}

function OutlinePanel() {
  const content = useStore((s) => s.content);
  const heads = outline(content);
  return (
    <>
      <div className="nav-header">
        <span className="nav-title">{tr("notes_panel.outline")}</span>
      </div>
      <div className="sidebar-body">
        {heads.length === 0 && <div className="panel-item">{tr("notes_panel.no_headings")}</div>}
        {heads.map((h, i) => (
          <div key={i} className="outline-item" style={{ paddingLeft: 10 + (h.level - 1) * 12 }}>
            {h.text}
          </div>
        ))}
      </div>
    </>
  );
}

/** Mirror of the left panel: same handle, same memory, growing the other way. */
const RIGHT_PANEL = {
  variable: '--right-width',
  storageKey: 'wo-right-width',
  grows: 'left' as const,
  other: '.sidebar',
};

export default function RightSidebar() {
  const rightPanel = useStore((s) => s.rightPanel);
  const setRightPanel = useStore((s) => s.setRightPanel);

  useEffect(() => restorePanelWidth(RIGHT_PANEL), []);
  const onResizeDown = panelResizeHandler(RIGHT_PANEL);

  return (
    <div className="right-sidebar">
      <div className="sidebar-resizer right" title={tr("notes_sidebar.drag_to_resize")} onPointerDown={onResizeDown} />
      <div className="right-tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={`right-tab ${rightPanel === t.id ? 'active' : ''}`}
            title={t.title}
            onClick={() => setRightPanel(t.id)}
          >
            <Icon name={t.icon} size={16} />
          </button>
        ))}
      </div>
      {rightPanel === 'backlinks' && <BacklinksPanel />}
      {rightPanel === 'outgoing' && <OutgoingPanel />}
      {rightPanel === 'tags' && (
        <>
          <div className="nav-header">
            <span className="nav-title">{tr("notes_panel.tags")}</span>
          </div>
          <div className="sidebar-body">
            <TagsPanel />
          </div>
        </>
      )}
      {rightPanel === 'outline' && <OutlinePanel />}
      {rightPanel === 'properties' && <PropertiesPanel />}
    </div>
  );
}

/**
 * The note's own properties, and every property the vault knows.
 *
 * The data was already there — the endpoints feed the editor's property widget
 * — but there was no way to look at the vault through them: which notes carry
 * `firma`, what values `status` takes. Clicking a key searches for it.
 */
function PropertiesPanel() {
  const activePath = useStore((s) => s.activePath);
  const content = useStore((s) => s.content);
  const searchFor = useStore((s) => s.searchFor);
  const [all, setAll] = useState<Array<{ key: string; type: string; count: number }>>([]);
  const [openOwn, setOpenOwn] = useState(true);
  const [openAll, setOpenAll] = useState(true);

  useEffect(() => {
    api
      .properties()
      .then((r) => setAll(r.properties))
      .catch(() => setAll([]));
  }, [activePath]);

  // The open note's frontmatter, read straight off the text so it follows edits
  // without a round trip.
  const own = useMemo(() => {
    const m = /^---\r?\n([\s\S]*?)\r?\n---/.exec(content);
    if (!m) return [] as Array<[string, string]>;
    const out: Array<[string, string]> = [];
    for (const line of m[1].split(/\r?\n/)) {
      const kv = /^([^:\s][^:]*):\s*(.*)$/.exec(line);
      if (kv) out.push([kv[1].trim(), kv[2].trim()]);
    }
    return out;
  }, [content]);

  return (
    <>
      <div className="nav-header">
        <span className="nav-title">{tr("notes_panel.properties")}</span>
      </div>
      <div className="sidebar-body">
        <Section title={tr("notes_panel.this_note")} count={own.length} open={openOwn} onToggle={() => setOpenOwn((v) => !v)}>
          {own.map(([k, v]) => (
            <div key={k} className="prop-row">
              <span className="prop-key">{k}</span>
              <span className="prop-val">{v || <em>leer</em>}</span>
            </div>
          ))}
          {!own.length && <div className="nav-empty">{tr("notes_panel.no_properties")}</div>}
        </Section>
        <Section title={tr("notes_panel.all_properties")} count={all.length} open={openAll} onToggle={() => setOpenAll((v) => !v)}>
          {all.map((p) => (
            <div key={p.key} className="tag-row clickable" onClick={() => searchFor(`["${p.key}"]`)}>
              <span>{p.key}</span>
              <span className="tag-count">{p.count}</span>
            </div>
          ))}
        </Section>
      </div>
    </>
  );
}
