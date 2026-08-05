// Schicht 0 — die Geometrie des Büros. Einmal gebaut, danach nur gelesen.
//
// **Alles hier steht in SCENE-Koordinaten (1600×900).** Die ×0,3-Skalierung auf den
// 480×270-Puffer passiert ausschließlich beim Rendern und ausschließlich für Positionen;
// Sprites werden nie mitskaliert (PIXEL-CONTRACT.md Regel 1). Wer hier eine Größe in
// Pufferpixeln hinterlegte, verrechnete die halbe Zeichenschicht um den Faktor 3,3.
//
// Der Grundriss ist bewusst schlicht und lesbar: zwei Schreibtisch-
// bänke links und rechts, dazwischen ein freier Gang für den runden Tisch, der Chefplatz oben
// rechts als Blickfang, Tür oben, Kaffee links außen. Zu jedem gewählten Wert steht unten
// die Begründung.

import { MAX_SEATS, POS_SCALE, SCENE } from "./const.ts";
import { hash32 } from "./ids.ts";
import type { Pt, Room, Seat } from "./types.ts";

// ── Maße ─────────────────────────────────────────────────────────────────────

/** Pod-Plätze: zwei Bänke zu sechs. Der dreizehnte Sitz (`MAX_SEATS`) ist der Chefplatz und
 *  wird nie vergeben — den bekommt der Wurzellauf zugewiesen (`deskIndex === -1`). */
export const POD_SEATS = MAX_SEATS - 1;

/** Waagerechte Mitte der beiden Bänke. Links 23 %, rechts 79 % — nicht symmetrisch, weil der
 *  Chefplatz rechts oben sitzt und die rechte Bank etwas weiter an die Wand rückt. */
export const BANK_X: readonly number[] = [Math.round(SCENE.w * 0.23), Math.round(SCENE.w * 0.79)];

/** Drei Reihen je Bank, bei 37 / 58 / 78 % der Höhe. Die Abstände nehmen nach vorn leicht zu
 *  (21 → 20 Punkte), was den Raum in die Tiefe zieht, ohne dass irgendwo perspektivisch
 *  gerechnet werden müsste. */
export const ROW_Y: readonly number[] = [
  Math.round(SCENE.h * 0.37), Math.round(SCENE.h * 0.58), Math.round(SCENE.h * 0.78),
];

/** Abstand eines Sitzes von der Tischmitte. Ein Schreibtisch ist eine Zweierbank: links und
 *  rechts sitzt je eine Figur, beide blicken auf dieselbe Platte. */
export const SEAT_DX = 92;

/** Der Fußpunkt sitzt etwas **unter** der Tischmitte. Das ist kein Schönheitswert: die Szene
 *  sortiert nach `y` (Maler-Algorithmus), und eine Figur mit demselben `y` wie ihr Tisch
 *  landete je nach Einfügereihenfolge mal davor, mal dahinter. */
export const SEAT_DY = 14;

/** Halbe Breite des runden Tisches bzw. seine halbe Tiefe — die Wegpunkte des Huddles liegen
 *  darauf. Ost/West stehen weiter außen als Nord/Süd, weil eine Figur seitlich mehr Platz
 *  braucht als vor oder hinter dem Tisch. */
const TABLE_RX = 172;
const TABLE_RY = 112;

// ── Sitze ────────────────────────────────────────────────────────────────────

function seat(deskX: number, deskY: number, side: number): Seat {
  // side 0 = links vom Tisch (blickt nach rechts), side 1 = rechts (blickt nach links).
  const left = side === 0;
  return {
    desk: { x: deskX, y: deskY },
    sit: { x: deskX + (left ? -SEAT_DX : SEAT_DX), y: deskY + SEAT_DY },
    flip: !left,
  };
}

/** `seats[0..11]` = Pod, `seats[12]` = Chefplatz.
 *
 *  Nummerierung: `bank * 6 + reihe * 2 + seite`. Damit liegen die Plätze 0–5 komplett in der
 *  linken Bank und 6–11 in der rechten — zwei Läufe mit benachbarten Nummern sitzen also
 *  beieinander und nicht quer durch den Raum verstreut. */
function buildSeats(): Seat[] {
  const out: Seat[] = [];
  for (const x of BANK_X) {
    for (const y of ROW_Y) {
      out.push(seat(x, y, 0));
      out.push(seat(x, y, 1));
    }
  }
  // Chefplatz: 61 % / 32 %, blickt nach links in den Raum hinein.
  const cx = Math.round(SCENE.w * 0.61);
  const cy = Math.round(SCENE.h * 0.32);
  out.push({ desk: { x: cx, y: cy }, sit: { x: cx, y: cy + SEAT_DY }, flip: true });
  return out;
}

// ── Der Raum ─────────────────────────────────────────────────────────────────

const SEATS = buildSeats();

/** Türschwelle: 73 % / 13,5 %, also in der oberen Wand rechts der Mitte. */
const DOOR: Pt = { x: Math.round(SCENE.w * 0.73), y: Math.round(SCENE.h * 0.135) };

