// Schicht 1 — die Leute.
//
// Maßstab (Regel 1 des Pixel-Vertrags): eine Figur ist **16×24 Pufferpixel**, nicht 16×24
// Szenenpixel. `POS_SCALE` taucht hier genau einmal auf — um die Szenenkoordinate eines
// Aktors hereinzuholen. Für keine einzige Größe.
//
// Die Figur ist aus 19 Teilen zusammengesetzt, nicht als 8 fertige Posen gezeichnet:
//
//   Kopf   3 × 10×9   front · seit · rück
//   Haar   5 × 12×11  Überlagerung — der Hebel für unterscheidbare Silhouetten
//   Torso  3 ×  8×10
//   Arme   4 ×  4×6   Überlagerung: ruhend · tippen-A · tippen-B · greifen/tragen
//   Beine  4 ×  8×6   sitzen · stehen · gehen-A · gehen-B
//
// 19 Teile statt 8×12 fertiger Sprites: acht Posen mal zwölf Leute wären 96 Bilder à 384 Pixel,
// also das Vierfache des gesamten Kunstbudgets. Zusammengesetzt kostet dieselbe Vielfalt 19
// Teile — und jede neue Pose kostet danach ein Teil, nicht zwölf Bilder.
//
// Der Aufbau von unten nach oben (die Reihenfolge ist die Verdeckung):
//
//   yBase-1  … yBase-6    Beine        (8 breit, mittig)
//   yBase-6  … yBase-15   Torso        (8 breit, überlappt die Beine um eine Zeile)
//   yBase-9  … yBase-14   Arme         (je 4 breit, links und rechts am Torso)
//   yBase-16 … yBase-24   Kopf         (10 breit)
//   yBase-14 … yBase-24   Haar         (12 breit, über Kopf **und** Schultern)
//
// Macht 24 Zeilen. Der Kopf nimmt davon 9 — bewusst zu groß für einen Erwachsenen: bei
// 24 Pixeln Gesamthöhe ist der Kopf das Einzige, woran man auf einen Blick „Mensch" erkennt.

import type { ActorState, Ctx, Gait, Look, Pose } from "../types.ts";
import { GATE_PULSE_MS, POS_SCALE } from "../const.ts";
import { mix } from "../ids.ts";
import type { Art } from "./art.ts";
import { defineArt, drawArt, fill, fillA } from "./art.ts";
import type { Pal } from "./palette.ts";
import { gaitOf, lookOf } from "./palette.ts";

// ═══ Maße ════════════════════════════════════════════════════════════════════

/** Nennbreite und -höhe einer Figur in Pufferpixeln. Die Arts bleiben knapp darunter
 *  (14 statt 16 breit); die 16 sind das Raster, in dem die Szene rechnet — Trefferprüfung,
 *  Blasenbreite, Mindestabstand zweier Figuren. */
export const FIG_W = 16;
export const FIG_H = 24;

/** Wie tief der Oberkörper beim Sitzen absackt. Drei Pixel sind wenig und genügen: zusammen
 *  mit den Sitzbeinen (Oberschenkel waagerecht) liest die Figur sofort als sitzend, und mehr
 *  ließe den Kopf hinter der Tischplatte verschwinden. */
const SIT_DROP = 3;

// ═══ Die Arts ════════════════════════════════════════════════════════════════
//
// Legende: `S`/`s` Haut und Hautschatten · `H`/`h` Haar und Haarschatten · `T`/`t` Oberteil
// und dessen Schatten · `P` Hose. Diese sieben Zeichen sind für die Figur **reserviert** und
// werden erst beim Zeichnen aus `palFor(grade, look)` bedient — dieselben 19 Teile ergeben so
// zwölf verschiedene Leute, ohne dass ein Pixel doppelt im Quelltext steht.
// `i` (= `ink`) ist kein reserviertes Zeichen, sondern echte Tinte: Augen und Schuhe sind bei
// jedem Menschen dunkel, die dürfen nicht mit der Haut mitwandern.

// ── Kopf ─────────────────────────────────────────────────────────────────────
// Die Augen liegen in **Zeile 4**, nicht weiter oben. Das ist keine Anatomie, sondern
// Platzverwaltung: das Haar deckt die Zeilen 0–3 ab, und ein Pony, der eine Augenzeile
// überschreibt, macht aus jeder zweiten Frisur ein blindes Gesicht.

