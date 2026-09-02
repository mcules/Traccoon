/**
 * One place that knows what this app can do.
 *
 * The command palette used to carry its own hard-coded list with invented ids,
 * while keyboard shortcuts lived in two other tables and the slash menu in a
 * third. Anything new had to be added to each of them, and nothing outside
 * those files could contribute at all.
 *
 * Ids follow the predecessor's own (`editor:toggle-bold`, `daily-notes`, …) — not
 * decoration: the vault's `app.json` lists its mobile toolbar by those ids, and
 * `hotkeys.json` binds keys to them. Speaking the same names means those files
 * can be read instead of re-invented.
 */

export interface Command {
  id: string;
  /** What the palette shows. */
  name: string;
  /** Default key combination, in the predecessor's notation (`Mod+B`). */
  hotkey?: string;
  /** Needs a focused editor — hidden elsewhere, and offered in the slash menu. */
  editor?: boolean;
  /** False hides the command right now (nothing open to act on, say). */
  enabled?: () => boolean;
  run: () => void | Promise<void>;
}

const registry = new Map<string, Command>();
const listeners = new Set<() => void>();

function changed() {
  for (const l of listeners) l();
}

export function registerCommand(cmd: Command): void {
  registry.set(cmd.id, cmd);
  changed();
}

export function registerCommands(cmds: Command[]): void {
  for (const c of cmds) registry.set(c.id, c);
  changed();
}

export function unregisterCommand(id: string): void {
  if (registry.delete(id)) changed();
}

/** Every command that makes sense to offer right now. */
export function listCommands(opts: { editorOnly?: boolean } = {}): Command[] {
  const out: Command[] = [];
  for (const c of registry.values()) {
    if (opts.editorOnly && !c.editor) continue;
    if (c.enabled && !c.enabled()) continue;
    out.push(c);
  }
  return out;
}

export function getCommand(id: string): Command | undefined {
  return registry.get(id);
}

/** Run a command by id; false when there is no such command (or it is off). */
export function runCommand(id: string): boolean {
  const cmd = registry.get(id);
  if (!cmd || (cmd.enabled && !cmd.enabled())) return false;
  void cmd.run();
  return true;
}

/** Called whenever the set of commands changes, so UI can re-render. */
export function onCommandsChanged(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/* ------------------------------------------------------------- key notation */

const isApple = typeof navigator !== 'undefined' && /mac|iphone|ipad/i.test(navigator.platform || navigator.userAgent);

/** `Mod+Shift+B` → the string a keydown event produces, for lookup. */
export function normalizeHotkey(hotkey: string): string {
  const parts = hotkey.split('+').map((p) => p.trim());
  const key = parts.pop() ?? '';
  const mods = new Set(parts.map((m) => m.toLowerCase()));
  const out: string[] = [];
  // "Mod" is Cmd on Apple keyboards and Ctrl everywhere else — the predecessor's own
  // convention, and the reason a shortcut file is portable between machines.
  if (mods.has('mod')) out.push(isApple ? 'meta' : 'ctrl');
  if (mods.has('ctrl') && !(mods.has('mod') && !isApple)) out.push('ctrl');
  if (mods.has('meta') || mods.has('cmd')) out.push('meta');
  if (mods.has('alt')) out.push('alt');
  if (mods.has('shift')) out.push('shift');
  return [...new Set(out)].sort().join('+') + '|' + key.toLowerCase();
}

export function eventHotkey(e: KeyboardEvent): string {
  const mods: string[] = [];
  if (e.ctrlKey) mods.push('ctrl');
  if (e.metaKey) mods.push('meta');
  if (e.altKey) mods.push('alt');
  if (e.shiftKey) mods.push('shift');
  return mods.sort().join('+') + '|' + e.key.toLowerCase();
}

/** Human-readable form for the palette, e.g. `⌘B` / `Strg+B`. */
export function displayHotkey(hotkey: string): string {
  return hotkey
    .split('+')
    .map((p) => {
      const k = p.trim().toLowerCase();
      if (k === 'mod') return isApple ? '⌘' : 'Strg';
      if (k === 'shift') return isApple ? '⇧' : 'Umschalt';
      if (k === 'alt') return isApple ? '⌥' : 'Alt';
      if (k === 'ctrl') return isApple ? '⌃' : 'Strg';
      return p.length === 1 ? p.toUpperCase() : p;
    })
    .join(isApple ? '' : '+');
}
