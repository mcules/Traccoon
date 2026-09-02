import { eventHotkey, listCommands, normalizeHotkey, onCommandsChanged } from './commands';

/**
 * Key combinations, resolved against the command registry.
 *
 * Each command carries its own default, and the vault's `hotkeys.json` can
 * override any of them — the same file the desktop app reads, so a key learned
 * in one place works in the other. An empty list for a command means "no key",
 * which is how that file records a removed default.
 */

let table = new Map<string, string>(); // normalized key → command id
let overrides: Record<string, Array<{ modifiers?: string[]; key?: string }>> = {};

function rebuild() {
  const next = new Map<string, string>();
  for (const cmd of listCommands()) {
    const custom = overrides[cmd.id];
    if (custom) {
      for (const k of custom) {
        if (!k?.key) continue;
        next.set(normalizeHotkey([...(k.modifiers ?? []), k.key].join('+')), cmd.id);
      }
      continue; // an entry in the file replaces the default outright
    }
    if (cmd.hotkey) next.set(normalizeHotkey(cmd.hotkey), cmd.id);
  }
  table = next;
}

/** Feed the vault's own bindings in; safe to call again when they change. */
export function loadHotkeys(fromVault: Record<string, Array<{ modifiers?: string[]; key?: string }>>): void {
  overrides = fromVault ?? {};
  rebuild();
}

onCommandsChanged(rebuild);

/** The command a keydown should run, if any. */
export function hotkeyCommand(e: KeyboardEvent): string | undefined {
  return table.get(eventHotkey(e));
}