const HEAD_FRONT = defineArt([
  "..SSSSSS..",
  ".SSSSSSSS.",
  "SSSSSSSSSS",
  "SSSSSSSSSS",
  "SSiSSSSiSS",
  "SSSSSSSSSS",
  ".SSSSSSSS.",
  "..sSSSSs..",
  "...sSSs...",
], { S: "S", s: "s", i: "ink" });

/** Blick nach rechts; nach links wird gespiegelt. Zwei Merkmale trennen die Seitenansicht von
 *  der Vorderansicht: das Ohr (`s`, Zeile 4) und die Nase, die in Zeile 5 eine Spalte weiter
 *  hinausragt. Ohne die Nase sieht der Kopf im Profil aus wie ein zu schmaler Vorderkopf. */
const HEAD_SIDE = defineArt([
  "..SSSSSS..",
  ".SSSSSSSS.",
  ".SSSSSSSS.",
  ".SSSSSSSS.",
  ".SsSSSiSS.",
  ".SSSSSSSSS",
  ".SSSSSSSs.",
  "..sSSSSs..",
  "...sSSs...",
], { S: "S", s: "s", i: "ink" });

const HEAD_BACK = defineArt([
  "..SSSSSS..",
  ".SSSSSSSS.",
  "SSSSSSSSSS",
  "SSSSSSSSSS",
  "SSSSSSSSSS",
  "sSSSSSSSSs",
  ".SSSSSSSS.",
  "..ssssss..",
  "...sSSs...",
], { S: "S", s: "s" });

const HEADS: readonly Art[] = [HEAD_FRONT, HEAD_SIDE, HEAD_BACK];

/** Kopfrichtung. Zahlen statt Zeichenketten, weil sie direkt in `HEADS` indizieren. */
const DIR_FRONT = 0;
const DIR_SIDE = 1;
const DIR_BACK = 2;

// ── Haar ─────────────────────────────────────────────────────────────────────
// Zwölf Figuren unterscheiden sich bei 16 Pixeln Breite **nur** über die Silhouette. Die
// Hautfarbe sieht man aus zwei Metern nicht, das Hemd kaum — die Kopfform sofort. Deshalb ist
// das Haar das einzige Teil, das über den Kopf hinausragen darf (12 statt 10 breit) und bis
// auf die Schultern reichen kann (Zeilen 9/10 liegen schon auf dem Torso).
//
// Zeile 0–8 decken sich mit dem Kopf, Spalte n des Haars ist Spalte n-1 des Kopfes.

const HAIR_SHORT = defineArt([
  "...HHHHHH...",
  "..HHHHHHHH..",
  ".HHHHHHHHHH.",
  ".HHHHHHHHHH.",
  ".HH......HH.",
  ".H........H.",
  "............",
  "............",
  "............",
  "............",
  "............",
], { H: "H", h: "h" });

const HAIR_PART = defineArt([
  "...HHHHHH...",
  "..HHHHHHHH..",
  ".HHHHHHHHHH.",
  ".HhHHHHHHHH.",
  ".HH.....HHH.",
  ".H.......HH.",
  "..........H.",
  "............",
  "............",
  "............",
  "............",
], { H: "H", h: "h" });

const HAIR_LONG = defineArt([
  "...HHHHHH...",
  "..HHHHHHHH..",
  ".HHHHHHHHHH.",
  ".HHHHHHHHHH.",
  "HHH......HHH",
  "HHh......hHH",
  "HHH......HHH",
  ".HH......HH.",
  ".HH......HH.",
  ".Hh......hH.",
  "............",
], { H: "H", h: "h" });

const HAIR_TAIL = defineArt([
  "...HHHHHH...",
  "..HHHHHHHH..",
  ".HHHHHHHHHH.",
  ".HHHHHHHHHHH",
  ".HH......HHH",
  ".H.......Hhh",
  "..........HH",
  "..........hH",
  "...........H",
  "............",
  "............",
], { H: "H", h: "h" });

const HAIR_CURL = defineArt([
  "..HHHHHHHH..",
  ".HHHHHHHHHH.",
  "HHHHHHHHHHHH",
  "HHHHHHHHHHHH",
  "HHH......HHH",
  "HHh......hHH",
  ".H........H.",
  "............",
  "............",
  "............",
  "............",
], { H: "H", h: "h" });

const HAIRS: readonly Art[] = [HAIR_SHORT, HAIR_PART, HAIR_LONG, HAIR_TAIL, HAIR_CURL];

