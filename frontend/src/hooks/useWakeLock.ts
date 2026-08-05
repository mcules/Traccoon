// Hält den Bildschirm wach, solange das Büro als Wandschirm läuft.
//
// ── Warum diese Datei hier liegt und nicht in `components/office/` ───────────────────────────
//
// `tools/office-check.mjs` ordnet jede unbekannte `.ts` **direkt** in `office/` fail-closed
// der Schicht 0 zu (`layerOf`), und Schicht 0 muss den Reinheits-Grep bestehen: keine Uhr,
// kein Zufall, **keine Browser-Umgebung**. Ein `navigator.wakeLock` fiele dort durch. Den
// Prüfer dafür aufzuweichen wäre genau der Präzedenzfall, den `PIXEL-CONTRACT.md` vermeiden
// will — also wohnt der Hook außerhalb, in `src/hooks/`.
//
// ── Was der Wake-Lock kann und was nicht ────────────────────────────────────────────────────
//
// Er verhindert, dass der Bildschirm abdunkelt; er startet **kein** Vollbild. Vollbild braucht
// eine Nutzergeste (`requestFullscreen()` scheitert sonst still) — dafür gibt es im Kiosk den
// ⛶-Knopf, und in der Praxis startet die Wand ohnehin als `chromium --kiosk`.
//
// Die Sperre wird vom Browser **automatisch freigegeben**, sobald das Dokument versteckt wird
// (Tabwechsel, Bildschirmsperre). Deshalb genügt es nicht, sie einmal anzufordern: bei jedem
// `visibilitychange` zurück auf sichtbar wird neu angefordert. Ohne das ist der Wandschirm
// nach dem ersten Tabwechsel wieder ein normaler Bildschirm mit Bildschirmschoner.

import { useEffect } from "react";

/** Der Ausschnitt der Wake-Lock-API, den dieser Hook benutzt. Von Hand deklariert statt aus
 *  `lib.dom` gezogen: die Typen sind je nach TypeScript-Fassung vorhanden oder nicht, und ein
 *  Bau, der an der Bibliotheksversion hängt, ist genau die Art Überraschung, die niemand
 *  sucht. Zugegriffen wird über genau **eine** Umdeutung, unten. */
interface WakeLockSentinelLike {
  released: boolean;
  release(): Promise<void>;
}
interface WakeLockLike {
  request(typ: "screen"): Promise<WakeLockSentinelLike>;
}

/**
 * Hält den Bildschirm wach, solange `aktiv` gilt.
 *
 * Fehler sind hier kein Fehler: `request()` wirft in unsicheren Kontexten (kein HTTPS), wenn
 * die Berechtigung fehlt oder das Dokument gerade nicht sichtbar ist. Ein Wandschirm, der
 * deswegen eine Ausnahme in die Konsole schreibt, ist immer noch ein funktionierender
 * Wandschirm — ein Wandschirm, der deswegen weiß wird, nicht.
 */
export function useWakeLock(aktiv: boolean): void {
  useEffect(() => {
    if (!aktiv) return;
    const wl = (navigator as unknown as { wakeLock?: WakeLockLike }).wakeLock;
    if (!wl) return;                              // Merkmalsprüfung: Firefox/Safari-Altstand

    let entlassen = false;
    let sperre: WakeLockSentinelLike | null = null;

    const anfordern = async () => {
      if (entlassen || sperre !== null || document.visibilityState !== "visible") return;
      try {
        sperre = await wl.request("screen");
        // Der Effekt kann während des `await` abgeräumt worden sein — dann gehört die
        // frisch geholte Sperre niemandem mehr und wird sofort wieder abgegeben.
        if (entlassen) { void sperre.release().catch(() => {}); sperre = null; }
      } catch {
        sperre = null;
      }
    };

    const beiSichtbarkeit = () => {
      if (document.visibilityState === "visible") {
        sperre = null;                            // beim Verstecken hat der Browser sie gelöst
        void anfordern();
      }
    };

    void anfordern();
    document.addEventListener("visibilitychange", beiSichtbarkeit);
    return () => {
      entlassen = true;
      document.removeEventListener("visibilitychange", beiSichtbarkeit);
      void sperre?.release().catch(() => {});
      sperre = null;
    };
  }, [aktiv]);
}
