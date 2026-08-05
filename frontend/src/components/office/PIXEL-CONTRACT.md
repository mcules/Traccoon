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

> **Ganzzahlig ist die Zeichnung im Rückspeicher — das Einpassen in den Viewport macht CSS.**
>
> Der Rückspeicher (`canvas.width/height`) bleibt ein ganzzahliges Vielfaches von 480×270; nur
> dort gilt die Regel, denn ein Blit mit Faktor 1,5 liefe über halbe Spalten. Die **CSS-Größe**
> des Canvas dagegen ist das größte 16:9-Rechteck, das in den Container passt — 480×270 *ist*
> 16:9, also verzerrt nichts, und eine Richtung füllt immer vollständig. Hochgezogen wird per
> `image-rendering: pixelated` (Klasse `.pixel-canvas` in `src/index.css`), nicht bilinear.
>
> Wer das zu „der sichtbare Canvas muss ein ganzzahliges Vielfaches sein" verkürzt, holt den
> Fehler zurück, der hier stand: auf 1920×1080 ergab das Faktor 3 statt 3,76 und ringsum breite
> leere Flächen — im Wandschirm, für den die Fläche der ganze Zweck ist.
>
> Was daran hängt: **die Trefferprüfung**. `hitTest` will Pufferkoordinaten, die Zeigerposition
> kommt in CSS-Pixeln, und dazwischen stehen jetzt zwei Faktoren (CSS → Rückspeicher → Puffer)
> statt nur des Blit-Faktors. `Stage.toBuffer` rechnet deshalb über `getBoundingClientRect()`
> **des Canvas** und beide Faktoren; wer einen vergisst, wählt eine Figur zu weit rechts.

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

**Zwei Seeds, nicht einer: `ActorState.seed` (individuell) und der Aussehen-Seed (Rolle).**

`ActorState.seed` ist `hash32("run:8871")` — die **Lauf**-Id. Alles daraus ist damit pro Lauf neu,
und genau das war zu viel: derselbe `developer` sah gestern anders aus als heute, wiedererkennen
konnte man niemanden. Deshalb gibt es daneben `rollenSeed(role, seed)` = `mix(hash32(role), SALT_ROLLE)`
(`pixel/palette.ts`). Die Aufteilung ist **nicht** verhandelbare Kosmetik, sondern folgt der
Sichtbarkeit bei 16×24 Pixeln:

| Merkmal | Quelle | Warum |
|---|---|---|
| Hemdfarbe | **Rolle** | größte zusammenhängende Farbfläche eines Sprites — aus drei Metern *die* Information |
| Haarfarbe | **Rolle** | zweitgrößte Fläche; mit dem Hemd zusammen ein Wappen |
| Torso (Form) | **Rolle** | Schultersilhouette, trägt die Rolle auch von hinten (der Chefplatz zeigt `DIR_BACK`) |
| Kopf, Haut, Arme, Beine, Haarform, Hosenfarbe | **`seed`** | damit zwölf `developer` unterscheidbar bleiben |
| alle sieben `gaitOf`-Felder | **`seed`** | die Bewegung sieht man vor der Frisur — sie ist der eigentliche Träger der Individualität |
| `Priv.pace`, Abgangsstreuung, Atem, `fx.seed` | **`seed`** | sonst gehen alle gleichzeitig durch dieselbe Tür |
| Sitzplatz (`seatOf(a.id)`) | **Lauf-Id** | `seatOf` sondiert linear — zwölf `developer` bekämen zwölf **aufeinanderfolgende** Plätze, die linke Bank wäre eine Monokultur |

Drei Dinge daran sind Vertrag, nicht Geschmack:

- **Es gibt keine Rollen-Farbtabelle.** Rollen sind Daten (`developer`, `assistent`, `architect`,
  `code_reviewer`, `project_manager`, `gameproj-operator`, `news` — und morgen eine achte), keine
  Aufzählung. Eine Tabelle bräuchte Pflege bei jedem neuen Agenten und hätte für unbekannte Rollen
  gar keinen Eintrag; `hash32(role)` gibt jeder Rolle für immer eine stabile Farbe.
