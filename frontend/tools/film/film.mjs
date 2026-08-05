// Der Film: Ereignisse rein, GIF-Bytes raus.
//
// Die Verdrahtung ist kurz, weil jedes Stück schon existiert: `Recorder` dedupliziert und
// übersetzt (`mapEvent`), `Replay` spielt ab, `renderFrame` malt, `raster.mjs` klippt,
// `gif.mjs` kodiert. Neu ist nur, **welche** Zeitpunkte gemalt werden — das entscheidet
// `schnitt.mjs`.
//
// Zwei Regeln, die diese Datei trägt und die man leicht verletzt:
//
//  1. **Ein einziger `Replay` für den ganzen Tag.** `advance()` läuft auch durch die nicht
//     gezeigten Strecken; nur so sitzen die Figuren aus Kapitel 3 noch dort, wo Kapitel 5 sie
//     erwartet. Teuer ist das nicht: `settle()` klemmt jede Lücke auf `MAX_GAP_MS`, eine
//     übersprungene Stunde kostet also höchstens 20 s Simulationszeit.
//  2. **Nie `frameAt` in der Schleife.** Das baut jedes Mal eine neue Engine und spult von
//     vorn — bei 300 Bildern über 2500 Ereignissen wären das Milliarden Aktorschritte statt
//     Zehntausenden.
//
// Und die dritte, die man gar nicht sieht: **keine Zeitzonenbibliothek**. Die Uhr rechnet
// ganzzahlig aus `ts + tz_offset_min·60000`. Python kennt den Versatz des Tages und schickt ihn
// mit; `toLocale*` oder `Intl` machten das Bild von der ICU-Version des Basis-Images abhängig.

import { PIX } from "../../src/components/office/const.ts";
import { Recorder } from "../../src/components/office/recorder.ts";
import { Replay } from "../../src/components/office/replay.ts";
import { CAM_FULL, renderFrame } from "../../src/components/office/pixel/scene.ts";
import { bildplan } from "./schnitt.mjs";
import { hudZeile, kapitelKarte } from "./hud.mjs";
import { rasterCtx } from "./raster.mjs";
import { gif } from "./gif.mjs";

/** Wie viele Bilder eine Trennkarte stehen bleibt (bei 12 fps ein Drittel einer Sekunde).
 *  Weniger und man liest die Uhrzeit nicht, mehr und acht Karten fressen ein Sechstel des Films. */
const KARTEN_BILDER = 4;
/** Unter so vielen Bildern ist ein Kapitel kein Kapitel mehr, sondern ein Zucken. */
const MIN_BILDER = 6;

/**
 * Baut den Film. `auftrag` ist der Rumpf von `POST /film`, Feld für Feld.
 *
 * Rückgabe: die GIF-Bytes und die Zahlen, die als `X-Film-*` in die Antwort gehen — Python
 * schreibt daraus die Bildunterschrift („8 von 67 Szenen").
 */
export function baueFilm(auftrag) {
  const t0 = Date.now();
  const events = Array.isArray(auftrag.events) ? auftrag.events : [];
  const grade = auftrag.grade === "day" ? "day" : "night";
  const fps = zahl(auftrag.fps, 12);
  const sekunden = zahl(auftrag.sekunden, 25);
  const versatz = zahl(auftrag.tz_offset_min, 0);
  const titel = typeof auftrag.titel === "string" ? auftrag.titel : "";

  const rec = new Recorder();
  // Der Roster wird aus den `run_start`-Zeilen **nachgebaut**, statt ihn mitschicken zu lassen:
  // dieselben Felder stehen schon im Ereignis (Rolle, Phase, Modell, Elternlauf), und ohne sie
  // hätte jede Figur die leere Rolle — alle sähen gleich aus (die Rolle bestimmt Hemd, Haar und
  // Torso) und die Übergabe am Laufende fiele aus, weil `mapEvent` den Elternlauf nur aus dem
  // Roster kennt. Ein zweites Feld im Vertrag wäre dafür überflüssig.
  rec.setRoster(rosterAus(events));
  for (const ev of events) rec.push(ev);

  const log = rec.entries();
  const grenzen = rec.bounds();
  if (log.length === 0) return null;

  const plan = bildplan(log, {
    fps, sekunden, kapitel: zahl(auftrag.kapitel, 8),
    minBilder: MIN_BILDER, kartenBilder: KARTEN_BILDER,
  });
  if (plan.bilder.length === 0) return null;

  const marken = sitzungsMarken(events);
  const replay = new Replay(log);
  const { ctx, buf, reset } = rasterCtx(PIX.w, PIX.h);
  const bilder = [];

  // Der erste Sprung ist ein `seek`, kein `advance`: ein frischer `Replay` steht auf dem
  // Anfang, hat aber noch **kein** Ereignis angewandt, und `advance(0)` ist ein Nichtstun.
  // Ohne diese Zeile zeigte das erste Bild jedes Kapitels einen leeren Raum.
  replay.seek(plan.bilder[0].ts);

  let karteIdx = -1;
  let karteLauf = 0;
  for (const b of plan.bilder) {
    const dt = b.ts - replay.position;
    if (dt > 0) replay.advance(dt);

    reset();
    const frame = replay.frame();
    renderFrame(ctx, frame, CAM_FULL, grade);

    const zeit = uhrzeit(b.ts, versatz);
    hudZeile(ctx, grade, zeile(zeit, marken, b.ts, frame));

    if (b.kapitel === null) {
      karteIdx = -1;
      karteLauf = 0;
    } else {
      if (b.kapitel !== karteIdx) { karteIdx = b.kapitel; karteLauf = 0; }
      kapitelKarte(ctx, grade, titel, zeit, blende(karteLauf, KARTEN_BILDER));
      karteLauf++;
    }

    // Der Rasterer schreibt in **denselben** Puffer; ohne Kopie enthielte das GIF 300-mal das
    // letzte Bild. Der Fehler sieht aus wie ein Encoder-Fehler und ist keiner.
    bilder.push(buf.slice());
  }

  const verzoegerung = Math.max(20, Math.round(1000 / (fps > 0 ? fps : 12)));
  const kodiert = gif(bilder, {
    w: PIX.w, h: PIX.h,
    delaysMs: bilder.map(() => verzoegerung),
    loop: 0,
  });

  return {
    bytes: kodiert.bytes,
    kapitel: plan.kapitel.length,
    inseln: plan.kapitel.length + plan.uebersprungen,
    bilder: bilder.length,
    gekappt: plan.gekappt || grenzen.dropped,
    dauerMs: Date.now() - t0,
  };
}

