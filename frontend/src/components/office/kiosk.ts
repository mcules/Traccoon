// Schicht 0 — die Kamerawahl des Wandschirms.
//
// ══ Die Einsicht, auf der das hier beruht ════════════════════════════════════════════════════
//
// Die Kamera braucht keine Identität, sondern einen **Punkt**. Und `Frame.fx` ist bereits der
// vollständige Geschehensstrom des Raums: jedes `tool` erzeugt einen `spark`, jedes `edit` einen
// `drop`, jedes `spawn`/`deliver` ein `link`, jedes `gate`/`done` ein `emote`. Die Engine hält
// die Liste außerdem schon sauber — `tick` wirft alles weg, dessen `until` vorbei ist.
//
// Deshalb kostet der Kiosk **null Umbau an der Engine**: kein neues Feld in `ActorState`, kein
// `lastAct`, das aus `Priv` nach außen gehoben werden müsste. Jedes solche Feld stünde im
// `Frame` und machte `golden.json` rot — für eine Information, die im `fx`-Strom ohnehin steht.
//
// Alle Zeiten sind `engine.t` (Simulationszeit, nicht Wanduhr), alle Koordinaten sind
// **Szenen**-Koordinaten (`SCENE`, 1600×900) — genau wie `Fx.x/y`. Das Umrechnen in Pufferpixel
// (`POS_SCALE`) ist Sache der Bühne und passiert bewusst nicht hier: sonst gäbe es zwei Stellen,
// an denen dieselbe Skalierung steht.

import type { Frame, Fx, FxKind, Pt } from "./types.ts";
import { KIOSK_HOLD_MS, KIOSK_IDLE_MS, SCENE } from "./const.ts";

/**
 * Was der Kiosk zwischen zwei Bildern behalten muss.
 *
 * `x`/`y`/`zoom` sind das **Soll** der Kamera, nicht ihr Ist — die Bühne fährt mit ihrer
 * vorhandenen Easing dorthin. `pickedAt` trägt die Halte-Regel, `lastFxT0` ist zugleich
 * Entprellung (ein Fx wird nie zweimal gewählt) und Aktivitätsuhr (die Stille-Regel misst
 * daran).
 */
export interface KioskCam {
  x: number;
  y: number;
  zoom: number;
  pickedAt: number;
  lastFxT0: number;
}

/**
 * Feste Rangfolge, **kein Punktesystem**. Ein Punktesystem bräuchte Gewichte, und die könnte
 * niemand begründen — „ein Fehler zählt 2,5 Funken" ist eine erfundene Zahl. Die Reihenfolge
 * hier ist dagegen eine Aussage über den Raum: `emote` (Gate oder Abschluss) ist das Seltenste
 * und Wichtigste, `link` (Übergabe, Spawn) die eigentliche Choreografie, `drop` (geschriebene
 * Datei) das Ergebnis, `spark` (Werkzeugschritt) das Alltäglichste.
 */
const RANK: Record<FxKind, number> = { emote: 3, link: 2, drop: 1, spark: 0 };

/** Der ganze Raum: die Mitte von `SCENE`. Mal `POS_SCALE` ist das exakt `CAM_FULL` — das steht
 *  aber in Schicht 1 und ist von hier aus zu Recht unerreichbar. */
const FULL: Pt = { x: SCENE.w / 2, y: SCENE.h / 2 };

/** Frischer Kiosk-Zustand: ganzer Raum, noch nichts gesehen.
 *
 *  `pickedAt` liegt eine volle Haltezeit in der Vergangenheit und `lastFxT0` **vor** dem
 *  Nullpunkt: die erste Wahl soll nicht sechs Sekunden lang auf einen Raum warten, in dem
 *  gerade etwas anfängt, und ein Fx bei `t0 === 0` ist ein Fx wie jedes andere. */
export function newKioskCam(): KioskCam {
  return { x: FULL.x, y: FULL.y, zoom: 1, pickedAt: -KIOSK_HOLD_MS, lastFxT0: -1 };
}

/** Zurück auf den ganzen Raum. Gibt `null`, wenn die Kamera dort schon steht — sonst meldete
 *  der Kiosk in jedem Bild eine „Änderung" und die Bühne malte durchgehend neu. */