/** Mitte des runden Tisches: 51 % / 55 %. Genau im Gang zwischen den Bänken (23 % und 79 %)
 *  und unterhalb des Chefplatzes — der einzige Fleck, auf dem vier Figuren stehen können,
 *  ohne einen Schreibtisch zu verdecken. */
const TABLE: Pt = { x: Math.round(SCENE.w * 0.51), y: Math.round(SCENE.h * 0.55) };

/** Standplatz vor dem Serverschrank, **in Pufferpixeln gedacht und zurückgerechnet**.
 *
 *  Alle anderen Punkte dieser Datei sind Bruchteile von `SCENE` — der Schrank nicht: er ist
 *  ein Möbel und steht in `pixel/scene.ts` an einer Pufferkoordinate (`CABINET_X`/`CABINET_Y`),
 *  weil die Rückwand Kulisse ist und kein Ort, an dem ein Prozentwert etwas bedeutete. Ein
 *  ausgedachter Prozentwert hier träfe den Schrank nur zufällig.
 *
 *  Die 18 Pixel Versatz nach rechts sind das eigentliche Maß: eine Figur ist 16 Pufferpixel
 *  breit und stünde mittig **vor** den LED-Reihen, die sie zeigen soll. Prüfung 11
 *  (`RACK_PX ≡ round(ROOM.rack × POS_SCALE)`) hält beide Zahlen zusammen. */
const RACK_BUF = { x: 196 + 18, y: 60 + 8 };
const RACK: Pt = {
  x: Math.round(RACK_BUF.x / POS_SCALE),
  y: Math.round(RACK_BUF.y / POS_SCALE),
};

export const ROOM: Room = {
  seats: SEATS,
  door: DOOR,
  table: TABLE,
  // Feste Reihenfolge Nord · West · Ost · Süd. Fest, weil die Engine den n-ten Ankömmling auf
  // den n-ten Platz stellt: eine andere Reihenfolge ergäbe beim Zurückspulen ein anderes Bild.
  huddle: [
    { x: TABLE.x, y: TABLE.y - TABLE_RY },
    { x: TABLE.x - TABLE_RX, y: TABLE.y },
    { x: TABLE.x + TABLE_RX, y: TABLE.y },
    { x: TABLE.x + 0, y: TABLE.y + TABLE_RY },
  ],
  // Kaffeeecke: 6,5 % / 25 %, links außen vor der Bank — der Weg dorthin führt an keinem
  // Schreibtisch vorbei, sonst liefe die Kaffeepause quer durchs Bild.
  coffee: { x: Math.round(SCENE.w * 0.065), y: Math.round(SCENE.h * 0.25) },
  // Sammelpunkt außerhalb des Bildes (`deskIndex === -2`). Er liegt **hinter der Tür**, nicht
  // irgendwo am Rand: Figuren betreten und verlassen den Raum durch die Tür, und ein zweiter
  // Ausgang ließe sie durch die Wand laufen. Das negative `y` sortiert ihn zugleich hinter
  // alles andere, falls die Zeichenschicht ihn doch einmal streift.
  away: { x: DOOR.x, y: -100 },
  rack: RACK,
};

// ── Sitzvergabe ──────────────────────────────────────────────────────────────

/** Welchen Pod-Platz ein Agent bekommt: `hash32(id) % 12` mit linearer Sondierung.
 *
 *  Deterministisch **ohne** Warteschlange, und das ist Absicht: ein Platz, der von der
 *  Ankunftsreihenfolge abhängt, replayt nicht identisch. Beim Zurückspulen kommen dieselben
 *  Läufe in derselben `seq`-Reihenfolge, aber ein FIFO-Puffer trüge Zustand über die
 *  Rücksetzung hinweg — und dann säße derselbe Agent beim zweiten Ansehen woanders.
 *  Determinismus geht hier vor Realismus.
 *
 *  Ist alles belegt, gibt es keinen Stuhl: `-2` heißt „auswärts", die Figur bleibt außerhalb
 *  des Raums. Kein Sonderplatz, keine gestapelten Figuren auf demselben Stuhl.
 *
 *  `taken` enthält ausschließlich Pod-Nummern (0..11). Der Chefplatz (`-1`) wird nicht
 *  vergeben, sondern gesetzt. */
export function seatOf(id: string, taken: ReadonlySet<number>): number {
  const start = hash32(id) % POD_SEATS;
  for (let i = 0; i < POD_SEATS; i++) {
    const slot = (start + i) % POD_SEATS;
    if (!taken.has(slot)) return slot;
  }
  return -2;
}

/** Fußpunkt eines Platzes. `-1` = Chefplatz, `-2` (und alles außerhalb 0..12) = auswärts.
 *
 *  Gibt eine **Kopie** zurück. Ein Aufrufer, der versehentlich `pt.x += 3` rechnet, verschöbe
 *  sonst den Stuhl für den Rest der Sitzung — und beim nächsten Abspielen stünde er wieder
 *  woanders. */
export function podSeat(slot: number): Pt {
  const src = slot === -1 ? SEATS[POD_SEATS].sit
    : (slot >= 0 && slot < POD_SEATS ? SEATS[slot].sit : ROOM.away);
  return { x: src.x, y: src.y };
}