// ── Zeit, ganzzahlig ─────────────────────────────────────────────────────────

/** `ts + Versatz` → `HH:MM:SS`. Reine Ganzzahlrechnung, kein `Date`, kein `Intl`.
 *  Der Versatz ist für den ganzen Tag fest — Python hat ihn gerechnet, weil nur Python weiß,
 *  in welcher Zone der Zuschauer sitzt und ob an diesem Tag die Uhr umgestellt wurde. */
export function uhrzeit(ms, versatzMin) {
  const t = Math.floor(ms) + Math.round(versatzMin) * 60000;
  let s = Math.floor(t / 1000) % 86400;
  if (s < 0) s += 86400;
  return p2((s / 3600) | 0) + ":" + p2(((s % 3600) / 60) | 0) + ":" + p2(s % 60);
}

function p2(n) {
  return n < 10 ? "0" + n : String(n);
}

// ── Die HUD-Zeile ────────────────────────────────────────────────────────────

/** Uhrzeit · Sitzung bzw. Ticket · Zahl der Figuren im Raum.
 *
 *  Die Zahl steht ohne Wort da: jede Beschriftung wäre Sprache, und Sprache baut in diesem
 *  Feature ausschließlich Python (die Bildunterschrift sagt ohnehin, was der Film zeigt). */
function zeile(zeit, marken, ts, frame) {
  let leute = 0;
  for (const a of frame.actors) if (a.retired !== true) leute++;
  const wo = markeBei(marken, ts);
  return wo ? `${zeit} | ${wo} | ${leute}` : `${zeit} | ${leute}`;
}

/** Beschriftungswechsel über den Tag: ein Eintrag je Ereignis, aufsteigend nach `ts`.
 *
 *  Nötig, weil `LogEntry` die Sitzung nicht trägt — der Recorder übersetzt nach Kommandos, und
 *  Kommandos kennen nur Figuren. Ein Tag enthält viele Sitzungen; ohne diese Spur zeigte die
 *  Zeile den ganzen Film über dasselbe Ticket. */
function sitzungsMarken(events) {
  const namen = new Map();
  for (const ev of events) {
    const key = typeof ev.issue_key === "string" && ev.issue_key.length > 0 ? ev.issue_key : null;
    if (key !== null && !namen.has(ev.sid)) namen.set(ev.sid, key);
  }
  const out = [];
  for (const ev of events) {
    const at = Date.parse(ev.ts);
    if (!Number.isFinite(at)) continue;
    out.push({ ts: at, text: namen.get(ev.sid) ?? ev.sid ?? "" });
  }
  out.sort((a, b) => a.ts - b.ts);
  return out;
}

/** Die letzte Marke mit `ts <= at`. Linear gesucht: die Bilder kommen aufsteigend, aber ein
 *  Zeiger über zwei Datenreihen wäre eine Zustandsvariable mehr für dieselbe Antwort. */
function markeBei(marken, at) {
  let text = marken.length > 0 ? marken[0].text : "";
  for (const m of marken) {
    if (m.ts > at) break;
    text = m.text;
  }
  return text;
}

// ── Kleinkram ────────────────────────────────────────────────────────────────

/** Ein- und Ausblenden der Trennkarte: das erste und das letzte Bild halb, dazwischen voll.
 *  Ein längerer Verlauf verschenkte bei vier Bildern nur Lesezeit. */
function blende(i, m) {
  if (m <= 1) return 1;
  return i === 0 || i === m - 1 ? 0.5 : 1;
}

function zahl(v, ersatz) {
  return typeof v === "number" && Number.isFinite(v) && v > 0 ? v : ersatz;
}

/** Der Roster aus den `run_start`-Zeilen. Nur die fünf Felder, die `mapEvent` wirklich liest —
 *  ein vollständiger `RosterEntry` wäre hier eine Erfindung, keine Vollständigkeit. */
function rosterAus(events) {
  const out = [];
  const gesehen = new Set();
  for (const ev of events) {
    if (ev.kind !== "run_start" || gesehen.has(ev.agent_id)) continue;
    gesehen.add(ev.agent_id);
    out.push({
      agent_id: ev.agent_id,
      run_id: ev.run_id,
      agent: typeof ev.agent === "string" ? ev.agent : "",
      phase: ev.phase ?? null,
      issue_key: ev.issue_key ?? null,
      model: ev.model ?? null,
      parent_run_id: ev.parent_run_id ?? null,
    });
  }
  return out;
}
