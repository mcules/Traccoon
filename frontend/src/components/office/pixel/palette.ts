// Layer 1, the colours of the office. A flat table, two grades, no arithmetic at runtime.
//
//
// Why a table and not CSS variables: the drawing layer sets `fillStyle` a few hundred times
// per frame. Every colour that would first have to be read from the document would be a
// layout read in the middle of the drawing loop, the most expensive mistake this feature can
// make, and forbidden in layer 1 on top of that (rule 3.1). Colours therefore stand here as
// hex strings, and `resolve()` runs **once per theme change**, never per frame.
//
// The two grades are bound to Traccoon's theme, not to the clock:
//
//   data-theme="dark"  →  "night"  = evening office: desk lamps on, the monitors are the
//                                    dominant light source, the windows stand deep blue.
//   data-theme="light" →  "day"    = day office: warm off-white, window glare, lamps off.
//
// This is explicitly **not a time-of-day simulation**. Reading the real clock would break
// determinism (the same log would give different pictures in the morning and in the evening,
// rewinding would no longer be bit identical), and it would confuse the viewer: they would
// see "evening" although the run they are watching took place at 9 in the morning. The room
// shows the run, not the end of the working day.

import type { Gait, Grade, Look } from "../types.ts";
import { hash32, mix, rnd01 } from "../ids.ts";
import { PACE_SPREAD } from "../const.ts";

// ── The keys ─────────────────────────────────────────────────────────────────

/** Every colour of the room has a name; hex values stand exclusively in the two tables below.
 *  The last seven are **reserved**: they belong to no scenery but to the figure being drawn
 *  right now and are only filled in by `resolve(grade, look)`. */
export type PalKey =
  // Scenery
  | "wall" | "wallLo" | "wallHi"
  | "floor" | "floorLo" | "floorHi"
  | "rug" | "rugLo"
  /** What can be seen through the window: sky respectively night city. */
  | "out"
  // Furniture
  | "desk" | "deskLo" | "chair" | "chairLo" | "metal" | "glass" | "clay"
  // Screens and paper
  | "screen" | "screenLit" | "ink" | "paper"
  // Green
  | "plant" | "plantLo" | "soil"
  // Licht
  | "lamp" | "shadow"
  // Zustandsfarben (Blasenrand, Monitorschein, Gate-Puls)
  | "acc" | "ok" | "err" | "blocked"
  // ── reserved: the figure ───────────────────────────────────────────────────
  /** Haut. */        | "S"
  /** Haut, Schatten. */ | "s"
  /** Haar. */        | "H"
  /** Haar, Schatten. */ | "h"
  /** Oberteil. */    | "T"
  /** Oberteil, Schatten. */ | "t"
  /** Hose. */        | "P";

/** Resolved palette: every key points at a finished hex value. */
export type Pal = Record<PalKey, string>;

/** Everything except the seven reserved keys, which is what the grade tables hold. */
type EnvKey = Exclude<PalKey, "S" | "s" | "H" | "h" | "T" | "t" | "P">;

// ── The two tables ───────────────────────────────────────────────────────────
//
// Set by hand, not computed. A night picture as "day picture x 0.4" looks like a darkened
// photo: the monitors would be dimmed along with everything else although in the evening
// office they are exactly the light source that makes everything else visible in the first
// place. Hence two tables in which `screenLit` and `lamp` grow **brighter** while the rest falls.

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
  // Deliberately **not** darker than during the day: in the evening office the monitors are the lamps.
  screen: "#161b22", screenLit: "#cfe3f5", ink: "#2b3444", paper: "#cfc9bb",
  plant: "#2f5c3a", plantLo: "#20402a", soil: "#33261b",
  lamp: "#ffd88a", shadow: "#080c14",
  // Status colours stay **identical** in both grades: they are the same four colours as in the
  // dock (AgentMonitor: yellow/green/red/orange). Two views of the same run must not
  // contradict each other just because one of them plays in the evening.
  acc: "#38bdf8", ok: "#4ade80", err: "#f87171", blocked: "#fb923c",
};

// ── The figure: tones `lookOf` chooses from ──────────────────────────────────
//
// `Look.skin`/`hairCol`/`shirtCol`/`pantsCol` are **palette keys**, not CSS colours (that is
// what the contract says), names from this table. That keeps `Look` serialisable and
// independent of the grade: the same figure is the same figure in the evening, only darker.

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

/** How far the shadow tone sits below the base tone. 0.70 is the limit from which the edge at
 *  16x24 still reads as a fold and not as a hole. */