// ── Torso ────────────────────────────────────────────────────────────────────
// Die unterste Zeile ist Hose (`P`) und nicht Oberteil: sie ist der Bund, auf dem die Beine
// aufsetzen. Ohne ihn klafft beim Gehen zwischen Hemdsaum und Bein eine Lücke.

const TORSO_PLAIN = defineArt([
  ".TTTTTT.",
  "TTTTTTTT",
  "TTTTTTTT",
  "TTTTTTTT",
  "TTTTTTTT",
  "TTTTTTTT",
  "TTTTTTTT",
  "tTTTTTTt",
  "tttttttt",
  "PPPPPPPP",
], { T: "T", t: "t", P: "P" });

/** Hemd: Kragen (zwei Hautpixel am Ausschnitt) und eine senkrechte Knopfleiste. */
const TORSO_SHIRT = defineArt([
  ".TTTTTT.",
  "TTtSStTT",
  "TTTtsTTT",
  "TTTtTTTT",
  "TTTtTTTT",
  "TTTtTTTT",
  "TTTtTTTT",
  "tTTtTTTt",
  "tttttttt",
  "PPPPPPPP",
], { T: "T", t: "t", P: "P", S: "S", s: "s" });

/** Kapuzenpulli: breitere Schulter, Kapuzenkante, Bauchtasche. */
const TORSO_HOOD = defineArt([
  "tTTTTTTt",
  "TtTTTTtT",
  "TTTTTTTT",
  "TTTTTTTT",
  "TTTTTTTT",
  "TTtttttT",
  "TTtTTTtT",
  "TTtttttT",
  "tttttttt",
  "PPPPPPPP",
], { T: "T", t: "t", P: "P" });

const TORSOS: readonly Art[] = [TORSO_PLAIN, TORSO_SHIRT, TORSO_HOOD];

// ── Arme ─────────────────────────────────────────────────────────────────────
// Gezeichnet als **rechter** Arm (Spalte 0 liegt am Torso); der linke ist derselbe Art,
// gespiegelt. Vier Zustände genügen, weil ein Arm bei 4×6 nur drei Dinge sagen kann:
// er hängt, er liegt auf der Tastatur, er greift nach vorn.

const ARM_REST = defineArt([
  "TTt.",
  "TTt.",
  "TTt.",
  ".Tt.",
  ".SS.",
  ".Ss.",
], { T: "T", t: "t", S: "S", s: "s" });

const ARM_TYPE_A = defineArt([
  "TTt.",
  "TTt.",
  ".TTt",
  "..Tt",
  "..SS",
  "....",
], { T: "T", t: "t", S: "S" });

const ARM_TYPE_B = defineArt([
  "TTt.",
  "TTt.",
  ".TTt",
  "..SS",
  "....",
  "....",
], { T: "T", t: "t", S: "S" });

const ARM_REACH = defineArt([
  "TTt.",
  "TTTt",
  ".TTS",
  "..SS",
  "....",
  "....",
], { T: "T", t: "t", S: "S" });

const ARMS: readonly Art[] = [ARM_REST, ARM_TYPE_A, ARM_TYPE_B, ARM_REACH];

const ARM_REST_I = 0;
const ARM_TYPE_A_I = 1;
const ARM_TYPE_B_I = 2;
const ARM_REACH_I = 3;

/** Ab welcher Zeile eines Arm-Arts der Unterarm beginnt.
 *
 *  Damit kostet „kurzer Ärmel" **kein** zusätzliches Art: der untere Teil desselben Sprites
 *  wird ein zweites Mal gezeichnet, diesmal komplett in Hautfarbe (`tint`). Vier weitere Arme
 *  wären zwar auch bezahlbar, aber sie müssten bei jeder Formänderung mitgepflegt werden —
 *  und genau das vergisst man. */
const ARM_CUFF: readonly number[] = [3, 3, 3, 2];

/** Der Unterarm-Teil jedes Arms, einmal beim Laden abgeschnitten. Erlaubt ist das, weil
 *  `drawArt` am **Fußpunkt** ankert: ein von oben gekürztes Art landet an derselben Stelle
 *  wie das Original. */
const ARM_FORE: readonly Art[] = ARMS.map((a, i) => ({
  rows: a.rows.slice(ARM_CUFF[i]), map: a.map,
}));