function toFull(st: KioskCam, t: number): Pt | null {
  if (st.zoom === 1 && st.x === FULL.x && st.y === FULL.y) return null;
  st.x = FULL.x;
  st.y = FULL.y;
  st.zoom = 1;
  st.pickedAt = t;
  return { x: st.x, y: st.y };
}

/**
 * Wohin die Kiosk-Kamera schauen soll — oder `null`, wenn alles bleibt, wie es ist.
 *
 * `st` wird dabei **fortgeschrieben** (Halte- und Entprell-Zustand). Das ist kein versteckter
 * Seiteneffekt, sondern die Aufgabe: `st` ist der Kamerazustand, und der Rückgabewert sagt der
 * Bühne nur, ob sie ihn diesmal übernehmen muss. `st.zoom` gehört zum Ergebnis dazu.
 *
 * Die Regeln, in genau dieser Reihenfolge:
 *
 *  1. **Leerer Raum** (keine Figur mit `retired !== true`): gar nicht wählen, ganzer Raum.
 *  2. **Halten** — ein gewähltes Ziel gilt `KIOSK_HOLD_MS` unverändert. Ohne das zappelte die
 *     Kamera zwölfmal je Sekunde zwischen Funken hin und her.
 *  3. **Wählen** — unter allen neuen `fx` nach `RANK`, innerhalb einer Art das jüngste `t0`.
 *  4. **Stille** — `KIOSK_IDLE_MS` ohne ein einziges neues Fx: zurück auf den ganzen Raum und
 *     dort bleiben. Das ist das ehrliche Bild — es passiert nichts, also sieht man den ganzen
 *     stillen Raum und nicht einen zufälligen leeren Schreibtisch aus der Nähe.
 */
export function pickTarget(f: Frame, st: KioskCam): Pt | null {
  const t = f.t;

  // Die Simulationszeit kann **rückwärts springen**: ein Seek auf der Zeitleiste oder ein neu
  // gebauter `Replay` (Raumwechsel im Kiosk) fängt wieder vorn an. Dann sind `t - pickedAt` und
  // `t - lastFxT0` negativ und die Kamera hielte für immer still. Das Halten eines Ziels aus
  // einer anderen Zeitlinie wäre ohnehin sinnlos — also alles fallen lassen und im selben Bild
  // neu wählen.
  if (t < st.pickedAt || t < st.lastFxT0) {
    st.pickedAt = t - KIOSK_HOLD_MS;
    st.lastFxT0 = -1;
  }

  // 1. Leerer Raum.
  let peopled = false;
  for (const a of f.actors) {
    if (a.retired !== true) { peopled = true; break; }
  }
  if (!peopled) return toFull(st, t);

  // Ein Durchgang durch den Strom: bester Kandidat **und** jüngstes gesehenes `t0`.
  let best: Fx | undefined;
  let newest = st.lastFxT0;
  for (const fx of f.fx) {
    if (fx.t0 <= st.lastFxT0) continue;
    if (fx.t0 > newest) newest = fx.t0;
    if (best === undefined) { best = fx; continue; }
    const r = RANK[fx.kind];
    const rb = RANK[best.kind];
    if (r > rb || (r === rb && fx.t0 > best.t0)) best = fx;
  }
  // Auch während des Haltens fortgeschrieben: `lastFxT0` ist die Aktivitätsuhr. Sonst zählte
  // ein sechs Sekunden langes Halten als Stille, sobald es zufällig ins 20-Sekunden-Fenster
  // fällt — und die Kamera zöge sich aus einem vollen Raum zurück.
  st.lastFxT0 = newest;

  // 2. Halten.
  if (t - st.pickedAt < KIOSK_HOLD_MS) return null;

  if (best === undefined) {
    // 4. Stille.
    if (t - st.lastFxT0 >= KIOSK_IDLE_MS) return toFull(st, t);
    return null;
  }

  // 3. Wählen. Zoom 2, nicht 3: der Puffer zeigt dann 240×135, also den halben Raum — die
  // Nachbarn des Ziels bleiben im Bild, und eine Übergabelinie läuft nicht aus dem Bild hinaus.
  st.x = best.x;
  st.y = best.y;
  st.zoom = 2;
  st.pickedAt = t;
  return { x: st.x, y: st.y };
}
