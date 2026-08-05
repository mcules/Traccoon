// Schicht 0 — deterministischer „Zufall".
//
// Der Raum muss aus demselben Log identisch wiederherstellbar sein: Zurückspulen heißt
// „neue Engine, Log von vorn abspielen". Damit ist jede Variation im Bild — Frisur, Hautton,
// Schrittlänge, Wackelphase, Sitzplatz, welcher Spruch — eine **reine Funktion des Seeds**,
// nie ein Wurf. `Math.random` kommt in den Schichten 0 und 1 nicht vor; der Prüfer erzwingt das.
//
// Alles rechnet ganzzahlig (`Math.imul`, `>>> 0`). Fließkomma-Akkumulation wäre zwar in der
// Praxis auch reproduzierbar, aber sie versteckt Rundungsdrift — Ganzzahlen tun das nicht.
//
// Siehe PIXEL-CONTRACT.md Regel 3.2.

/** FNV-1a, 32 Bit. Jedes UTF-16-Code-Unit geht mit **beiden** Bytes ein — Traccoons Rollen- und
 *  Werkzeugnamen sind deutsch (`erinnere_dich`, `gedaechtnis_suchen`, Umlaute in Titeln), und
 *  ein `& 0xff` allein ließe „ä" und „ö" auf denselben Wert fallen. */
export function hash32(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    h = Math.imul(h ^ (c & 0xff), 0x01000193);
    h = Math.imul(h ^ (c >>> 8), 0x01000193);
  }
  return h >>> 0;
}

/** Streut einen Hash mit einem Salz neu (Finalizer aus MurmurHash3).
 *
 *  Jede Verwendungsstelle bekommt ihr **eigenes, benanntes** `SALT`. Zwei Stellen mit demselben
 *  Salz sind perfekt korreliert — dann haben alle langsamen Läufer rote Haare, und das fällt erst
 *  auf, wenn zwölf Figuren im Raum stehen. */
export function mix(h: number, salt: number): number {
  let x = (h ^ Math.imul(salt >>> 0, 0x9e3779b1)) >>> 0;
  x = Math.imul(x ^ (x >>> 16), 0x85ebca6b) >>> 0;
  x = Math.imul(x ^ (x >>> 13), 0xc2b2ae35) >>> 0;
  return (x ^ (x >>> 16)) >>> 0;
}

/** Hash → `[0, 1)`. Teilt durch 2^32, nicht durch `0xffffffff` — sonst wäre 1.0 erreichbar
 *  und `pick`-artige Rechnungen liefen einen Index zu weit. */
export function rnd01(h: number): number {
  return (h >>> 0) / 4294967296;
}

/** Wählt ein Element. Modulo auf der Ganzzahl, kein Umweg über `rnd01` — das spart die eine
 *  Fließkomma-Rundung, an der zwei Browser theoretisch auseinanderlaufen könnten.
 *  `arr` muss nicht leer sein; ein leeres Feld ist ein Aufruferfehler. */
export function pick<T>(arr: readonly T[], h: number): T {
  return arr[(h >>> 0) % arr.length];
}