// ── Beine ────────────────────────────────────────────────────────────────────
// Der Laufzyklus hat vier Bilder, aber nur drei Arts: `stehen · gehen-A · stehen · gehen-B`.
// Die Durchgangsstellung ist zweimal dieselbe Haltung, und dass sie beide Male gleich aussieht,
// ist richtig so — echte Beine sehen in beiden Durchgängen gleich aus.

/** Sitzen, von der Seite: Oberschenkel waagerecht nach vorn, Unterschenkel senkrecht.
 *  Zusammen mit `SIT_DROP` ist das der ganze Unterschied zwischen „steht am Tisch" und
 *  „sitzt am Tisch" — und ohne ihn sähe der halbe Raum aus, als arbeite er im Stehen. */
const LEGS_SIT = defineArt([
  "PPPPPPPP",
  "PPPPPPPP",
  "...PPPPP",
  "......PP",
  "......PP",
  "....iiii",
], { P: "P", i: "ink" });

const LEGS_STAND = defineArt([
  "PPPPPPPP",
  "PPPPPPPP",
  "PPP..PPP",
  "PPP..PPP",
  "PPP..PPP",
  "iii..iii",
], { P: "P", i: "ink" });

// Der volle Schritt ist bewusst **weit**: die erste Fassung stellte die Füße nur zwei Pixel
// auseinander, und im Prüfbild war eine laufende Figur von einer stehenden nicht zu
// unterscheiden. Bei 8 Pixeln Beinbreite muss der Ausschlag an den Rand gehen, sonst ist er
// kleiner als die Strichstärke.

const LEGS_WALK_A = defineArt([
  "PPPPPPPP",
  "PPPPPPPP",
  ".PPPPPP.",
  ".PP..PP.",
  "PP....PP",
  "ii....ii",
], { P: "P", i: "ink" });

/** Der Gegenschritt. Nicht die Spiegelung von A — dann sähen beide Halbschritte gleich aus
 *  und der Gang wäre ein Hüpfen. B setzt enger und um ein Pixel nach vorn versetzt auf. */
const LEGS_WALK_B = defineArt([
  "PPPPPPPP",
  "PPPPPPPP",
  ".PPPPPP.",
  "..PP.PP.",
  ".PP...PP",
  ".ii...ii",
], { P: "P", i: "ink" });

const LEGS: readonly Art[] = [LEGS_SIT, LEGS_STAND, LEGS_WALK_A, LEGS_WALK_B];

const LEGS_SIT_I = 0;
const LEGS_STAND_I = 1;
const LEGS_WALK_A_I = 2;
const LEGS_WALK_B_I = 3;

// ═══ Zeit → Bild ═════════════════════════════════════════════════════════════
//
// **Alle** Phasen kommen aus `t` (`(t / MS | 0) % n`), keine einzige aus einem Zähler.
// Das ist nicht Geschmack, sondern die dt-Split-Invarianz (Regel 3.4): live kommen Ticks im
// rAF-Takt, beim Zurückspulen in 250-ms-Schritten. Ein Zähler zählte dabei verschieden oft
// und die Zeitleiste zeigte einen anderen Raum als die Bühne.
//
// Die Zahlen stehen hier und nicht in `const.ts`: sie beschreiben, wie ein Sprite aussieht,
// nicht wie sich der Raum verhält. Wer die Schrittlänge ändert, ändert Kunst, keine Simulation.

/** Bilddauer des Laufzyklus bei Normaltempo. 4 × 120 ms = knapp eine halbe Sekunde je
 *  Doppelschritt — das passt zu `SPEED_PX_PER_S = 150` (≈45 Pufferpixel/s). */
const WALK_FRAME_MS = 120;
/** Tippen: zwei Bilder. Schneller wirkt hektisch, langsamer wie Zwei-Finger-Suchsystem. */
const TYPE_FRAME_MS = 160;
/** Gestik beim Sprechen. */
const TALK_FRAME_MS = 240;
/** Atmen — das einzige Mikro-Idle, das v1 behalten hat. Ohne es sieht ein Raum voller
 *  wartender Agenten aus wie ein Standbild, und man sucht den Fehler in der Engine. */
const BREATH_MS = 900;

const SALT_BREATH = 0x41544d4e; // "ATMN"

/** Die Atemkurve als Tabelle statt als Sinus: bei ±1 Pixel gibt es ohnehin nur zwei Werte,
 *  und eine Tabelle ist über alle Browser bitgleich. */
const BREATH: readonly number[] = [0, -1, -1, 0];