const SHADE = 0.70;

/** At night people are not coloured differently, only lit differently: around 18 % darker and
 *  8 % cooler. Both computed instead of typed, because otherwise it would be 26 additional hex
 *  values to maintain twice on every colour change. */
const NIGHT_DARK = 0.82;
const NIGHT_COOL = 0.08;

// ── Colour arithmetic (pure, rounded to integers) ────────────────────────────

function clamp255(v: number): number {
  return v < 0 ? 0 : v > 255 ? 255 : Math.round(v);
}

function hex2(v: number): string {
  const s = clamp255(v).toString(16);
  return s.length === 1 ? "0" + s : s;
}

/** `#rrggbb` to three channels. The short form (`#abc`) deliberately does not occur in the tables. */
function parse(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];
}

/** Multiplies all channels: the shadow tone of a sprite part. */
function darken(hex: string, f: number): string {
  const [r, g, b] = parse(hex);
  return "#" + hex2(r * f) + hex2(g * f) + hex2(b * f);
}

/** Cools a colour: red falls the most, blue moves towards white. That is the cheapest credible
 *  imitation of moonlight; plain dimming makes skin tones look muddy instead of evening-lit. */
function cool(hex: string, k: number): string {
  const [r, g, b] = parse(hex);
  return "#" + hex2(r * (1 - k)) + hex2(g * (1 - k * 0.5)) + hex2(b + (255 - b) * k * 0.5);
}

/** The person tone in the respective grade. */
function toneOf(key: string, fallback: string, grade: Grade): string {
  const hex = TONES[key] ?? fallback;
  return grade === "night" ? cool(darken(hex, NIGHT_DARK), NIGHT_COOL) : hex;
}

// ── The two finished grades ──────────────────────────────────────────────────

/** Default figure for the case that resolving happens without a `Look` (furniture preview,
 *  test pictures). Never visible in the running room, where every actor has their `Look`. */
function defaultPerson(grade: Grade): Pick<Pal, "S" | "s" | "H" | "h" | "T" | "t" | "P"> {
  const S = toneOf("skin1", "#e3b184", grade);
  const H = toneOf("hair1", "#4b3521", grade);
  const T = toneOf("shirt0", "#3f6fb0", grade);
  const P = toneOf("pants0", "#3a4152", grade);
  return { S, s: darken(S, SHADE), H, h: darken(H, SHADE), T, t: darken(T, SHADE), P };
}

/** The two grades, fully resolved. Constant over the runtime: whoever changes something here
 *  changes it for all pictures at once. */
export const GRADES: Record<Grade, Pal> = {
  day: { ...DAY_ENV, ...defaultPerson("day") },
  night: { ...NIGHT_ENV, ...defaultPerson("night") },
};

// ── Appearance from the seed ─────────────────────────────────────────────────
//
// Every trait gets its **own named salt** (rule 3.2). Two traits on the same salt would be
// perfectly correlated, and then all blond figures would wear the same shirt, which only
// shows once twelve figures stand in the room.

const SALT_HEAD = 0x4b4f5046;   // "HEAD"
const SALT_HAIR = 0x48414152;   // "HAIR" - the **shape** of the hairstyle (individual)
const SALT_HAIRC = 0x48414146;  // "HAIC" - the **colour** of the hair (from the role)
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

/** Number of hair shapes (art parts) and hair colours. The product used to be the supply of
 *  hairstyles; since shape and colour come from two different sources (see `lookOf`) these
 *  are two independent supplies. */
const HAIR_SHAPES = 5;
const HAIR_COLORS = 8;

// ── The appearance seed: what the role determines ────────────────────────────

const SALT_ROLE = 0x524f4c4c;  // "ROLL"

/**
 * The seed the **appearance** comes from, not to be confused with `ActorState.seed`, which
 * everything individual comes from.
 *
 * Why two seeds at all: `ActorState.seed` is `hash32("run:8871")`, that is the **run** id.
 * With that, the same `developer` looked different yesterday than today, and nobody could be
 * recognised. Out of `hash32(role)` on the other hand falls the same colour for a role forever.
 *
 * Deliberately **no role colour table**: the real roles (`developer`, `assistent`,
 * `architect`, `code_reviewer`, `project_manager`, `gameproj-operator`, `news`) are data, not an
 * enumeration. A table would need maintenance with every new agent and would have no entry at
 * all for the test fixture (`exec_agent`/`plan_agent`/`review_agent`).
 *
 * An empty role means the run seed. A nameless figure should not make all nameless figures
 * alike; and because `role === seed` hits exactly the old salts again, the role-less case is
 * bit identical to the previous behaviour.
 */
