// Der Schnitt — aus einem ganzen Bürotag werden 300 Bilder.
//
// Der Satz, auf dem alles steht, ist eine Eigenschaft von `replay.ts::settle()` und keine
// Vermutung: die Simulationszeit zwischen zwei Ereignissen ist `min(MAX_GAP_MS, ts - anchor)`.
// Sobald `ts - anchor` die Grenze erreicht, sättigt der Wert, `want === spent`, und jedes
// weitere Vorrücken integriert **null**. Ab 20 s nach dem letzten Ereignis ist damit jedes Bild
// bitgleich mit dem vorigen — Stille zu überspringen verliert beweisbar nichts. Aus 14 Stunden
// Wanduhr werden so 20–80 Minuten Simulationszeit.
//
// Was daraus **nicht** folgt: eine feste Bildrate. 45 Minuten auf 25 s bei 12 fps sind 2,25 s
// Simulationszeit je Bild; ein Gang durch den Raum (rund 3 s) bekäme ein einziges Bild und die
// Figuren sprängen von Platz zu Platz. Deshalb Kapitel: wenige Inseln in Echtzeit statt aller
// Inseln im Zeitraffer.

import { MAX_GAP_MS, REPLAY_CAP } from "../../src/components/office/const.ts";

/** Statuswerte, die als Fehlschlag zählen — dieselbe Zusammenfassung wie in der Personalakte
 *  (`loop_exhausted` ist ein Abbruch, kein eigener Zustand für den Zuschauer). */
const FEHLER_STATUS = new Set(["failed", "loop_exhausted"]);

/**
 * Aktivitätsinseln: zusammenhängende Zeitfenster, zwischen denen mehr als `luecke` Stille liegt.
 *
 * `bis` ist bewusst **nicht** der letzte Zeitstempel, sondern `letztes ts + luecke`: bis dorthin
 * bewegt sich der Raum noch (der Gang zum Tisch endet, die Blase läuft ab), erst danach steht
 * er nachweislich still. Wer bei `letztes ts` abschneidet, schneidet mitten in der Bewegung ab.
 *
 * Das Log wird hier — und nur hier — nach `ts` sortiert. Für den Replay wäre das ein Fehler
 * (er setzte die Wirkung vor die Ursache), gemessen wird aber nichts abgespielt: gesucht sind
 * die Zeitfenster, in denen überhaupt etwas passiert ist. Die Kopie schützt den Aufrufer.
 */
export function inseln(log, luecke) {
  const spalte = luecke > 0 ? luecke : MAX_GAP_MS;
  const nachZeit = log.slice().sort((a, b) => (a.ts - b.ts) || (a.seq - b.seq));

  const out = [];
  let cur = null;
  for (const e of nachZeit) {
    if (cur === null || e.ts - cur.bis > spalte) {
      cur = { von: e.ts, bis: e.ts, ereignisse: 0, agenten: new Set(), fehler: 0, gates: 0, gewicht: 0 };
      out.push(cur);
    }
    cur.bis = e.ts;
    cur.ereignisse++;
    for (const c of e.cmds) zaehle(cur, c);
  }

  for (const i of out) {
    i.bis += spalte;
    // Deterministisch und ohne Stellschraube: Fehler wiegen am schwersten (sie erklären den
    // Tag), Gates danach (ein wartender Raum ist die häufigste Ursache für Stille), die Zahl
    // der Beteiligten macht ein Bild voll, und die schiere Ereignismenge geht nur logarithmisch
    // ein — sonst gewönne jede lange Werkzeugkette gegen jeden interessanten Moment.
    i.gewicht = 3 * i.fehler + 2 * i.gates + i.agenten.size + 0.5 * Math.log(1 + i.ereignisse);
  }
  return out;
}

function zaehle(insel, c) {
  if (typeof c.id === "string") insel.agenten.add(c.id);
  if (c.k === "toolEnd" && c.ok === false) insel.fehler++;
  else if (c.k === "done" && c.ok === false) insel.fehler++;
  else if (c.k === "status" && FEHLER_STATUS.has(c.status)) insel.fehler++;
  else if (c.k === "deploy" && c.state === "fail") insel.fehler++;
  else if (c.k === "gate") insel.gates++;
}

/**
 * Der Bildplan: welcher Zeitpunkt bekommt welches Bild.
 *
 * `kapitel` steht hier zusätzlich zu den vier im Vertrag genannten Optionen, weil die Zahl der
 * Kapitel aus dem HTTP-Auftrag kommt und die Budgetrechnung sie braucht.
 *
 * Rückgabe: `bilder[]` in Abspielreihenfolge (`kapitel` = Nummer der Trennkarte, `null` =
 * gewöhnliches Bild), die gewählten `kapitel[]`, die Zahl der **nicht** gezeigten Inseln und
 * `gekappt`.
 */
