# Pixel-Vertrag — Traccoon „Büro"

Dies ist das Regelwerk für alles unter `src/components/office/`. Es ist kein Stilratgeber,
sondern die Zusage, auf der sieben parallel gebaute Teile zusammenpassen. `frontend/tools/office-check.mjs`
erzwingt die Regeln maschinell — was hier steht, bricht dort den Lauf.

Jede Regel steht mit ihrer Begründung da. Wer eine Regel brechen will, muss die Begründung
widerlegen, nicht die Regel übersehen.

---

## Regel 1 — Der Bildpuffer ist fest 480×270 (die wichtigste Regel)

Es gibt genau eine Auflösung, in der gezeichnet wird: **480×270 Pufferpixel**. Der sichtbare
Canvas ist beliebig groß; er bekommt den Puffer ganzzahlig hochskaliert
(`imageSmoothingEnabled = false`).

Die Simulation dagegen läuft in **`SCENE = 1600×900`**. Zwischen beiden steht `POS_SCALE = 0.3`:

> **`POS_SCALE` gilt für Positionen. Für Sprites gilt es nicht.**

Eine Figur ist **16×24 Pufferpixel** — ein knappes Elftel der Bildhöhe. Sie ist *nicht* 16×24
Szenenpixel, die dann auf 5×7 schrumpfen. Wer die Sprites mitskaliert, malt Figuren mit 35 statt
384 Pixeln und rechnet damit das gesamte Kunstbudget (≤20 KB, ~36 Arts) um den Faktor 3 falsch —
und merkt es erst, wenn die Arts fertig und unbrauchbar sind.

Praktisch heißt das in jeder Zeichenfunktion:

```ts
const px = Math.round(actor.x * POS_SCALE);   // Position: skaliert
const py = Math.round(actor.y * POS_SCALE);   // Position: skaliert
drawPerson(ctx, px, py, look, pose);          // Sprite: 16×24 Pufferpixel, ungeskaliert
```

Möbel, Blasen, Schrift, Partikel: ebenfalls in Pufferpixeln. `POS_SCALE` taucht in Schicht 1
höchstens auf, um eine Szenenkoordinate hereinzuholen — nie, um eine Größe zu berechnen.

---

## Regel 2 — Zeichenregeln (Schicht 1)

### 2.1 Drei Werkzeuge, mehr nicht

Auf dem 2D-Kontext existieren für Schicht 1 genau drei Dinge:

| erlaubt | verboten |
|---|---|
| `ctx.fillStyle` | `beginPath`, `moveTo`, `lineTo`, `arc`, `arcTo`, `ellipse`, `rect`, `fill`, `stroke`, `clip` |
| `ctx.globalAlpha` | `createLinearGradient`, `createRadialGradient`, `createPattern` |
| `ctx.fillRect` | `drawImage`, `putImageData`, `getImageData` |
| | `shadowBlur`, `shadowColor`, `filter`, `globalCompositeOperation` |
| | `save`, `restore`, `translate`, `scale`, `rotate`, `setTransform` |
| | `fillText`, `strokeText`, `measureText` |

Gründe: Pfade und Verläufe rastern auf Subpixel-Kanten und zerstören die Pixeloptik; `save`/`restore`
schleppen einen Zustandsstapel mit, der die goldenen Ops-Hashes von der Aufrufreihenfolge abhängig
macht; `fillText` liefert je Plattform andere Pixel und wäre damit nicht mehr golden prüfbar.
Schrift ist ein Art, kein Font.

Kurven gibt es trotzdem — als **gestufte `fillRect`-Läufe**: pro Zeile eine Kante berechnen, eine
Rechteckzeile setzen. Ein Kreis ist eine Tabelle von Halbbreiten, kein `arc`.

`globalAlpha` wird nach jedem Block wieder auf `1` gesetzt. Da es kein `restore` gibt, ist das
Zurücksetzen die Pflicht des Aufrufers — ein vergessenes `globalAlpha` färbt den Rest des Bildes.

