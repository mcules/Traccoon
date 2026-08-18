// Layer 0: deterministic "randomness".
//
// The room has to be identically reconstructable from the same log: rewinding means "new
// engine, replay the log from the start". Every variation in the picture (hairstyle, skin
// tone, stride length, wobble phase, seat, which line) is therefore a **pure function of the
// seed**, never a throw. `Math.random` does not occur in layers 0 and 1; the checker enforces that.
//
// Everything computes with integers (`Math.imul`, `>>> 0`). Floating point accumulation would
// be reproducible in practice as well, but it hides rounding drift; integers do not.
//
// Siehe PIXEL-CONTRACT.md Regel 3.2.

/** FNV-1a, 32 bit. Every UTF-16 code unit goes in with **both** bytes: Traccoon's role and
 *  tool names are German (`erinnere_dich`, `gedaechtnis_suchen`, umlauts in titles), and an
 *  `& 0xff` alone would let "ä" and "ö" fall onto the same value. */
export function hash32(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    h = Math.imul(h ^ (c & 0xff), 0x01000193);
    h = Math.imul(h ^ (c >>> 8), 0x01000193);
  }
  return h >>> 0;
}

/** Re-spreads a hash with a salt (the finalizer from MurmurHash3).
 *
 *  Every place of use gets its **own, named** `SALT`. Two places with the same salt are
 *  perfectly correlated, and then all slow walkers have red hair, which only shows once
 *  twelve figures stand in the room. */
export function mix(h: number, salt: number): number {
  let x = (h ^ Math.imul(salt >>> 0, 0x9e3779b1)) >>> 0;
  x = Math.imul(x ^ (x >>> 16), 0x85ebca6b) >>> 0;
  x = Math.imul(x ^ (x >>> 13), 0xc2b2ae35) >>> 0;
  return (x ^ (x >>> 16)) >>> 0;
}

/** Hash to `[0, 1)`. Divides by 2^32, not by `0xffffffff`; otherwise 1.0 would be reachable
 *  and `pick`-like computations would run one index too far. */
export function rnd01(h: number): number {
  return (h >>> 0) / 4294967296;
}

/** Chooses an element. Modulo on the integer, without the detour over `rnd01`, which saves
 *  the one floating point rounding at which two browsers could theoretically drift apart.
 *  `arr` must not be empty; an empty array is a caller error. */
export function pick<T>(arr: readonly T[], h: number): T {
  return arr[(h >>> 0) % arr.length];
}
