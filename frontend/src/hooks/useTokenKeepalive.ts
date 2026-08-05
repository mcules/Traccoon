// Verlängert das Zugangs-Token, solange ein Tab lange offen steht.
//
// ── Warum es das braucht ────────────────────────────────────────────────────────────────────
//
// `jwt_expire_minutes` ist 720, und `src/api.ts` macht bei 401 `setToken(null)` plus harte
// Weiterleitung nach `/login`. Ohne Erneuerung ist der Wandschirm also spätestens nach zwölf
// Stunden ein Anmeldeformular — und davor steht nachts niemand. Der Endpunkt dahinter
// (`POST /auth/refresh`) verlängert ausschließlich eine **bestehende** Sitzung und gibt kein
// neues Recht; er prüft `status` und `password_changed_at` genau wie jeder andere Aufruf.
//
// Der Aufruf geht über `/auth/…` — und genau dieser Pfad ist in `src/api.ts` von der harten
// 401-Weiterleitung ausgenommen. Ein fehlgeschlagener Refresh wirft den Nutzer also nicht
// hinaus; er bleibt einfach beim alten Token, und der nächste echte Aufruf entscheidet.
//
// Nützt jedem lange offenen Tab, nicht nur dem Kiosk — deshalb ein eigener Hook und keine
// Zeile im Büro.

import { useEffect } from "react";
import { api, setToken } from "../api";

/** Alle sechs Stunden. Bei zwölf Stunden Laufzeit ist das zweimal Luft, bevor es eng wird —
 *  eine verpasste Erneuerung (Netz weg, Backend im Neustart) kostet damit nichts. */
export const KEEPALIVE_MS = 6 * 60 * 60 * 1000;

/**
 * Erneuert das Token beim Einhängen und danach im Takt.
 *
 * Auch sofort beim Einhängen: ein Tab, der gerade aus einem elf Stunden alten Token
 * wiederkommt, hätte sonst noch eine Stunde und danach dieselbe Anmeldemaske.
 */
export function useTokenKeepalive(aktiv: boolean, intervallMs: number = KEEPALIVE_MS): void {
  useEffect(() => {
    if (!aktiv) return;
    let entlassen = false;

    const erneuern = async () => {
      try {
        const r = await api.post<{ access_token?: string }>("/auth/refresh");
        if (!entlassen && r?.access_token) setToken(r.access_token);
      } catch {
        // Abgelaufen, Konto deaktiviert oder Backend gerade weg: hier ist nichts zu heilen.
        // Der nächste gewöhnliche Aufruf läuft dann in die reguläre 401-Behandlung.
      }
    };

    void erneuern();
    const timer = window.setInterval(() => { void erneuern(); }, intervallMs);
    return () => { entlassen = true; window.clearInterval(timer); };
  }, [aktiv, intervallMs]);
}