Der Prüfer stellt der Zeichenschicht einen `ctx`-Proxy hin, der bei jedem anderen Zugriff wirft.

### 2.2 Die Signatur `(ctx, cx, yBase, …)`

Jede Weltzeichenfunktion (alles, was in der Szene steht: Figuren, Möbel, Requisiten) hat die Form

```ts
function drawX(ctx: Ctx, cx: number, yBase: number, …): void
```

- **`cx`** = waagerechte **Mitte** des Objekts in Pufferpixeln.
- **`yBase`** = die Pufferzeile, **auf der das Objekt steht** — die erste Zeile *unter* dem Sprite.
  Ein 24 Pixel hohes Sprite belegt also `yBase-24 … yBase-1` und beginnt mit
  `ctx.fillRect(cx - 8, yBase - 24, …)`.

Das ist keine Geschmacksfrage: die Szene sortiert vor dem Zeichnen nach `yBase` (Maler-Algorithmus,
hinten zuerst). Ein Objekt, das über seinen Fußpunkt lügt — etwa seine Oberkante als `y` übergibt —
sortiert falsch und verschwindet hinter Möbeln, vor denen es steht. Der Fehler sieht nach einem
Tiefenproblem aus und ist ein Signaturproblem.

`Frame.actors` kommt aus der Engine bereits nach `y` sortiert. Wer eigene Objekte einmischt, sortiert
mit demselben Schlüssel.

### 2.3 Bewegung nur in ganzen Pixeln

Gezeichnet wird ausschließlich auf ganzzahligen Pufferkoordinaten. Ein `fillRect` mit `x = 12.4`
lässt der Browser über zwei Spalten mit halber Deckkraft laufen — bei einer laufenden Figur flimmert
das sichtbar, bei einem Möbel verwischt die Kante.

Gerundet wird **beim Rendern** (`Math.round` nach `× POS_SCALE`), nicht in der Engine. Die Engine
rechnet in Szenenpixeln mit einem Subpixel-Akkumulator (`ActorState.sub`) weiter — siehe Regel 3.

---

## Regel 3 — Determinismus (Schicht 0 und Schicht 1)

Der Raum muss aus dem Ereignis-Log heraus **bitgleich** wiederherstellbar sein: Zurückspulen ist
„neue Engine, Log von vorn abspielen". Alles, was nicht aus dem Log kommt, bricht das.

### 3.1 Verbotene Bezeichner

In den Schichten 0 und 1 kommen diese Zeichenketten nicht vor (Prüfer: Reinheits-Grep, Kommentare
werden vorher entfernt):

```
Math.random   Date.now   performance.now   new Date
window.   document.   localStorage   toLocale
```

`Math.random` ist offensichtlich. Die Uhren sind es weniger: eine Animationsphase aus
`performance.now()` sieht live richtig aus und liefert beim Zurückspulen ein anderes Bild als beim
ersten Mal. `toLocale*` hängt an der Zeitzone und der Sprache des Browsers — dasselbe Log ergäbe
in zwei Tabs zwei verschiedene Zeitleisten. Formatierung gehört nach Schicht 2.

### 3.2 Variation kommt aus dem Seed

Jede „Zufälligkeit" — Frisur, Hautton, Schrittlänge, Wackelphase, welcher Stuhl, welcher Spruch —
ist eine reine Funktion aus `ActorState.seed`:

```ts
const gait = 1 + (rnd01(mix(seed, SALT_PACE)) - 0.5) * 2 * PACE_SPREAD;
```

`seed` selbst ist `hash32(agentId)`. Jede Verwendungsstelle bekommt ihr **eigenes** `SALT` als
benannte Konstante; zwei Stellen mit demselben Salz sind korreliert (alle Langsamen haben rote
Haare), und das fällt erst spät und peinlich auf.

### 3.3 Subpixel-Akkumulator

Positionen werden als Fließkomma integriert und **nur beim Rendern** gerundet:

```ts
a.sub.x += vx * dt;                       // Bruchteile bleiben erhalten
const step = Math.trunc(a.sub.x);
a.x += step; a.sub.x -= step;             // ganze Szenenpixel wandern nach x
```

Wer stattdessen pro Tick rundet, verliert bei kleinen `dt` jede Bewegung (`round(0.4) = 0`) und
verletzt damit sofort Regel 3.4.

### 3.4 dt-Split-Invarianz

> `tick(200)` muss denselben Zustand ergeben wie `tick(25)` achtmal.

Das ist die Regel, die Live-Betrieb und Replay überhaupt erst gleichsetzt: live kommen Ticks im
rAF-Takt, beim Zurückspulen in `REPLAY_STEP_MS`-Schritten. Wären die Ergebnisse verschieden, zeigte
die Zeitleiste einen anderen Raum als die Bühne.

Daraus folgt hart:

- **Alle Phasen kommen aus `engine.t`**, nie aus einem Tick-Zähler.
  Richtig: `const frame = Math.floor(t / 120) % 4;` — falsch: `a.frame = (a.frame + 1) % 4;`
- Keine Schwelle der Form „alle 10 Ticks"; Schwellen sind Zeitpunkte (`if (t >= a.busy)`).
- Zufall darf nicht pro Tick gezogen werden, sondern pro Ereignis (siehe 3.2).
- `dt` selbst ist geklemmt: `dt = min(MAX_GAP_MS, max(0, ts - prev))` — **beidseitig**, denn unter
  `WORKER_CONCURRENCY > 1` kann `ts` gegenüber `seq` rückwärts laufen.

### 3.5 Iteration in Einfügereihenfolge

Aktoren, Werkzeuge, Effekte leben in `Map`, und es wird über `map.values()` iteriert — nie über
`Object.keys(obj)`. Bei einem Objekt hängt die Reihenfolge davon ab, ob ein Schlüssel wie eine Zahl
aussieht (`"12"` wandert vor `"run:8871"`); mit `run:`-Ids ginge das lange gut und bräche genau
dann, wenn eine Id einmal rein numerisch ist.

Wer eine Sammlung nach außen gibt, gibt eine **Kopie** (`[...map.values()]`). Ein lebender Cursor auf einer
`shift()`-Warteschlange verschluckt sonst Einträge, sobald der Kopf verworfen wird — die Indizes
rutschen unter ihm weg, und ein ganzer Agent erscheint nie im Raum.

---

## Regel 4 — Schichten

| Schicht | Dateien | darf importieren |
|---|---|---|
| **0** reine Domäne | `types`, `ids`, `const`, `toolAct`, `mapEvent`, `room`, `engine`, `recorder`, `replay`, `timeline` | nur Schicht 0 |
| **1** reine Pixel | `pixel/palette`, `pixel/art`, `pixel/person`, `pixel/furniture`, `pixel/props`, `pixel/scene` | Schicht 1 + `types`/`ids`/`const` — **nie** Engine |
| **2** React | `api`, `useOfficeFeed`, `useTheme`, `OfficeView`, `Stage`, `TopBar`, `Timeline`, `Dock`, `Inspector` | alles |

Zusätzlich: **Schicht 0 und 1 importieren keine Pakete.** Kein `react`, kein `@tanstack/*`, nichts
aus `node_modules` — sonst wären sie nicht mehr ohne Bundler ausführbar (Regel 5).

Warum Schicht 1 die Engine nicht kennen darf: die Zeichenschicht bekommt einen `Frame`, sonst nichts.
Sähe sie die Engine, könnte sie den Zustand beim Zeichnen fortschreiben — und das Bild hinge davon
ab, wie oft gezeichnet wurde. Damit wäre Replay tot.

Warum Schicht 0 nichts aus Schicht 1 sehen darf: die Domäne muss ohne Canvas testbar bleiben; der
Prüfer lädt sie nackt unter Node, wo es kein `CanvasRenderingContext2D` gibt.