- **Leere Rolle → der Laufseed.** Eine namenlose Figur soll nicht alle namenlosen Figuren einander
  gleichmachen. Weil `rolle === seed` dabei genau die alten Salze trifft, ist der rollenlose Fall
  bitgleich zum Verhalten vor der Aufteilung.
- **Das Aussehen wird beim Zeichnen aufgelöst, nie in einen Seed zurückgeschrieben.** `engine.ts`
  setzt `a.role` erst *nach* `ensureActor`, und `wake(id)` ruft `ensureActor` ganz ohne Rolle. Ein
  einmalig gespeicherter Aussehen-Seed müsste also nachträglich überschrieben werden — mit der
  Auflösung in Schicht 1 bekommt die Figur ihr Rollenaussehen einfach im ersten Bild, in dem die
  Rolle bekannt ist. Deshalb lebt das Ganze in `pixel/palette.ts` (Schicht 1, darf `ids.ts`
  importieren) und **nicht** in Schicht 0: `ActorState.role` steht bereits im `Frame`, es braucht
  kein neues Feld — und damit bleibt Prüfung 3 (goldenes Frame-JSON) bytegleich, während nur
  Prüfung 8 (Pixel-Ops) sich ändert.

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
vis.drawImage(buffer, 0, 0, PIX.w, PIX.h, 0, 0, PIX.w * z, PIX.h * z);
```

Der Blit deckt den Rückspeicher **vollständig** ab (kein Versatz, kein Briefkasten): auf die
sichtbare Fläche zieht ihn CSS, siehe Regel 1.

Dieses `drawImage` lebt in **`Stage.tsx`, Schicht 2**, und ist vom Vertrag ausdrücklich ausgenommen.
Es ist die einzige Ausnahme; ein zweites `drawImage` irgendwo anders ist ein Fehler, kein Präzedenzfall.

---

## Regel 5 — Werkzeug-Beschränkung: Schicht 0 und 1 laufen nackt unter Node

Es gibt im Frontend keinen Test-Runner, und vitest lohnt nicht (das Dockerfile macht bei jedem Bau
`npm install` ohne Lockfile). Stattdessen laufen die Schichten 0 und 1 **direkt** unter Node:

```bash
docker run --rm -v "$PWD/frontend":/w -v "$PWD/backend":/backend -w /w node:22-alpine \
  node --experimental-strip-types tools/office-check.mjs
