import { useQuery } from "@tanstack/react-query";
import { pluginApi, type PluginInfo } from "./api";
import type { NavEintrag } from "./nav";

/**
 * Die Plugins, die dieser Mensch sehen darf.
 *
 * Sie stehen in der Bereichsschiene neben den eingebauten Bereichen — ein Plugin soll kein
 * Anhaengsel in einem Untermenue sein, sondern dort auftauchen, wo man Bereiche sucht.
 * Deshalb laedt der Hook frueh und haelt das Ergebnis eine Weile: Eine Navigation, die eine
 * Sekunde nach dem Seitenaufbau um einen Eintrag springt, liest sich wie ein Fehler.
 */
export function usePlugins(): PluginInfo[] {
  const { data } = useQuery({
    queryKey: ["plugins", "meine"],
    queryFn: () => pluginApi.meine(),
    staleTime: 60_000,
  });
  return data ?? [];
}

/** Was die Plugins an Seiten beisteuern, als Eintraege der Bereichsschiene. */
export function pluginNav(plugins: PluginInfo[]): NavEintrag[] {
  const raus: NavEintrag[] = [];
  for (const p of plugins) {
    for (const b of p.contributions || []) {
      if (b.typ !== "seite") continue;
      // Mehrere Seiten eines Plugins landen als Anker hinter derselben Adresse: Der Wirt
      // reicht ihn an das iframe weiter, und das Plugin entscheidet selbst, was es zeigt.
      const anker = (b.pfad || "").replace(/^\//, "");
      raus.push({
        key: `plugin:${p.slug}:${anker}`,
        label: b.label || p.name,
        icon: b.icon || p.icon || "\u{1F9E9}",
        to: anker ? `/p/${p.slug}#${anker}` : `/p/${p.slug}`,
      });
    }
  }
  return raus;
}