/** Ganzzahliger Phasenindex aus der Simulationszeit. */
function phase(t: number, ms: number, n: number, offset: number): number {
  const raw = ((t / ms) | 0) + offset;
  // `%` liefert bei negativem `t` negative Werte — die Engine startet zwar bei 0, aber ein
  // Aufrufer mit einem Versatz nach hinten würde sonst aus dem Feld greifen.
  return ((raw % n) + n) % n;
}

// ═══ Aktion ══════════════════════════════════════════════════════════════════

export type CharAct = "idle" | "type" | "read" | "walk" | "wait" | "talk" | "handoff" | "gaze";

/**
 * Was die Figur gerade tut — als **Tabelle**, nicht als Kaskade von Sonderfällen.
 *
 * Die Reihenfolge ist die Aussage: Gehen schlägt alles (wer läuft, tippt nicht), danach trennt
 * sich sitzend von stehend, und innerhalb beider gewinnt der spezifischere Zustand. Wer hier
 * eine Bedingung nach oben zieht, ändert nicht die Optik, sondern was der Raum behauptet.
 *
 * Abweichung vom Entwurf: dort stand für den Wartefall `waiting > 0 && waiting === busy`.
 * `ActorState.waiting` ist im fertigen Vertrag ein `boolean` (die Engine setzt ihn bei `gate`
 * und löscht ihn bei `resume`), es gibt also weder einen Zähler noch einen Zeitpunkt zu
 * vergleichen. Die Bedingung ist deshalb schlicht `a.waiting`.
 */
export function actOf(a: ActorState): CharAct {
  if (a.pose === "walk") return "walk";

  if (a.pose === "sit") {
    if (a.done !== undefined) return "idle";
    if (a.waiting) return "wait";
    if (a.act === "read" || a.act === "browse") return "read";
    if (a.act === "write" || a.act === "run" || a.act === "other") return "type";
    if (a.busy > 0) return "type";
    return "idle";
  }

  if (a.say !== undefined) return "talk";
  if (a.act === "delegate") return "handoff";
  if (a.act === "browse") return "gaze";
  return "idle";
}

// ═══ Haltung ═════════════════════════════════════════════════════════════════

/** Eine fertig ausgerechnete Körperhaltung. Reine Zahlen — `drawBody` malt sie nur noch. */
interface Stance {
  /** Index in `HEADS`. */
  dir: number;
  legs: number;
  /** Arm auf der Betrachterseite bzw. abgewandt. Zwei Werte, weil beim Tippen die Hände
   *  versetzt schlagen — mit einem Wert tippen beide Hände synchron, und das sieht aus wie
   *  Klavierspielen mit gefesselten Handgelenken. */
  armNear: number;
  armFar: number;
  /** Versatz des ganzen Körpers nach unten (Sitzen). */
  drop: number;
  /** Versatz des ganzen Körpers nach oben (Atmen, Laufwippe). */
  lift: number;
  /** Waagerechter Versatz des Oberkörpers in Blickrichtung (Vorlage beim Gehen). */
  leanX: number;
  /** Waagerechter Versatz der Arme (Schwingen beim Gehen). */
  armX: number;
  /** Zusätzliche Schuhlänge des führenden Fußes (Schrittweite aus dem Seed). */
  shoe: number;
  /** Papier vor der Brust (Lesen). */
  paper: boolean;
}

/**
 * Baut die Haltung aus Aktion, Zeit und Gangart.
 *
 * Hier werden **alle sieben** Felder von `gaitOf` benutzt. Das ist keine Vollständigkeits-
 * kosmetik: mit `speed/bob/phase` allein laufen zwölf Leute im gleichen Takt und unterscheiden
 * sich nur im Tempo — aus zwei Metern Abstand ist das eine einzige Animation. Erkennbar werden
 * sie erst durch Schrittweite, Vorlage und Armschwung, also durch die Silhouette in Bewegung.
 */