### Die eine dokumentierte Ausnahme

Das Blitten des 480×270-Puffers auf den sichtbaren Canvas braucht genau ein `drawImage`:

```ts
// Stage.tsx (Schicht 2) — die einzige vom Pixel-Vertrag ausgenommene Stelle.
vis.imageSmoothingEnabled = false;
vis.drawImage(buffer, 0, 0, PIX.w, PIX.h, ox, oy, PIX.w * z, PIX.h * z);
```

Dieses `drawImage` lebt in **`Stage.tsx`, Schicht 2**, und ist vom Vertrag ausdrücklich ausgenommen.
Es ist die einzige Ausnahme; ein zweites `drawImage` irgendwo anders ist ein Fehler, kein Präzedenzfall.

---

## Regel 5 — Werkzeug-Beschränkung: Schicht 0 und 1 laufen nackt unter Node

Es gibt im Frontend keinen Test-Runner, und vitest lohnt nicht (das Dockerfile macht bei jedem Bau
`npm install` ohne Lockfile). Stattdessen laufen die Schichten 0 und 1 **direkt** unter Node:

```bash
docker run --rm -v "$PWD/frontend":/w -w /w node:22-alpine \
  node --experimental-strip-types tools/office-check.mjs
```

`--experimental-strip-types` ersetzt Typen durch Leerzeichen — es transpiliert nicht. Daraus folgen
vier Verbote für die Schichten 0 und 1:

- **kein `enum`** (erzeugt Laufzeitcode) → stattdessen `as const`-Objekt + `typeof`-Union,
- **kein `namespace`**, kein `module {}`,
- **keine Parameter-Properties** (`constructor(private x: number)`),
- **kein `import =` / `export =`**.

Erlaubt ist alles, was durch bloßes Streichen verschwindet: Typannotationen, `interface`, `type`,
Generics, `as`, `satisfies`, `import type`.

Zwei weitere Regeln, die aus Nodes ESM-Auflösung folgen:

- **Relative Importe tragen die `.ts`-Endung**: `import { mix } from "./ids.ts";`
  Node löst extensionslose Specifier in ESM nicht auf. Vite und `tsc`
  (`allowImportingTsExtensions: true`) kommen damit klar — der Prüfer erzwingt es.
- **Typ-Importe heißen `import type`**. Ein `import { Ev } from "./types.ts"` bliebe nach dem
  Streichen als echter Import stehen und knallte zur Laufzeit, weil `types.ts` keinen Wert exportiert.

Das kostet nichts und schenkt uns einen Test-Runner mit null Abhängigkeiten.

---

## Was der Prüfer prüft

`frontend/tools/office-check.mjs`, `npm run check:office`. Bewusst **nicht** Teil von `npm run build` —
der Docker-Bau darf nicht davon abhängen.

| Prüfung | Regel | Stand |
|---|---|---|
| Reinheits-Grep (verbotene Bezeichner) | 3.1 | gebaut |
| Schicht-Import-Regel + `.ts`-Endung + keine Pakete | 4, 5 | gebaut |
| goldenes Bild: `frameAt` an 8 Zeitpunkten gegen `tools/golden.json` | 3 | Welle M |
| Seek-Idempotenz (`seek(t)` zweimal = einmal) | 3 | Welle M |
| `seek ≡ advance` | 3 | Welle M |
| dt-Split-Invarianz `tick(200) ≡ tick(25)×8` | 3.4 | Welle M |
| Pixel-Vertrag als `ctx`-Proxy (alles außer `fillStyle`/`globalAlpha`/`fillRect` wirft) | 2.1 | Welle M |
| goldene Pixel-Ops-Hashes | 2 | Welle M |
| Vollständigkeit der Werkzeug-Tabelle (jedes native Traccoon-Werkzeug) | — | Welle M |

Dazu, außerhalb des Prüfers: `tsc -b` muss durchlaufen (`strict: true`).
