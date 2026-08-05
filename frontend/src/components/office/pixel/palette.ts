// Schicht 1 — die Farben des Büros. Eine flache Tabelle, zwei Abstufungen, keine Rechnerei
// zur Laufzeit.
//
// Warum eine Tabelle und nicht CSS-Variablen: die Zeichenschicht setzt pro Bild einige hundert
// Mal `fillStyle`. Jede Farbe, die erst aus dem Dokument gelesen werden müsste, wäre ein
// Layout-Lesevorgang mitten in der Zeichenschleife — der teuerste Fehler, den dieses Feature
// machen kann, und obendrein in Schicht 1 verboten (Regel 3.1). Farben stehen deshalb hier als
// Hex-Zeichenketten, und `resolve()` läuft **einmal je Themenwechsel**, nie je Bild.
//
// Die zwei Abstufungen sind an Traccoons Theme gebunden, nicht an die Uhr:
//
//   data-theme="dark"  →  "night"  = Abendbüro: Schreibtischlampen an, die Monitore sind die
//                                    dominierende Lichtquelle, die Fenster stehen tiefblau.
//   data-theme="light" →  "day"    = Tagbüro: warmes Off-White, Fensterglanz, Lampen aus.
//
// Das ist ausdrücklich **keine Tageszeitsimulation**. Die echte Uhrzeit zu lesen bräche den
// Determinismus (dasselbe Log ergäbe morgens und abends verschiedene Bilder, das Zurückspulen
// wäre nicht mehr bitgleich) — und es würde den Zuschauer verwirren: er sähe „Abend", obwohl
// der Lauf, den er gerade anschaut, um 9 Uhr morgens stattfand. Der Raum zeigt den Lauf,
// nicht den Feierabend.

import type { Gait, Grade, Look } from "../types.ts";
import { hash32, mix, rnd01 } from "../ids.ts";
import { PACE_SPREAD } from "../const.ts";

// ── Die Schlüssel ────────────────────────────────────────────────────────────

/** Jede Farbe des Raums hat einen Namen; Hex-Werte stehen ausschließlich in den zwei Tabellen
 *  unten. Die letzten sieben sind **reserviert**: sie gehören keiner Kulisse, sondern der
 *  gerade gezeichneten Figur und werden erst von `resolve(grade, look)` gefüllt. */
export type PalKey =
  // Kulisse
  | "wall" | "wallLo" | "wallHi"
  | "floor" | "floorLo" | "floorHi"
  | "rug" | "rugLo"
  /** Was durchs Fenster zu sehen ist — Himmel bzw. Nachtstadt. */
  | "out"
  // Möbel
  | "desk" | "deskLo" | "chair" | "chairLo" | "metal" | "glass" | "clay"
  // Bildschirme und Papier
  | "screen" | "screenLit" | "ink" | "paper"
  // Grün
  | "plant" | "plantLo" | "soil"
  // Licht
  | "lamp" | "shadow"
  // Zustandsfarben (Blasenrand, Monitorschein, Gate-Puls)
  | "acc" | "ok" | "err" | "blocked"
  // ── reserviert: die Figur ──────────────────────────────────────────────────
  /** Haut. */        | "S"
  /** Haut, Schatten. */ | "s"
  /** Haar. */        | "H"
  /** Haar, Schatten. */ | "h"
  /** Oberteil. */    | "T"
  /** Oberteil, Schatten. */ | "t"
  /** Hose. */        | "P";

/** Aufgelöste Palette: jeder Schlüssel zeigt auf einen fertigen Hex-Wert. */
export type Pal = Record<PalKey, string>;

/** Alles außer den sieben reservierten Zeichen — das, was in den Grade-Tabellen steht. */
type EnvKey = Exclude<PalKey, "S" | "s" | "H" | "h" | "T" | "t" | "P">;

// ── Die zwei Tabellen ────────────────────────────────────────────────────────
//
// Handgesetzt, nicht gerechnet. Ein Nachtbild als „Tagbild × 0.4" sieht aus wie ein
// abgedunkeltes Foto: die Monitore würden mitverdunkelt, obwohl sie im Abendbüro genau die
// Lichtquelle sind, die alles andere überhaupt sichtbar macht. Deshalb zwei Tabellen, in denen
// `screenLit` und `lamp` **heller** werden, während der Rest fällt.