function stanceOf(
  act: CharAct, pose: Pose, t: number, look: Look, gait: Gait, seed: number,
): Stance {
  const sitting = pose === "sit";
  const breath = BREATH[phase(t, BREATH_MS / BREATH.length, BREATH.length,
    mix(seed, SALT_BREATH) % BREATH.length)];

  const s: Stance = {
    dir: sitting ? DIR_SIDE : DIR_FRONT,
    legs: sitting ? LEGS_SIT_I : LEGS_STAND_I,
    armNear: ARM_REST_I,
    armFar: ARM_REST_I,
    drop: sitting ? SIT_DROP : 0,
    lift: breath,
    leanX: 0,
    armX: 0,
    shoe: 0,
    paper: false,
  };

  if (act === "walk") {
    // Tempo aus dem Seed **verlängert die Bilddauer**, statt Bilder zu überspringen: ein
    // langsamer Läufer soll nicht ruckeln, sondern langsam gehen.
    const ms = Math.max(60, Math.round(WALK_FRAME_MS / gait.speed));
    const f = phase(t, ms, 4, Math.round(gait.phase * 4));
    // Führender Fuß aus dem Aussehen: sonst setzen alle zwölf mit demselben Bein an.
    const lead = (look.legs & 1) === 0;
    const strideFrames: readonly number[] = lead
      ? [LEGS_STAND_I, LEGS_WALK_A_I, LEGS_STAND_I, LEGS_WALK_B_I]
      : [LEGS_STAND_I, LEGS_WALK_B_I, LEGS_STAND_I, LEGS_WALK_A_I];
    s.dir = DIR_SIDE;
    s.legs = strideFrames[f];
    // Wippen: hoch in der Durchgangsstellung, tief im vollen Schritt. `bob` ist 0.35..1.
    s.lift = f % 2 === 0 ? -Math.max(1, Math.round(gait.bob * 2)) : 0;
    s.leanX = Math.round(gait.lean * 2);
    s.shoe = f % 2 === 1 ? gait.stride - 2 : 0;
    const af = phase(t, ms, 4, Math.round(gait.armPhase * 4));
    s.armX = af === 1 ? gait.swing : af === 3 ? -gait.swing : 0;
    s.armNear = ARM_REST_I;
    s.armFar = ARM_REST_I;
    return s;
  }

  if (act === "type") {
    const f = phase(t, TYPE_FRAME_MS, 2, 0);
    s.armNear = f === 0 ? ARM_TYPE_A_I : ARM_TYPE_B_I;
    s.armFar = f === 0 ? ARM_TYPE_B_I : ARM_TYPE_A_I;
    return s;
  }

  if (act === "read") {
    // Im Profil, weil ein Blatt vor dem Gesicht bei Vorderansicht wie ein Latz aussieht.
    s.dir = DIR_SIDE;
    s.armNear = ARM_REACH_I;
    s.armFar = ARM_REACH_I;
    s.paper = true;
    return s;
  }

  if (act === "wait") {
    // Gewichtsverlagerung im Takt des Gate-Pulses. Derselbe Rhythmus wie das Wartesignal über
    // dem Kopf — zwei Signale, die aus derselben Konstante kommen, lesen sich als ein Zustand.
    s.leanX = phase(t, GATE_PULSE_MS, 2, 0) === 0 ? 0 : 1;
    return s;
  }

  if (act === "talk") {
    const f = phase(t, TALK_FRAME_MS, 2, 0);
    s.armNear = f === 0 ? ARM_REACH_I : ARM_REST_I;
    return s;
  }

  if (act === "handoff") {
    s.dir = DIR_SIDE;
    s.armNear = ARM_REACH_I;
    s.armFar = ARM_REACH_I;
    s.leanX = 1;
    return s;
  }

  if (act === "gaze") {
    s.dir = DIR_SIDE;
    // Ein Pixel Kopfnicken alle 800 ms: der Unterschied zwischen „schaut" und „ist eingefroren".
    s.lift = breath + (phase(t, 800, 2, 0) === 0 ? 0 : 1);
    return s;
  }

  // idle: im Stehen verlagert sich das Gewicht je nach Seed auf ein Bein.
  if (!sitting && (look.legs & 2) !== 0) s.leanX = 1;
  return s;
}

// ═══ Zeichnen ════════════════════════════════════════════════════════════════

/**
 * Der Kontaktschatten. Ohne ihn schwebt jede Figur einen Hauch über den Dielen — der Effekt
 * ist winzig, fällt aber sofort auf, weil das Auge Bodenhaftung an genau dieser Kante prüft.
 *
 * Drei flache Rechtecke statt eines weichen Flecks: `shadowBlur` ist verboten (Regel 2.1) und
 * wäre bei 480×270 ohnehin Matsch.
 */
export function drawShadow(ctx: Ctx, cx: number, yBase: number, pal: Pal, w: number): void {
  const half = w >> 1;
  fillA(ctx, pal, "shadow", 0.22, cx - half + 1, yBase - 1, w - 2, 1);
  fillA(ctx, pal, "shadow", 0.14, cx - half, yBase, w, 1);
  fillA(ctx, pal, "shadow", 0.07, cx - half - 1, yBase + 1, w + 2, 1);
}

