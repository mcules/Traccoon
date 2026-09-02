import { api } from './api';

/**
 * Load the vault's enabled CSS snippets into the page.
 *
 * They are appended last so they can override the interface's own rules — which
 * is the point of a snippet. Served from this origin, so the strict script/style
 * policy is untouched.
 */
export async function loadVaultSnippets(): Promise<string[]> {
  const info = await api.appearance().catch(() => null);
  if (!info) return [];
  applyRainbow(info.rainbow);
  for (const name of info.enabledSnippets) {
    const id = `vault-snippet-${name}`;
    if (document.getElementById(id)) continue;
    const link = document.createElement('link');
    link.id = id;
    link.rel = 'stylesheet';
    link.href = `/api/notes/settings/snippet/${encodeURIComponent(name)}.css`;
    document.head.appendChild(link);
  }
  return info.enabledSnippets;
}

/**
 * Coloured folders, switched on the way the vault has them.
 *
 * The style is a class on the app's root and the fill strength a variable, so
 * the whole thing is CSS from there on: no per-row work, and a tree of six
 * thousand notes costs nothing to colour.
 */
export function applyRainbow(r: {
  style: 'off' | 'default' | 'simple' | 'full';
  opacity: number;
  files: boolean;
  inheritSubfolders: boolean;
}): void {
  const el = document.documentElement;
  for (const s of ['default', 'simple', 'full']) el.classList.toggle(`rainbow-${s}`, r.style === s);
  el.classList.toggle('rainbow-files', r.style !== 'off' && r.files);
  el.classList.toggle('rainbow-inherit', r.style !== 'off' && r.inheritSubfolders);
  el.style.setProperty('--rainbow-opacity', String(r.opacity));
}
