import { useQuery } from "@tanstack/react-query";
import { pluginApi, type PluginInfo } from "./api";
import { tr } from "./i18n";
import type { NavEntry } from "./nav";

/**
 * The plugins this person may see.
 *
 * They stand in the area rail next to the built-in areas — a plugin should be no appendage in a
 * submenu but turn up where one looks for areas. That is why the hook loads early and holds the
 * result for a while: a navigation that jumps by one entry a second after the page is built
 * reads like a fault.
 */
export function usePlugins(): PluginInfo[] {
  const { data } = useQuery({
    queryKey: ["plugins", "meine"],
    queryFn: () => pluginApi.my(),
    staleTime: 60_000,
  });
  return data ?? [];
}

/**
 * What the plugins contribute as pages, as entries of the area rail.
 *
 * A label from a manifest goes through `tr` like every other text: a plugin that wants to be
 * translated names a key (`agentshield.title`), and one that does not simply keeps its
 * words — `tr` gives back what it does not know.
 */
export function pluginNav(plugins: PluginInfo[]): NavEntry[] {
  const out: NavEntry[] = [];
  for (const p of plugins) {
    for (const b of p.contributions || []) {
      if (b.type !== "page") continue;
      // Several pages of a plugin land as anchors behind the same address: the host
      // passes it on to the iframe, and the plugin decides itself what it shows.
      const anchor = (b.path || "").replace(/^\//, "");
      out.push({
        key: `plugin:${p.slug}:${anchor}`,
        label: tr(b.label || p.name),
        icon: b.icon || p.icon || "\u{1F9E9}",
        to: anchor ? `/p/${p.slug}#${anchor}` : `/p/${p.slug}`,
      });
    }
  }
  return out;
}