/** Gesichtsdetail aus `look.head`.
 *
 *  Warum nicht drei Kopf-Arts je Richtung: die drei Köpfe sind **Blickrichtungen**, keine
 *  Personen — `look.head` hätte sonst gar keine Wirkung und wäre ein totes Feld im Seed.
 *  Mund und Bart kosten zwei `fillRect` und geben jeder zweiten Figur ein eigenes Gesicht. */
function face(ctx: Ctx, pal: Pal, cx: number, headTop: number, variant: number, dir: number): void {
  if (dir === DIR_BACK) return;
  const hx = cx - 5;
  if (variant === 1) {
    fill(ctx, pal, "s", hx + 4, headTop + 6, 2, 1);
  } else if (variant === 2) {
    // Kinnbart: **eine** Zeile am Kinn, vier Pixel breit. Zwei Zeilen über sechs Spalten
    // (erste Fassung) lasen sich aus der Entfernung als schwarzer Balken quer durchs Gesicht —
    // bei einem 10 Pixel breiten Kopf ist jede zweite Spalte ein Drittel des Gesichts.
    fill(ctx, pal, "h", hx + 3, headTop + 7, 4, 1);
  }
}

/** Ein Arm samt Ärmellänge. `sleeve` 0/1 = lang, 2 = kurz, 3 = hochgekrempelt. */
function arm(
  ctx: Ctx, pal: Pal, idx: number, cx: number, yBase: number,
  flip: boolean, sleeve: number, alpha: number,
): void {
  drawArt(ctx, ARMS[idx], cx, yBase, pal, { flip, alpha });
  if (sleeve >= 2) {
    const fore = ARM_FORE[idx];
    // Bei hochgekrempelt bleibt eine Zeile Stoff mehr stehen — dafür wird das Unterarm-Art
    // noch einmal um eine Zeile gekürzt.
    const rows = sleeve === 3 ? fore.rows.slice(1) : fore.rows;
    if (rows.length > 0) {
      drawArt(ctx, { rows, map: fore.map }, cx, yBase, pal, { flip, alpha, tint: "S" });
    }
  }
}

/** Setzt die Figur aus ihren Teilen zusammen. Reihenfolge = Verdeckung: Beine, Torso, der
 *  abgewandte Arm, Kopf, Gesicht, der nahe Arm, Haar. Der abgewandte Arm liegt **hinter** dem
 *  Torso, der nahe davor — das ist der einzige Tiefenhinweis, den eine 14 Pixel breite Figur
 *  überhaupt geben kann. */
function drawBody(
  ctx: Ctx, cx: number, yBase: number, pal: Pal, look: Look, s: Stance,
  flip: boolean, alpha: number,
): void {
  const dirSign = flip ? -1 : 1;
  const bodyY = yBase + s.drop + s.lift;
  const legsY = yBase;

  const torsoY = bodyY - 5;
  const headY = torsoY - 10;
  const armY = torsoY - 3;
  const hairY = headY + 2;

  const bodyX = cx + s.leanX * dirSign;
  const armXNear = bodyX + (5 + s.armX) * dirSign;
  const armXFar = bodyX - (5 - s.armX) * dirSign;

  // Beine: der führende Schuh wird um `shoe` Pixel verlängert — die Schrittweite aus dem Seed,
  // ohne dafür ein zweites Bein-Art zu brauchen.
  drawArt(ctx, LEGS[s.legs], cx, legsY, pal, { flip, alpha });
  if (s.shoe > 0) {
    // Nach links laufend wächst der Schuh nach links: eine mit `dirSign` multiplizierte
    // Breite wäre negativ, und `fill` verwirft negative Breiten stillschweigend — der
    // Schritt der linkslaufenden Hälfte des Raums wäre dann einfach kürzer.
    const sx = flip ? cx - 4 - s.shoe : cx + 4;
    if (alpha < 1) ctx.globalAlpha = alpha;
    fill(ctx, pal, "ink", sx, legsY - 1, s.shoe, 1);
    if (alpha < 1) ctx.globalAlpha = 1;
  }

  drawArt(ctx, TORSOS[look.torso], bodyX, torsoY, pal, { flip, alpha });
  arm(ctx, pal, s.armFar, armXFar, armY, !flip, look.arms, alpha);
  drawArt(ctx, HEADS[s.dir], bodyX, headY, pal, { flip, alpha });
  if (alpha >= 1) face(ctx, pal, bodyX, headY - 9, look.head, s.dir);

  if (s.paper) {
    // Das Blatt in der Hand. Ein einzelnes helles Rechteck vor der Brust reicht: „liest" ist
    // sonst von „tippt" nicht zu unterscheiden, weil beide Arme nach vorn zeigen.
    // Vor dem Körper, nicht auf ihm: auf der Brust läse es sich als Namensschild.
    const px = bodyX + dirSign * 5 - 2;
    if (alpha < 1) ctx.globalAlpha = alpha;
    fill(ctx, pal, "paper", px, torsoY - 8, 5, 5);
    fill(ctx, pal, "ink", px + 1, torsoY - 7, 3, 1);
    fill(ctx, pal, "ink", px + 1, torsoY - 5, 2, 1);
    if (alpha < 1) ctx.globalAlpha = 1;
  }

  arm(ctx, pal, s.armNear, armXNear, armY, flip, look.arms, alpha);
  drawArt(ctx, HAIRS[look.hair], bodyX, hairY, pal, { flip, alpha });
}