```

`backend/` wird mit eingehängt, weil die Vollständigkeitsprüfung der Werkzeug-Tabelle ihre
Sollliste aus `backend/app/worker/*.py` **zieht**. Eine abgeschriebene Liste prüfte nur, ob die
Abschrift zu sich selbst passt — die Drift, um die es geht, sähe sie nie. Fehlt der Mount,
bricht der Prüfer und sagt, was zu tun ist.

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

`frontend/tools/office-check.mjs`, `npm run check:office` (aus `frontend/`, dort liegt das
Backend unter `../backend`). Bewusst **nicht** Teil von `npm run build` — der Docker-Bau darf
nicht davon abhängen.

| Prüfung | Regel | Stand |
|---|---|---|
| Reinheits-Grep (verbotene Bezeichner) | 3.1 | gebaut |
| Schicht-Import-Regel + `.ts`-Endung + keine Pakete | 4, 5 | gebaut |
| goldenes Bild: `frameAt` an 8 Zeitpunkten gegen `tools/golden.json` | 3 | gebaut |
| Seek-Idempotenz (`seek(t)` zweimal = einmal, und `frameAt` ≡ `seek`) | 3 | gebaut |
| `seek ≡ advance` (5 Schrittweiten, auch krumme) | 3 | gebaut |
| dt-Split-Invarianz `tick(200) ≡ tick(25)×8`, nackt und über das Kommando-Skript | 3.4 | gebaut |
| Pixel-Vertrag als `ctx`-Proxy (alles außer `fillStyle`/`globalAlpha`/`fillRect` wirft, dazu ganzzahlige Koordinaten) | 2.1, 2.3 | gebaut |
| goldene Pixel-Ops-Hashes (8 Bilder × Tag/Abend) | 2 | gebaut |
| Vollständigkeit der Werkzeug-Tabelle (Sollliste aus `backend/app/worker`) | — | gebaut |
| Sitzgeometrie: `SEATS_PX[i]` ≡ `round(ROOM.seats[i].sit × POS_SCALE)` | 4 | gebaut |
| Rack-Geometrie: `RACK_PX` ≡ `round(ROOM.rack × POS_SCALE)` | 4 | gebaut |
| Das Rack leuchtet in der Fixture (≥ 2 Zustände über die 8 goldenen Bilder) | — | gebaut |

Die letzte Prüfung ist keine Geometrie, sondern eine Aussage über die Prüfung selbst: die
Ops-Hashes melden nur „dieselben Aufrufe wie beim letzten Bless" und sind blind dafür, ob sie
einen Zeichenzweig je betreten haben. Enthielte kein goldenes Bild ein leuchtendes Rack, wäre
die LED-Zeichnung ungeprüft — eine Prüfung, die den neuen Code nicht ausführt, ist Theater.

### Der Serverschrank — der einzige nicht-aktorgebundene Zustand im `Frame`

`Frame.rack` (`{ state, since, label }`) steht neben `actors`, weil ein Deployment dem **Raum**
gehört und keiner Figur: der Lauf, der es angestoßen hat, geht längst durch die Tür, während
noch gebaut wird. Zwei Folgerungen sind Vertrag, nicht Geschmack:

- **Die Phase kommt aus `(t - since)`**, wie bei jedem `Fx` (Regel 3.4). `since` ist `engine.t`.
- **Kein Verfall.** Der Zustand wird vom `start` gesetzt und vom `ok`/`fail`/`back` abgelöst —
  es gibt kein `until`. Das ist der ausdrückliche Gegensatz zu `TOOL_BUSY_MS`, das nur
  existiert, weil eine Werkzeugzeile aus Altdaten kein Intervall kennt; beim Deployment sind
  **beide Enden echte Ereignisse**. Kommt das Ende nie (Deployer tot), leuchtet das Rack weiter.
  Das ist die Wahrheit, kein Fehler: es läuft etwas, von dem niemand weiß, wie es ausging.

`drawRack` betritt den LED-Block **ausschließlich** bei `state !== "idle"`. Bei `idle` fallen
byteweise dieselben `fillRect` in derselben Reihenfolge an wie ohne `rack` — sonst hinge jedes
der 16 Ops-Hashes am Zustand des Schranks und die Absicht eines Bless-Diffs verschwände im
Rauschen.

Der Serverschrank ist **20×34** (hoch statt breit) und steht in der Wandlücke zwischen
Whiteboard und Uhr; der alte 22×20-Kasten mit drei Schlitzen las sich als Aktenschrank mit
Schubladengriffen und trug die Bedeutung damit nicht. Der Aktenschrank ist er selbst geblieben —
16×15, `drawCabinet`, ohne jede Bedeutung, Ablage für die Topfpflanze.

Die Fixture dazu steht in `frontend/tools/fixture.mjs` und ist der Form von
`services/office.py::step_events` nachgebaut, nicht ausgedacht. Ändert sich das erwartete
Verhalten, schreibt `--bless` die goldenen Bilder neu — **eine Entscheidung, keine Reparatur**,
und die Begründung gehört in die Commit-Nachricht.

Dazu, außerhalb des Prüfers: `tsc -b` muss durchlaufen (`strict: true`).