export function rolesSeed(role: string, seed: number): number {
  return role ? mix(hash32(role), SALT_ROLE) : seed;
}

/**
 * The appearance of a figure: a pure function of two seeds, therefore identical over live and replay.
 *
 * **The split is the whole point.** From `role` come exactly the three traits that can be read
 * from three metres away at all:
 *
 *   · **shirt colour**, the largest contiguous colour area of a 16x24 sprite,
 *   · **hair colour**, the second largest; together with the shirt a coat of arms,
 *   · **torso shape**, the shoulder silhouette that carries the role from behind as well
 *     (the chief's seat sits with `DIR_BACK` towards the viewer).
 *
 * Everything else hangs off the run seed: head, skin, arms, legs, **hair shape**, trouser
 * colour. Otherwise twelve `developer` would stand in the room as twelve clones, and
 * recognition that no longer allows individuals is not recognition but a uniform.
 *
 * The earlier trick of drawing hair shape and colour from **one** hash (remainder and quotient
 * over `HAIR_SHAPES × HAIR_COLORS`) is thereby obsolete: the two now lie on different seeds
 * anyway and necessarily vary independently. They do need **salts of their own** for that: with
 * the same salt they would be perfectly correlated in the role-less case (`role === seed`), and
 * then every hairstyle shape would have exactly one colour.
 *
 * The seat deliberately stays on the run id (`seatOf(a.id)`, layer 0): `seatOf` probes
 * linearly, so twelve `developer` would otherwise get twelve **consecutive** seats and the left
 * bench would be a monoculture.
 */
export function lookOf(seed: number, role: number): Look {
  return {
    head: mix(seed, SALT_HEAD) % 3,
    hair: mix(seed, SALT_HAIR) % HAIR_SHAPES,
    torso: mix(role, SALT_TORSO) % 3,
    arms: mix(seed, SALT_ARMS) % 4,
    legs: mix(seed, SALT_LEGS) % 4,
    skin: "skin" + (mix(seed, SALT_SKIN) % 6),
    hairCol: "hair" + (mix(role, SALT_HAIRC) % HAIR_COLORS),
    shirtCol: "shirt" + (mix(role, SALT_SHIRT) % 8),
    pantsCol: "pants" + (mix(seed, SALT_PANTS) % 5),
  };
}

/**
 * The gait, likewise purely from the seed.
 *
 * Seven values, because recognisability runs over the movement: at 16 pixels wide you only see
 * the hairstyle when you look, but the walk immediately. `armPhase` deliberately sits about
 * half a period beside `phase` (arms and legs swing in opposition); with the same phase
 * everybody marches.
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

// ── Resolving ────────────────────────────────────────────────────────────────

/**
 * Builds the finished palette: scenery from the grade, the seven reserved keys from the
 * `Look`. Without a `Look` the default figure is used.
 *
 * **Once per theme change respectively per figure, never per frame.** An object spread over 36
 * keys is cheap, 24 figures × 60 frames/s of them are not, which is what `palFor` below is for.
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

/** Identity of a `Look` for the cache: only the four colour keys, because only those land in
 *  the palette. Two figures with the same colours therefore share a palette even when their
 *  hairstyle differs. */
function lookKey(grade: Grade, look: Look): string {
  return grade + "|" + look.skin + "|" + look.hairCol + "|" + look.shirtCol + "|" + look.pantsCol;
}

const PAL_CACHE = new Map<string, Pal>();

/**
 * `resolve` with memory, **this** is the version the drawing loop uses.
 *
 * The cache is pure allocation saving: same input, same result, no state that shows up in the
 * picture (rule 3 stays intact). It is cleared when it grows beyond 64 entries: there cannot be
 * more than `MAX_ACTORS` different colour sets on the stage, but there can be over a long
 * session with many runs coming and going. A map that never clears is a leak with a run-up.
 *
 * **The cap of 64 holds with role-fixed colours too.** `lookKey` sees four keys; two of them
 * (shirt, hair) are constant per role, the other two vary individually over 6 skin tones × 5
 * trousers = **30 palettes per role and grade**. What matters is not the upper bound over the
 * session but the one per **frame**: there at most `MAX_ACTORS = 24` figures ask, so the entries
 * of one frame always fit under 64. A `clear()` can therefore never strike in the middle of a
 * frame and repeat frame after frame, which would silently have turned the cache into one
 * allocation per frame.
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