/**
 * Zeichnet einen Aktor an seiner Szenenposition.
 *
 * Die einzige Stelle in dieser Datei, an der `POS_SCALE` vorkommt — und sie skaliert eine
 * **Position**, keine Größe (Regel 1). Gerundet wird hier, nicht in der Engine: die rechnet
 * mit einem Subpixel-Akkumulator weiter, damit auch winzige `dt` vorankommen.
 *
 * `pal` ist die **bereits aufgelöste Palette dieser Figur** (`palFor(grade, lookOf(a.seed))`).
 * Sie hier selbst aufzulösen wäre ein Objekt-Spread über 36 Schlüssel je Figur und Bild.
 */
export function drawActor(ctx: Ctx, a: ActorState, t: number, pal: Pal): void {
  const cx = Math.round(a.x * POS_SCALE);
  const yBase = Math.round(a.y * POS_SCALE);
  const look = lookOf(a.seed);
  const gait = gaitOf(a.seed);
  const act = actOf(a);
  const s = stanceOf(act, a.pose, t, look, gait, a.seed);

  // Der Chefplatz ist der einzige Sitz, dessen Figur **vor** ihrem Schreibtisch sitzt
  // (`room.ts`: `sit = desk + SEAT_DY`, gleiche x-Mitte). Sie blickt also von uns weg — das
  // ist keine Stilentscheidung, sondern die Geometrie des Raums.
  if (a.pose === "sit" && a.deskIndex === -1) s.dir = DIR_BACK;

  drawShadow(ctx, cx, yBase, pal, a.pose === "sit" ? 10 : 12);
  drawBody(ctx, cx, yBase, pal, look, s, a.flip, 1);
}

/**
 * Ein Agent ohne Schreibtisch (`deskIndex === -2`).
 *
 * Halbdurchsichtig und ohne Kontaktschatten: er gehört zum Lauf, hat aber keinen Platz im
 * Raum. Ihn wie alle anderen zu zeichnen wäre eine Lüge (er hat keinen Stuhl), ihn wegzulassen
 * eine zweite (er arbeitet ja). Der Geist ist die einzige ehrliche Darstellung — und man sieht
 * sofort, dass der Raum voll ist.
 */
export function drawGhost(
  ctx: Ctx, cx: number, yBase: number, pal: Pal, t: number, seed: number,
): void {
  const look = lookOf(seed);
  const gait = gaitOf(seed);
  const s = stanceOf("idle", "stand", t, look, gait, seed);
  // Zusätzliches Schweben, damit der Geist auch im Standbild als Geist liest.
  s.lift += phase(t, 700, 2, mix(seed, SALT_BREATH) % 2) === 0 ? 0 : -1;
  drawBody(ctx, cx, yBase, pal, look, s, false, 0.45);
}

/** Nur für die Trefferprüfung der Bühne: die Fläche, die eine Figur belegt. Kein Zeichnen,
 *  damit `scene.ts` nicht die Maße aus dem Kommentarkopf abschreiben muss. */
export function actorBox(cx: number, yBase: number): {
  x: number; y: number; w: number; h: number;
} {
  return { x: cx - (FIG_W >> 1), y: yBase - FIG_H, w: FIG_W, h: FIG_H + 2 };
}