const DAY_ENV: Record<EnvKey, string> = {
  wall: "#e6e0d3", wallLo: "#c4bba7", wallHi: "#f4f0e7",
  floor: "#c49a67", floorLo: "#a37c4e", floorHi: "#d7b184",
  rug: "#8496ad", rugLo: "#6a7c94",
  out: "#bfdcef",
  desk: "#d9bd8e", deskLo: "#a8814f",
  chair: "#4d5a6b", chairLo: "#333e4c",
  metal: "#a9b1bb", glass: "#eaf3fa", clay: "#b9694a",
  screen: "#2e343d", screenLit: "#dbe6f0", ink: "#3a4350", paper: "#f7f5ef",
  plant: "#559159", plantLo: "#3a6b41", soil: "#5d4632",
  lamp: "#f3e2b4", shadow: "#5d4f3c",
  acc: "#38bdf8", ok: "#4ade80", err: "#f87171", blocked: "#fb923c",
};

const NIGHT_ENV: Record<EnvKey, string> = {
  wall: "#2b3040", wallLo: "#1e2331", wallHi: "#3a4154",
  floor: "#4a3b2e", floorLo: "#372b21", floorHi: "#5d4a39",
  rug: "#3b4557", rugLo: "#2c3442",
  out: "#0e1a33",
  desk: "#6b563a", deskLo: "#4a3a26",
  chair: "#2b333f", chairLo: "#1b212a",
  metal: "#5a636e", glass: "#7f96a8", clay: "#7c4633",
  // Absichtlich **nicht** dunkler als tagsüber: im Abendbüro sind die Monitore die Lampen.
  screen: "#161b22", screenLit: "#cfe3f5", ink: "#2b3444", paper: "#cfc9bb",
  plant: "#2f5c3a", plantLo: "#20402a", soil: "#33261b",
  lamp: "#ffd88a", shadow: "#080c14",
  // Zustandsfarben bleiben in beiden Abstufungen **identisch** — sie sind dieselben vier Farben
  // wie im Dock (AgentMonitor: yellow/green/red/orange). Zwei Ansichten desselben Laufs dürfen
  // sich nicht widersprechen, nur weil eine davon abends spielt.
  acc: "#38bdf8", ok: "#4ade80", err: "#f87171", blocked: "#fb923c",
};

// ── Die Figur: Farbtöne, aus denen `lookOf` wählt ────────────────────────────
//
// `Look.skin`/`hairCol`/`shirtCol`/`pantsCol` sind **Palettenschlüssel**, keine CSS-Farben
// (so steht es im Vertrag) — Namen aus dieser Tabelle. Damit bleibt `Look` serialisierbar und
// unabhängig von der Abstufung: dieselbe Figur ist abends dieselbe Figur, nur dunkler.

const TONES: Record<string, string> = {
  skin0: "#f2cda6", skin1: "#e3b184", skin2: "#cd9264",
  skin3: "#ab744c", skin4: "#7f5433", skin5: "#5b3a24",

  hair0: "#241c16", hair1: "#4b3521", hair2: "#7b5a34", hair3: "#b9863f",
  hair4: "#d8b46a", hair5: "#8c3b22", hair6: "#8a8f96", hair7: "#3d4a63",

  shirt0: "#3f6fb0", shirt1: "#4a8f6a", shirt2: "#b5563f", shirt3: "#6b5aa6",
  shirt4: "#c9973f", shirt5: "#3a4450", shirt6: "#a8556f", shirt7: "#2f8f92",

  pants0: "#3a4152", pants1: "#26303f", pants2: "#5a4a3a",
  pants3: "#4a4f57", pants4: "#2f3a33",
};

/** Wie stark der Schattenton unter dem Grundton liegt. 0.70 ist die Grenze, ab der die Kante
 *  bei 16×24 noch als Falte liest und nicht als Loch. */
const SHADE = 0.70;

/** Nachts sind Menschen nicht anders gefärbt, nur anders beleuchtet: rund 18 % dunkler und
 *  8 % kühler. Beides gerechnet statt getippt, weil es sonst 26 zusätzliche Hex-Werte wären,
 *  die man bei jeder Farbänderung doppelt pflegen müsste. */
const NIGHT_DARK = 0.82;
const NIGHT_COOL = 0.08;

// ── Farbrechnung (rein, ganzzahlig gerundet) ─────────────────────────────────

function clamp255(v: number): number {
  return v < 0 ? 0 : v > 255 ? 255 : Math.round(v);
}

function hex2(v: number): string {
  const s = clamp255(v).toString(16);
  return s.length === 1 ? "0" + s : s;
}