export function bildplan(log, opts) {
  const fps = opts.fps > 0 ? opts.fps : 12;
  const sekunden = opts.sekunden > 0 ? opts.sekunden : 25;
  const kartenBilder = opts.kartenBilder >= 0 ? opts.kartenBilder : 4;
  const minBilder = opts.minBilder > 0 ? opts.minBilder : 6;
  const wunsch = opts.kapitel > 0 ? Math.floor(opts.kapitel) : 8;

  const budget = Math.max(1, Math.round(sekunden * fps));
  const alle = inseln(log, MAX_GAP_MS);
  const leer = { bilder: [], kapitel: [], uebersprungen: 0, gekappt: log.length >= REPLAY_CAP };
  if (alle.length === 0) return leer;

  // Gleichstand bricht `von` auf: zwei gleich gewichtete Inseln dürfen nicht davon abhängen,
  // wie die Sortierung des Laufzeitsystems gerade schaufelt — sonst ist derselbe Tag zweimal
  // ein anderer Film.
  const rang = alle.slice().sort((a, b) => (b.gewicht - a.gewicht) || (a.von - b.von));

  // Ein Kapitel unter `minBilder` wäre ein Zucken statt einer Szene: lieber weniger Kapitel.
  const passt = Math.floor(budget / (kartenBilder + minBilder));
  const n = Math.max(1, Math.min(rang.length, wunsch, passt));
  const gewaehlt = rang.slice(0, n).sort((a, b) => a.von - b.von);

  const rest = budget - n * kartenBilder;
  const anteile = verteile(gewaehlt, rest, minBilder);

  const bilder = [];
  for (let k = 0; k < gewaehlt.length; k++) {
    const kap = gewaehlt[k];
    for (let i = 0; i < kartenBilder; i++) bilder.push({ ts: kap.von, kapitel: k });
    const m = anteile[k];
    const spanne = kap.bis - kap.von;
    for (let i = 0; i < m; i++) {
      const ts = m > 1 ? kap.von + Math.round((spanne * i) / (m - 1)) : kap.von;
      bilder.push({ ts, kapitel: null });
    }
  }

  return {
    bilder,
    kapitel: gewaehlt.map((i) => ({ von: i.von, bis: i.bis, gewicht: i.gewicht })),
    uebersprungen: alle.length - gewaehlt.length,
    // Der Recorder kappt am **ältesten** Ende: ein Log an der Kappgrenze hat mit hoher
    // Wahrscheinlichkeit den Morgen verloren. Still zu verlieren wäre der schlimmste Fehler
    // dieses Features, deshalb wandert die Zahl als Kopfzeile bis in die Bildunterschrift.
    gekappt: log.length >= REPLAY_CAP,
  };
}

/**
 * Bilder je Kapitel nach `√Gewicht`.
 *
 * Die Wurzel und nicht das Gewicht selbst: linear verteilt bekäme die stärkste Insel eines
 * Fehlertages zwei Drittel des Films und die übrigen sieben Kapitel je zwölf Bilder. Die Wurzel
 * dämpft genau so weit, dass die Rangfolge sichtbar bleibt, ohne dass ein Kapitel verhungert.
 */
function verteile(kapitel, rest, minBilder) {
  let summe = 0;
  for (const k of kapitel) summe += Math.sqrt(Math.max(0, k.gewicht));
  const anteile = kapitel.map((k) =>
    Math.max(minBilder, summe > 0 ? Math.round((rest * Math.sqrt(Math.max(0, k.gewicht))) / summe) : minBilder));

  // Runden und Untergrenze sprengen das Budget in beide Richtungen. Ausgeglichen wird immer am
  // größten bzw. kleinsten Kapitel, bei Gleichstand am vorderen — dieselbe Regel wie oben, und
  // aus demselben Grund.
  let ist = anteile.reduce((a, b) => a + b, 0);
  while (ist > rest) {
    let idx = -1;
    for (let i = 0; i < anteile.length; i++) {
      if (anteile[i] > minBilder && (idx < 0 || anteile[i] > anteile[idx])) idx = i;
    }
    if (idx < 0) break;
    anteile[idx]--;
    ist--;
  }
  while (ist < rest) {
    let idx = 0;
    for (let i = 1; i < kapitel.length; i++) if (kapitel[i].gewicht > kapitel[idx].gewicht) idx = i;
    anteile[idx]++;
    ist++;
  }
  return anteile;
}
