import { useEffect, useMemo, useState } from 'react';
import { tr } from "../../i18n";
import { api } from '../lib/api';
import { useStore } from '../lib/store';
import { displayHotkey, listCommands, normalizeHotkey } from '../lib/commands';
import { loadHotkeys } from '../lib/hotkeys';

type Binding = { modifiers?: string[]; key?: string };

/**
 * Assigning keys to commands.
 *
 * Written back into the vault's own hotkeys.json, so a key learned here works
 * in the desktop app too. Only differences are stored — a command left at its
 * default has no entry, and an empty list means "no key", which is how that
 * file records a removed default.
 */
export default function HotkeySettings() {
  const notify = useStore((s) => s.notify);
  const [custom, setCustom] = useState<Record<string, Binding[]>>({});
  const [filter, setFilter] = useState('');
  const [recording, setRecording] = useState<string | null>(null);

  useEffect(() => {
    api
      .vaultConfig()
      .then((c) => setCustom(c.hotkeys ?? {}))
      .catch(() => {});
  }, []);

  const commands = useMemo(
    () =>
      listCommands()
        .filter((c) => !filter.trim() || c.name.toLowerCase().includes(filter.toLowerCase()) || c.id.includes(filter))
        .sort((a, b) => a.name.localeCompare(b.name, 'de')),
    [filter, custom],
  );

  const save = async (next: Record<string, Binding[]>) => {
    setCustom(next);
    try {
      await api.saveHotkeys(next);
      loadHotkeys(next);
    } catch (e: any) {
      notify(e.message);
    }
  };

  // While recording, the next key press becomes the binding.
  useEffect(() => {
    if (!recording) return;
    const onKey = (e: KeyboardEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (e.key === 'Escape') {
        setRecording(null);
        return;
      }
      if (['Shift', 'Control', 'Alt', 'Meta'].includes(e.key)) return;
      const modifiers: string[] = [];
      if (e.ctrlKey || e.metaKey) modifiers.push('Mod');
      if (e.altKey) modifiers.push('Alt');
      if (e.shiftKey) modifiers.push('Shift');
      void save({ ...custom, [recording]: [{ modifiers, key: e.key.length === 1 ? e.key.toUpperCase() : e.key }] });
      setRecording(null);
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [recording, custom]);

  const shown = (id: string, fallback?: string) => {
    const c = custom[id];
    if (c) {
      if (!c.length || !c[0]?.key) return '—';
      return displayHotkey([...(c[0].modifiers ?? []), c[0].key].join('+'));
    }
    return fallback ? displayHotkey(fallback) : '—';
  };

  /** A key that two commands share is worth seeing before it surprises someone. */
  const clashes = useMemo(() => {
    const seen = new Map<string, string[]>();
    for (const cmd of listCommands()) {
      const c = custom[cmd.id];
      const combo = c?.[0]?.key
        ? normalizeHotkey([...(c[0].modifiers ?? []), c[0].key].join('+'))
        : c
          ? null
          : cmd.hotkey
            ? normalizeHotkey(cmd.hotkey)
            : null;
      if (!combo) continue;
      seen.set(combo, [...(seen.get(combo) ?? []), cmd.id]);
    }
    return new Set([...seen.values()].filter((ids) => ids.length > 1).flat());
  }, [custom]);

  return (
    <div className="setting-section">
      <input
        className="setting-filter"
        placeholder={tr("notes_hotkeys.find_command")}
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      />
      <div className="hotkey-list">
        {commands.map((c) => (
          <div key={c.id} className="hotkey-row">
            <span className="hotkey-name">
              {c.name}
              {clashes.has(c.id) && <span className="hotkey-clash" title={tr("notes_hotkeys.taken_twice")}> ⚠</span>}
            </span>
            <span className="hotkey-id">{c.id}</span>
            <span className="hotkey-combo">{recording === c.id ? tr("notes_hotkeys.press_a_key") : shown(c.id, c.hotkey)}</span>
            <button className="tool-btn" onClick={() => setRecording(c.id)}>
              {tr("notes_menu.change")}
            </button>
            {custom[c.id] && (
              <button
                className="tool-btn"
                title={tr("notes_hotkeys.reset")}
                onClick={() => {
                  const next = { ...custom };
                  delete next[c.id];
                  void save(next);
                }}
              >
                {tr("common.back")}
              </button>
            )}
            <button className="tool-btn" title={tr("notes_hotkeys.no_key")} onClick={() => void save({ ...custom, [c.id]: [] })}>
              Leeren
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