/** `#rrggbb` → drei Kanäle. Kurzform (`#abc`) gibt es in den Tabellen bewusst nicht. */
function parse(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];
}

/** Multipliziert alle Kanäle — der Schattenton eines Sprite-Teils. */
function darken(hex: string, f: number): string {
  const [r, g, b] = parse(hex);
  return "#" + hex2(r * f) + hex2(g * f) + hex2(b * f);
}

/** Kühlt eine Farbe: Rot fällt am stärksten, Blau wandert Richtung Weiß. Das ist die
 *  billigste glaubwürdige Nachahmung von Mondlicht — ein reines Abdunkeln lässt Hauttöne
 *  schlammig statt abendlich aussehen. */
function cool(hex: string, k: number): string {
  const [r, g, b] = parse(hex);
  return "#" + hex2(r * (1 - k)) + hex2(g * (1 - k * 0.5)) + hex2(b + (255 - b) * k * 0.5);
}

/** Der Personenton in der jeweiligen Abstufung. */
function toneOf(key: string, fallback: string, grade: Grade): string {
  const hex = TONES[key] ?? fallback;
  return grade === "night" ? cool(darken(hex, NIGHT_DARK), NIGHT_COOL) : hex;
}

// ── Die zwei fertigen Abstufungen ────────────────────────────────────────────

/** Voreingestellte Figur für den Fall, dass ohne `Look` aufgelöst wird (Möbelvorschau,
 *  Prüferbilder). Nie im laufenden Raum sichtbar — dort hat jeder Aktor seinen `Look`. */
function defaultPerson(grade: Grade): Pick<Pal, "S" | "s" | "H" | "h" | "T" | "t" | "P"> {
  const S = toneOf("skin1", "#e3b184", grade);
  const H = toneOf("hair1", "#4b3521", grade);
  const T = toneOf("shirt0", "#3f6fb0", grade);
  const P = toneOf("pants0", "#3a4152", grade);
  return { S, s: darken(S, SHADE), H, h: darken(H, SHADE), T, t: darken(T, SHADE), P };
}

/** Die beiden Abstufungen, fertig aufgelöst. Konstant über die Laufzeit — wer daran etwas
 *  ändert, ändert es für alle Bilder gleichzeitig. */
export const GRADES: Record<Grade, Pal> = {
  day: { ...DAY_ENV, ...defaultPerson("day") },
  night: { ...NIGHT_ENV, ...defaultPerson("night") },
};

// ── Aussehen aus dem Seed ────────────────────────────────────────────────────
//
// Jedes Merkmal bekommt sein **eigenes benanntes Salz** (Regel 3.2). Zwei Merkmale am selben
// Salz wären perfekt korreliert — dann trügen alle Blonden dasselbe Hemd, und das fällt erst
// auf, wenn zwölf Figuren im Raum stehen.

const SALT_HEAD = 0x4b4f5046;   // "KOPF"
const SALT_HAIR = 0x48414152;   // "HAAR" — die **Form** der Frisur (individuell)
const SALT_HAIRC = 0x48414146;  // "HAAF" — die **Farbe** des Haars (aus der Rolle)
const SALT_TORSO = 0x544f5253;  // "TORS"
const SALT_ARMS = 0x41524d45;   // "ARME"
const SALT_LEGS = 0x4245494e;   // "BEIN"
const SALT_SKIN = 0x48415554;   // "HAUT"
const SALT_SHIRT = 0x48454d44;  // "HEMD"
const SALT_PANTS = 0x484f5345;  // "HOSE"

const SALT_PACE = 0x54454d50;   // "TEMP"
const SALT_BOB = 0x574950;      // "WIP"
const SALT_PHASE = 0x50484153;  // "PHAS"
const SALT_STRIDE = 0x53434852; // "SCHR"
const SALT_LEAN = 0x4e454947;   // "NEIG"
const SALT_SWING = 0x53434857;  // "SCHW"
const SALT_ARMPH = 0x41524d50;  // "ARMP"

/** Anzahl Haarformen (Art-Teile) und Haarfarben. Das Produkt war früher der Vorrat an
 *  Frisuren; seit Form und Farbe aus zwei verschiedenen Quellen kommen (s. `lookOf`), sind es
 *  zwei unabhängige Vorräte. */
const HAIR_SHAPES = 5;
const HAIR_COLORS = 8;

// ── Der Aussehen-Seed: was die Rolle bestimmt ────────────────────────────────

