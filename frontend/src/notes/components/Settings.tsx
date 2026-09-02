import { useEffect, useRef, useState } from 'react';
import { useStore } from '../lib/store';
import HotkeySettings from './HotkeySettings';
import CalendarSettings from './CalendarSettings';
import { api } from '../lib/api';
import Icon from './Icon';
import { applyRainbow } from '../lib/snippets';

type Section = 'vault' | 'git' | 'api' | 'appearance' | 'hotkeys' | 'calendar' | 'assistant';

export default function Settings() {
  const open = useStore((s) => s.settingsOpen);
  const setOpen = useStore((s) => s.setSettings);
  const [section, setSection] = useState<Section>('vault');
  const [settings, setSettings] = useState<any>(null);

  useEffect(() => {
    if (open) api.getSettings().then(setSettings).catch(() => {});
  }, [open]);

  if (!open) return null;

  return (
    <div className="modal-bg" onClick={() => setOpen(false)}>
      <div className="modal settings-modal" onClick={(e) => e.stopPropagation()}>
        {/* Der Dialog fuellt am Handy den Bildschirm, damit fehlt der
            Hintergrund zum Wegtippen. Deshalb eine eigene Kopfzeile. */}
        <div className="settings-head">
          <div className="title">Einstellungen</div>
          <button className="tool-btn" title="Schließen" onClick={() => setOpen(false)}>
            <Icon name="x" size={18} />
          </button>
        </div>
        <div className="settings-layout">
          <div className="settings-nav">
            {(['vault', 'calendar', 'assistant', 'git', 'api', 'appearance', 'hotkeys'] as Section[]).map((s) => (
              <button key={s} className={section === s ? 'active' : ''} onClick={() => setSection(s)}>
                {labels[s]}
              </button>
            ))}
          </div>
          <div className="settings-content">
            {settings && section === 'vault' && <VaultSettings s={settings} reload={() => api.getSettings().then(setSettings)} />}
            {settings && section === 'git' && <GitSettings s={settings} reload={() => api.getSettings().then(setSettings)} />}
            {section === 'api' && <ApiKeys />}
            {settings && section === 'appearance' && <Appearance s={settings} />}
            {section === 'hotkeys' && <HotkeySettings />}
            {section === 'calendar' && <CalendarSettings />}
            {settings && section === 'assistant' && (
              <AssistantSettings s={settings} reload={() => api.getSettings().then(setSettings)} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

const labels: Record<Section, string> = {
  vault: 'Vault & Dateien',
  git: 'Git-Sicherung',
  api: 'API-Schlüssel',
  appearance: 'Aussehen',
  hotkeys: 'Tastenkürzel',
  calendar: 'Kalender',
  assistant: 'Assistent',
};

function Row({ name, desc, children }: { name: string; desc?: string; children: React.ReactNode }) {
  return (
    <div className="setting-row">
      <div className="info">
        <div className="name">{name}</div>
        {desc && <div className="desc">{desc}</div>}
      </div>
      <div className="control">{children}</div>
    </div>
  );
}

function VaultSettings({ s, reload }: { s: any; reload: () => void }) {
  const [path, setPath] = useState(s.vault.path);
  const [deleteMode, setDeleteMode] = useState(s.vault.deleteMode ?? 'trash');
  const [browser, setBrowser] = useState<any>(null);
  const save = async () => {
    await api.putSettings({ vault: { path } });
    await reload();
    alert('Pfad gespeichert. Falls nötig, den Index über die Befehlspalette neu aufbauen.');
  };
  const saveDeleteMode = async (mode: string) => {
    setDeleteMode(mode);
    await api.putSettings({ vault: { deleteMode: mode } });
    await reload();
  };
  const browse = async (dir?: string) => setBrowser(await api.browse(dir).catch((e) => ({ error: e.message })));
  return (
    <div>
      <h2>Vault & Dateien</h2>
      <Row name="Pfad zum Vault" desc="Absoluter Pfad auf dem Server zum Notizordner">
        <input className="text-input" style={{ width: 260 }} value={path} onChange={(e) => setPath(e.target.value)} />
      </Row>
      <div style={{ display: 'flex', gap: 8, margin: '8px 0' }}>
        <button className="tool-btn" onClick={() => browse()}>Durchsuchen…</button>
        <button className="primary" onClick={save}>Vault-Pfad speichern</button>
      </div>
      {browser && !browser.error && (
        <div style={{ border: '1px solid var(--bg-modifier-border)', borderRadius: 6, padding: 8, marginTop: 8 }}>
          <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 6 }}>{browser.dir}</div>
          <div className="result" onClick={() => browse(browser.parent)} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Icon name="folder" size={15} /> ..
          </div>
          {browser.folders.map((f: any) => (
            <div className="result" key={f.path} onClick={() => browse(f.path)} onDoubleClick={() => setPath(f.path)} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Icon name="folder" size={15} /> {f.name}
              <button className="btn secondary" style={{ float: 'right', padding: '2px 8px' }} onClick={(e) => { e.stopPropagation(); setPath(f.path); }}>
                Wählen
              </button>
            </div>
          ))}
        </div>
      )}
      {browser?.error && <div style={{ color: '#e5534b' }}>{browser.error}</div>}
      <Row
        name="Beim Löschen einer Datei"
        desc="In den Papierkorb heißt: wiederherstellbar. Endgültig löschen entfernt sofort."
      >
        <select
          className="text-input"
          style={{ width: 220 }}
          value={deleteMode}
          onChange={(e) => saveDeleteMode(e.target.value)}
        >
          <option value="trash">In den Papierkorb (wiederherstellbar)</option>
          <option value="permanent">Endgültig löschen</option>
        </select>
      </Row>
    </div>
  );
}

function GitSettings({ s, reload }: { s: any; reload: () => void }) {
  const [g, setG] = useState({ ...s.git });
  const [log, setLog] = useState<string[]>([]);
  const logRef = useRef<HTMLTextAreaElement>(null);
  const set = (k: string, v: any) => setG((p: any) => ({ ...p, [k]: v }));
  // Append timestamped lines to the running log instead of replacing it, so the
  // textarea keeps a history of every git action across clicks.
  const append = (lines: string[]) => {
    const ts = new Date().toLocaleTimeString();
    setLog((prev) => [...prev, ...lines.map((l, i) => (i === 0 ? `[${ts}] ${l}` : `         ${l}`))]);
  };
  // Auto-scroll to the newest line whenever the log grows.
  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [log]);
  const save = async () => { await api.putSettings({ git: g }); await reload(); append(['Saved git settings']); };
  const run = async (fn: () => Promise<any>, label: string) => {
    append([`${label}…`]);
    try {
      const r = await fn();
      // sync returns { ok, log: string[] }; others return { message }. Split any
      // embedded newlines so multi-line git output renders one line per row.
      const lines: string[] = Array.isArray(r?.log)
        ? [`${label} ${r.ok ? 'ok' : 'NOT ok'}`, ...r.log]
        : [String(r?.message ?? JSON.stringify(r))];
      append(lines.flatMap((l) => String(l).split('\n')));
    } catch (e: any) { append([`Error: ${e.message}`]); }
  };
  return (
    <div>
      <h2>Git-Sicherung</h2>
      <Row name="Git-Abgleich einschalten"><input type="checkbox" checked={g.enabled} onChange={(e) => set('enabled', e.target.checked)} /></Row>
      <Row name="Adresse der Gegenstelle" desc="https://github.com/owner/repo.git">
        <input className="text-input" style={{ width: 260 }} value={g.remote} onChange={(e) => set('remote', e.target.value)} />
      </Row>
      <Row name="Zweig"><input className="text-input" style={{ width: 120 }} value={g.branch} onChange={(e) => set('branch', e.target.value)} /></Row>
      <Row name="Zugriffstoken" desc="Bleibt auf dem Server; maskiert lassen behält den gespeicherten">
        <input className="text-input" type="password" style={{ width: 260 }} value={g.token} onChange={(e) => set('token', e.target.value)} />
      </Row>
      <Row name="Name im Commit"><input className="text-input" value={g.authorName} onChange={(e) => set('authorName', e.target.value)} /></Row>
      <Row name="E-Mail im Commit"><input className="text-input" value={g.authorEmail} onChange={(e) => set('authorEmail', e.target.value)} /></Row>
      <Row name="Von selbst abgleichen" desc="Im unten eingestellten Takt holen, sichern und hochladen"><input type="checkbox" checked={g.autoSync} onChange={(e) => set('autoSync', e.target.checked)} /></Row>
      <Row name="Beim Speichern sichern" desc="Etwa 5 Sekunden nach jeder Änderung"><input type="checkbox" checked={g.autoCommitOnSave} onChange={(e) => set('autoCommitOnSave', e.target.checked)} /></Row>
      <Row name="Takt (Sekunden)"><input className="text-input" type="number" style={{ width: 90 }} value={g.intervalSec} onChange={(e) => set('intervalSec', Number(e.target.value))} /></Row>
      <Row name="Git-LFS-Muster" desc="Durch Leerzeichen getrennt, wird über LFS geführt">
        <input className="text-input" style={{ width: 260 }} value={(g.lfsPatterns || []).join(' ')} onChange={(e) => set('lfsPatterns', e.target.value.split(/\s+/).filter(Boolean))} />
      </Row>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
        <button className="primary" onClick={save}>Speichern</button>
        <button className="btn secondary" onClick={() => run(api.gitInit, 'Init')}>Repo anlegen</button>
        <button className="btn secondary" onClick={() => run(api.gitClone, 'Clone')}>Klonen</button>
        <button className="btn secondary" onClick={() => run(api.gitPull, 'Pull')}>Holen</button>
        <button className="btn secondary" onClick={() => run(() => api.gitCommit(), 'Commit')}>Sichern</button>
        <button className="btn secondary" onClick={() => run(api.gitPush, 'Push')}>Hochladen</button>
        <button className="btn" onClick={() => run(() => api.gitSync(), 'Sync')}>Jetzt abgleichen</button>
      </div>
      <div style={{ marginTop: 12 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
          <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>Abgleich-Protokoll</span>
          {log.length > 0 && (
            <button className="btn secondary" style={{ padding: '2px 8px' }} onClick={() => setLog([])}>Leeren</button>
          )}
        </div>
        <textarea
          ref={logRef}
          readOnly
          value={log.length ? log.join('\n') : 'No git activity yet. Click an action above (Sync now, Pull, Push…) to see logs here.'}
          style={{
            width: '100%', height: 200, boxSizing: 'border-box', resize: 'vertical',
            background: 'var(--bg-primary)', color: 'var(--text-normal)',
            border: '1px solid var(--bg-modifier-border, #444)', borderRadius: 6, padding: 10,
            fontFamily: 'var(--font-monospace, monospace)', fontSize: 12, lineHeight: 1.5, whiteSpace: 'pre',
          }}
        />
      </div>
    </div>
  );
}

function ApiKeys() {
  const [keys, setKeys] = useState<any[]>([]);
  const [name, setName] = useState('my-agent');
  const [scopes, setScopes] = useState<string[]>(['read', 'search']);
  const [created, setCreated] = useState('');
  const load = () => api.listKeys().then((r) => setKeys(r.keys)).catch(() => {});
  useEffect(() => { load(); }, []);
  const toggle = (sc: string) => setScopes((p) => (p.includes(sc) ? p.filter((x) => x !== sc) : [...p, sc]));
  const create = async () => {
    const r = await api.createKey(name, scopes);
    setCreated(r.key);
    await load();
  };
  return (
    <div>
      <h2>API-Schlüssel</h2>
      <p style={{ color: 'var(--text-muted)' }}>Keys let AI agents call <code>/api/v1</code>. The raw key is shown once.</p>
      <Row name="Name"><input className="text-input" value={name} onChange={(e) => setName(e.target.value)} /></Row>
      <Row name="Scopes">
        <span>
          {['read', 'write', 'search'].map((sc) => (
            <label key={sc} style={{ marginRight: 10 }}>
              <input type="checkbox" checked={scopes.includes(sc)} onChange={() => toggle(sc)} /> {sc}
            </label>
          ))}
        </span>
      </Row>
      <button className="btn" onClick={create}>Schlüssel erzeugen</button>
      {created && (
        <pre style={{ background: 'var(--bg-primary)', padding: 10, borderRadius: 6, marginTop: 10, wordBreak: 'break-all', whiteSpace: 'pre-wrap' }}>
          {created}
          {'\n'}⚠ Copy now — it will not be shown again.
        </pre>
      )}
      <div style={{ marginTop: 16 }}>
        {keys.map((k) => (
          <div className="setting-row" key={k.id}>
            <div className="info">
              <div className="name">{k.name} <span style={{ color: 'var(--text-faint)' }}>{k.prefix}…</span></div>
              <div className="desc">scopes: {k.scopes.join(', ')} · used: {k.lastUsed ?? 'never'}</div>
            </div>
            <button className="btn danger" onClick={async () => { await api.revokeKey(k.id); load(); }}>Widerrufen</button>
          </div>
        ))}
      </div>
    </div>
  );
}

function Appearance({ s }: { s: any }) {
  const [theme, setTheme] = useState(s.ui.theme);
  const [stil, setStil] = useState<string>(s.ui.rainbowStyle ?? '');
  const [deckkraft, setDeckkraft] = useState<number>(s.ui.rainbowOpacity || 1);
  const save = async (t: string) => { setTheme(t); await api.putSettings({ ui: { theme: t } }); location.reload(); };
  /** Speichern und sofort anwenden — Farben will man sehen, nicht beschreiben. */
  const farbenSpeichern = async (next: { rainbowStyle?: string; rainbowOpacity?: number }) => {
    await api.putSettings({ ui: next });
    const info = await api.appearance().catch(() => null);
    if (info) applyRainbow(info.rainbow);
  };
  return (
    <div>
      <h2>Aussehen</h2>
      <Row name="Theme">
        <select className="text-input" value={theme} onChange={(e) => save(e.target.value)}>
          <option value="dark">Dunkel</option>
          <option value="light">Hell</option>
        </select>
      </Row>
      <Row
        name="Farbige Ordner"
        desc="Ohne eigene Wahl gilt, was im Vault eingestellt ist (Theme über Style Settings)."
      >
        <select
          className="text-input"
          value={stil}
          onChange={(e) => { setStil(e.target.value); void farbenSpeichern({ rainbowStyle: e.target.value }); }}
        >
          <option value="">Wie im Vault</option>
          <option value="off">Aus</option>
          <option value="default">Nur der Pfeil</option>
          <option value="simple">Pfeil und Name</option>
          <option value="full">Ganze Fläche</option>
        </select>
      </Row>
      <Row name="Deckkraft der Fläche" desc="Nur für „Ganze Fläche“.">
        <input
          type="range"
          min={0.05}
          max={1}
          step={0.05}
          value={deckkraft}
          onChange={(e) => setDeckkraft(Number(e.target.value))}
          onMouseUp={() => void farbenSpeichern({ rainbowOpacity: deckkraft })}
          onTouchEnd={() => void farbenSpeichern({ rainbowOpacity: deckkraft })}
        />
      </Row>
    </div>
  );
}

function AssistantSettings({ s, reload }: { s: any; reload: () => void }) {
  const [a, setA] = useState({ ...s.assistant });
  const [state, setState] = useState('');
  const set = (k: string, v: any) => setA((p: any) => ({ ...p, [k]: v }));

  const save = async () => {
    await api.putSettings({ assistant: a });
    await reload();
    setState('Gespeichert.');
  };
  const test = async () => {
    setState('Wird geprüft…');
    try {
      const r = await api.assistantChat(1);
      setState(`Erreichbar — ${r.messages.length ? 'Unterhaltung gefunden.' : 'noch keine Unterhaltung.'}`);
    } catch (e: any) {
      setState(e.message || 'Nicht erreichbar');
    }
  };

  return (
    <div>
      <h2>Assistent</h2>
      <Row name="Assistent nutzen" desc="Blendet den Chat in der rechten Leiste ein">
        <input type="checkbox" checked={a.enabled} onChange={(e) => set('enabled', e.target.checked)} />
      </Row>
      <Row name="Adresse" desc="Basis der API, z. B. https://…/api">
        <input className="text-input" style={{ width: 300 }} value={a.baseUrl} onChange={(e) => set('baseUrl', e.target.value)} />
      </Row>
      <Row name="Zugang" desc="Bleibt auf dem Server; maskiert lassen behält den gespeicherten">
        <input className="text-input" type="password" style={{ width: 300 }} value={a.token} onChange={(e) => set('token', e.target.value)} />
      </Row>
      <Row name="Name" desc="Wie er im Chat genannt wird">
        <input className="text-input" style={{ width: 160 }} value={a.name} onChange={(e) => set('name', e.target.value)} />
      </Row>
      <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
        <button className="primary" onClick={save}>Speichern</button>
        <button className="btn secondary" onClick={test}>Verbindung prüfen</button>
      </div>
      {state && <div style={{ marginTop: 10, fontSize: 12, color: 'var(--text-muted)' }}>{state}</div>}
    </div>
  );
}