const SALT_ROLLE = 0x524f4c4c;  // "ROLL"

/**
 * Der Seed, aus dem das **Aussehen** kommt — nicht zu verwechseln mit `ActorState.seed`, aus
 * dem alles Individuelle kommt.
 *
 * Warum überhaupt zwei Seeds: `ActorState.seed` ist `hash32("run:8871")`, also die **Lauf**-Id.
 * Damit sah derselbe `developer` gestern anders aus als heute — wiedererkennen konnte man
 * niemanden. Aus `hash32(role)` dagegen fällt für jede Rolle für immer dieselbe Farbe.
 *
 * Bewusst **keine Rollen-Farbtabelle**: die echten Rollen (`developer`, `assistent`,
 * `architect`, `code_reviewer`, `project_manager`, `uniwar-operator`, `news`) sind Daten, keine
 * Aufzählung — eine Tabelle bräuchte Pflege bei jedem neuen Agenten und hätte für die
 * Prüf-Fixture (`exec_agent`/`plan_agent`/`review_agent`) gar keinen Eintrag.
 *
 * Leere Rolle → der Laufseed. Eine namenlose Figur soll nicht alle namenlosen Figuren einander
 * gleichmachen; und weil `rolle === seed` genau die alten Salze wieder trifft, ist der
 * rollenlose Fall bitgleich zum bisherigen Verhalten.
 */
export function rollenSeed(role: string, seed: number): number {
  return role ? mix(hash32(role), SALT_ROLLE) : seed;
}

/**
 * Das Aussehen einer Figur — reine Funktion aus zwei Seeds, also über Live und Replay identisch.
 *
 * **Die Aufteilung ist der ganze Punkt.** Aus `rolle` kommen genau die drei Merkmale, die man
 * aus drei Metern Entfernung überhaupt lesen kann:
 *
 *   · **Hemdfarbe** — die größte zusammenhängende Farbfläche eines 16×24-Sprites,
 *   · **Haarfarbe** — die zweitgrößte; zusammen mit dem Hemd ein Wappen,
 *   · **Torsoform** — die Schultersilhouette, die die Rolle auch von hinten trägt
 *     (der Chefplatz sitzt mit `DIR_BACK` zum Betrachter).
 *
 * Alles andere hängt am Laufseed: Kopf, Haut, Arme, Beine, **Haarform**, Hosenfarbe. Sonst
 * stünden zwölf `developer` als zwölf Klone im Raum — und Wiedererkennung, die keine Individuen
 * mehr zulässt, ist keine Wiedererkennung, sondern eine Uniform.
 *
 * Der frühere Griff, Haarform und -farbe aus **einem** Hash zu ziehen (Rest/Quotient über
 * `HAIR_SHAPES × HAIR_COLORS`), ist damit hinfällig: die beiden liegen jetzt ohnehin auf
 * verschiedenen Seeds und variieren zwangsläufig unabhängig. Sie brauchen dafür aber **eigene
 * Salze** — mit demselben Salz wären sie im rollenlosen Fall (`rolle === seed`) perfekt
 * korreliert, und dann hätte jede Frisurform genau eine Farbe.
 *
 * Der Sitzplatz bleibt bewusst an der Lauf-Id (`seatOf(a.id)`, Schicht 0): `seatOf` sondiert
 * linear, zwölf `developer` bekämen sonst zwölf **aufeinanderfolgende** Plätze und die linke
 * Bank wäre eine Monokultur.
 */
export function lookOf(seed: number, rolle: number): Look {
  return {
    head: mix(seed, SALT_HEAD) % 3,
    hair: mix(seed, SALT_HAIR) % HAIR_SHAPES,
    torso: mix(rolle, SALT_TORSO) % 3,
    arms: mix(seed, SALT_ARMS) % 4,
    legs: mix(seed, SALT_LEGS) % 4,
    skin: "skin" + (mix(seed, SALT_SKIN) % 6),
    hairCol: "hair" + (mix(rolle, SALT_HAIRC) % HAIR_COLORS),
    shirtCol: "shirt" + (mix(rolle, SALT_SHIRT) % 8),
    pantsCol: "pants" + (mix(seed, SALT_PANTS) % 5),
  };
}

/**
 * Die Gangart — ebenfalls rein aus dem Seed.
 *
 * Sieben Werte, weil Wiedererkennbarkeit über die Bewegung läuft: bei 16 Pixeln Breite sieht man
 * die Frisur erst, wenn man hinschaut, den Gang aber sofort. `armPhase` liegt bewusst rund eine
 * halbe Periode neben `phase` (Arme und Beine schwingen gegenläufig) — mit gleicher Phase
 * marschieren alle.
 */
export function gaitOf(seed: number): Gait {
  return {
    speed: 1 + (rnd01(mix(seed, SALT_PACE)) - 0.5) * 2 * PACE_SPREAD,
    bob: 0.35 + rnd01(mix(seed, SALT_BOB)) * 0.65,
    phase: rnd01(mix(seed, SALT_PHASE)),
    stride: 2 + (mix(seed, SALT_STRIDE) % 3),
    lean: rnd01(mix(seed, SALT_LEAN)) * 0.6,
    swing: 1 + (mix(seed, SALT_SWING) % 2),
    armPhase: (rnd01(mix(seed, SALT_PHASE)) + 0.5 + rnd01(mix(seed, SALT_ARMPH)) * 0.2) % 1,
  };
}

// ── Auflösen ─────────────────────────────────────────────────────────────────

/**
 * Baut die fertige Palette: Kulisse aus der Abstufung, die sieben reservierten Schlüssel aus
 * dem `Look`. Ohne `Look` kommt die Voreinstellungsfigur.
 *
 * **Einmal je Themenwechsel bzw. je Figur, nie je Bild.** Ein Objekt-Spread über 36 Schlüssel
 * ist billig, 24 Figuren × 60 Bilder/s davon sind es nicht — dafür gibt es `palFor` unten.
 */
export function resolve(grade: Grade, look?: Look): Pal {
  const base = GRADES[grade];
  if (!look) return { ...base };
  const S = toneOf(look.skin, "#e3b184", grade);
  const H = toneOf(look.hairCol, "#4b3521", grade);
  const T = toneOf(look.shirtCol, "#3f6fb0", grade);
  const P = toneOf(look.pantsCol, "#3a4152", grade);
  return {
    ...base,
    S, s: darken(S, SHADE),
    H, h: darken(H, SHADE),
    T, t: darken(T, SHADE),
    P,
  };
}

/** Identität eines `Look` für den Zwischenspeicher — nur die vier Farbschlüssel, denn nur die
 *  landen in der Palette. Zwei Figuren mit gleichen Farben teilen sich also eine Palette,
 *  auch wenn ihre Frisur verschieden ist. */
function lookKey(grade: Grade, look: Look): string {
  return grade + "|" + look.skin + "|" + look.hairCol + "|" + look.shirtCol + "|" + look.pantsCol;
}

const PAL_CACHE = new Map<string, Pal>();

/**
 * `resolve` mit Gedächtnis — **das** ist die Fassung, die die Zeichenschleife benutzt.
 *
 * Der Zwischenspeicher ist reine Allokationsersparnis: gleiche Eingabe, gleiches Ergebnis,
 * kein Zustand, der ins Bild durchschlägt (Regel 3 bleibt gewahrt). Er wird geleert, wenn er
 * über 64 Einträge wächst — mehr als `MAX_ACTORS` verschiedene Farbsätze kann es zwar auf der
 * Bühne nicht geben, wohl aber über eine lange Sitzung hinweg mit vielen kommenden und
 * gehenden Läufen. Eine Map, die nie leert, ist ein Leck mit Anlauf.
 *
 * **Der 64er-Deckel hält auch mit rollenfesten Farben.** `lookKey` sieht vier Schlüssel; zwei
 * davon (Hemd, Haar) sind je Rolle konstant, die anderen beiden variieren individuell über
 * 6 Hauttöne × 5 Hosen = **30 Paletten je Rolle und Abstufung**. Entscheidend ist aber nicht
 * die Obergrenze über die Sitzung, sondern die je **Bild**: dort fragen höchstens
 * `MAX_ACTORS = 24` Figuren an, also passen die Einträge eines Bildes immer unter 64. Ein
 * `clear()` kann deshalb nie mitten im Bild zuschlagen und sich Bild für Bild wiederholen —
 * sonst wäre aus dem Zwischenspeicher still eine Allokation je Bild geworden.
 */
export function palFor(grade: Grade, look: Look): Pal {
  const key = lookKey(grade, look);
  const hit = PAL_CACHE.get(key);
  if (hit) return hit;
  if (PAL_CACHE.size > 64) PAL_CACHE.clear();
  const pal = resolve(grade, look);
  PAL_CACHE.set(key, pal);
  return pal;
}
