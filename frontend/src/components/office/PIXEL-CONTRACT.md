# Pixel contract: the Traccoon "office"

This is the rulebook for everything under `src/components/office/`. It is not a style guide but
the promise on which seven parts built in parallel fit together. `frontend/tools/office-check.mjs`
enforces the rules mechanically: what stands here breaks the run there.

Every rule stands together with its reasoning. Whoever wants to break a rule has to refute the
reasoning, not overlook the rule.

---

## Rule 1: art level 480x270, frame buffer 960x540 (the most important rule)

There is exactly one coordinate system that is drawn in: the **art level**, 480x270 units
(`ART`). The frame buffer is twice as fine, **960x540** (`PIX = ART × ART_SCALE`,
`ART_SCALE = 2`). Between the two stands the same camera wrapper that also does the zooming:
`CAM_FULL` has `zoom: ART_SCALE`.

> **Why the separation, since when, and what it is for**
>
> Until 2026-08-07 the art level and the buffer were the same: 480x270. On a 1080p screen that
> became a 4x4 block per drawn pixel, and a figure of 16x24 was a thumbnail from three metres
> away: "extremely pixelated", and not as a style but as a side effect.
>
> The art level stays so that **nothing has to be converted**: every existing drawing function
> keeps painting in the same numbers and delivers the same picture (every unit becomes a 2x2
> block). The finer buffer is the room new art grows into, stage by stage, object family by
> object family, without anything being broken in between.
>
> **Whoever wants to draw more finely draws in buffer pixels**, so bypassing the camera wrapper,
> with `ART_SCALE` as the conversion. As long as a family is still coarse it stays in art units.
> Both side by side is explicitly allowed; that is the whole purpose.

The visible canvas is arbitrarily large; it gets the buffer scaled up by an integer factor
(`imageSmoothingEnabled = false`).

> **Integer applies to the drawing in the backing store; fitting into the viewport is done by CSS.**
>
> The backing store (`canvas.width/height`) stays an integer multiple of `PIX`, and only there
> does the rule apply, because a blit with a factor of 1.5 would run over half columns. The **CSS
> size** of the canvas on the other hand is the largest 16:9 rectangle that fits into the
> container: 480x270 *is* 16:9, so nothing is distorted, and one direction always fills
> completely. It is scaled up over `image-rendering: pixelated` (the class `.pixel-canvas` in
> `src/index.css`), not bilinearly.
>
> Whoever shortens that to "the visible canvas has to be an integer multiple" brings back the bug
> that stood here: on 1920x1080 that gave a factor of 3 instead of 3.76 and wide empty areas all
> around, on the wall screen whose whole purpose is the area.
>
> What depends on it: **the hit test**. `hitTest` wants buffer coordinates, the pointer position
> comes in CSS pixels, and between them there are now two factors (CSS to backing store to
> buffer) instead of only the blit factor. `Stage.toBuffer` therefore computes over
> `getBoundingClientRect()` **of the canvas** and both factors; whoever forgets one selects a
> figure too far to the right.

The simulation on the other hand runs in **`SCENE = 1600x900`**. Between the two stands
`POS_SCALE = 0.3`:

> **`POS_SCALE` applies to positions. It does not apply to sprites.**

A figure is **16x24 art units**, a good eleventh of the picture height. It is *not* 16x24 scene
pixels that then shrink to 5x7. Whoever scales the sprites along paints figures with 35 instead
of 384 pixels and thereby miscalculates the whole art budget (<= 20 KB, about 36 arts) by a
factor of 3, and notices only when the arts are finished and unusable.

In practice that means in every drawing function:

```ts
const px = Math.round(actor.x * POS_SCALE);   // position: scaled
const py = Math.round(actor.y * POS_SCALE);   // position: scaled
drawPerson(ctx, px, py, look, pose);          // sprite: 16x24 buffer pixels, unscaled
```

Furniture, bubbles, text, particles: likewise in art units (respectively, where a family is
already drawn finely, in buffer pixels, see rule 1). `POS_SCALE` appears in layer 1 at most in
order to bring a scene coordinate in, never in order to compute a size.

---

## Rule 2: drawing rules (layer 1)

### 2.1 Three tools, no more

On the 2D context exactly three things exist for layer 1:

| allowed | forbidden |
|---|---|
| `ctx.fillStyle` | `beginPath`, `moveTo`, `lineTo`, `arc`, `arcTo`, `ellipse`, `rect`, `fill`, `stroke`, `clip` |
| `ctx.globalAlpha` | `createLinearGradient`, `createRadialGradient`, `createPattern` |
| `ctx.fillRect` | `drawImage`, `putImageData`, `getImageData` |
| | `shadowBlur`, `shadowColor`, `filter`, `globalCompositeOperation` |
| | `save`, `restore`, `translate`, `scale`, `rotate`, `setTransform` |
| | `fillText`, `strokeText`, `measureText` |

The reasons: paths and gradients rasterise on subpixel edges and destroy the pixel look;
`save`/`restore` drag a state stack along that makes the golden ops hashes depend on the call
order; `fillText` delivers different pixels per platform and would therefore no longer be golden
checkable. Text is an art, not a font.

Curves exist regardless, as **stepped `fillRect` runs**: compute one edge per row, set one
rectangle row. A circle is a table of half widths, not an `arc`.

`globalAlpha` is set back to `1` after every block. Since there is no `restore`, resetting is the
duty of the caller: a forgotten `globalAlpha` colours the rest of the picture.

The checker puts a `ctx` proxy in front of the drawing layer that raises on every other access.

### 2.2 The signature `(ctx, cx, yBase, …)`

Every world drawing function (everything that stands in the scene: figures, furniture, props) has
the form

```ts
function drawX(ctx: Ctx, cx: number, yBase: number, …): void
```

- **`cx`** = the horizontal **centre** of the object in buffer pixels.
- **`yBase`** = the buffer row **the object stands on**, the first row *below* the sprite. A
  sprite 24 pixels high therefore occupies `yBase-24 … yBase-1` and begins with
  `ctx.fillRect(cx - 8, yBase - 24, …)`.

That is not a matter of taste: the scene sorts by `yBase` before drawing (painter's algorithm,
back first). An object that lies about its foot point, passing its top edge as `y` for instance,
sorts wrongly and disappears behind furniture it stands in front of. The bug looks like a depth
problem and is a signature problem.

`Frame.actors` comes from the engine already sorted by `y`. Whoever mixes objects of their own in
sorts with the same key.

### 2.3 Movement only in whole pixels

Drawing happens exclusively on integer buffer coordinates. A `fillRect` with `x = 12.4` is let
run by the browser over two columns with half opacity: with a walking figure that flickers
visibly, with a piece of furniture the edge smears.

Rounding happens **while rendering** (`Math.round` after `× POS_SCALE`), not in the engine. The
engine keeps computing in scene pixels with a subpixel accumulator (`ActorState.sub`), see rule 3.

### 2.4 The projection: surfaces from above, faces from the front

The room is seen **from above**. Everything that lies on the floor or stands on it is therefore
drawn as a surface: the desktop is a rectangle with a keyboard on it, the chair is a seat with
its backrest at the far edge, the table is a disc. Only things that have a **face** are drawn
from the front: people, monitors, the door, the windows, the rack. A monitor seen from above is
an unrecognisable bar, and a person seen from above is a hat.

> **Why this is a rule and not taste**
>
> Until 2026-08-21 the furniture was drawn from the front: a desk with a top, a front edge and
> two legs, a chair with a backrest facing the viewer, and a monitor floating above both. On a
> floor seen from above that is a second, contradictory projection, and the result reads as a
> stage set photographed from the side rather than as a room. It is the single biggest reason
> the office did not look like the top-down games it is measured against, and no colour or
> texture fixes it.
>
> The seat geometry follows from it: a person sits **in front of their own desk**, so `desk` and
> `mon` of a seat lie on the same `x` as `sit` and above it. The old placement (half a desk
> pushed sideways towards the middle of a bench) was invisible in the front view and put every
> colleague at the corner of a table as soon as the projection was right.

### 2.5 Furniture carries a contour, people do not

Every **object** in the room is drawn with a closed outline in the palette key `line` (and
`lineSoft` for inner seams). Every **person** is drawn without one: the edge of a figure is one
row of its own colour, darker (`s`, `t`, `h`).

That split is not a compromise, it is what the reference does, and there is a reason for it.
An object has an edge in the world: a desktop ends, and the line says where. A face has none;
a dark line around a head 20 pixels wide takes a fifth of the face away and turns the figure
into a sticker. The tile based games this room is measured against draw exactly this way, and
the first attempt here got it the other way round: contours on everything, and the people
looked like cardboard.

> **What happens without any contour at all**
>
> Until 2026-08-21 nothing had one. Furniture was areas of colour on an area of colour, and as
> soon as a desk and the floor were of similar brightness the room stopped being readable. With
> twelve figures in it, the picture became a colour field.
>
> `line` is deliberately **not** black (`#4e4e60` by day): a pure black on a light floor cuts
> holes into the picture. The value is measured out of the reference, like the rest of the day
> palette.

Two rules follow for the objects:

- **The contour is drawn into the art, never painted around it.** An outline pass would be four
  extra draws per object, and it would put a line where the object touches the ground, exactly
  where there must be none.
- **A `tint` replaces the fill of a sprite, never its contour** (`drawArt`). Without that
  exception a short sleeve would take the arm out of its silhouette.

### 2.6 Parts of one sprite have the same height

All five hairstyles are `HAIR_H` rows tall, and the padding is done by a helper, not by hand.
`drawArt` anchors at the **foot point**, so an art two rows shorter lands two rows lower. With
the hair that means the fringe covers the eyes, and the figure loses its face while every single
art is correct on its own. The bug is invisible in the source and obvious in the picture, which
is the worst combination there is.

### 2.7 Carpets are drawn before the sorted layer

Anything that lies **flat** on the floor (`drawRug`) is drawn in the background pass, not pushed
into the list sorted by foot point. A carpet has no foot point: sorted along with the furniture
it lands behind the sofa standing on it and covers it, and it looks as though the sofa had never
been drawn.

---

## Rule 3: determinism (layers 0 and 1)

The room has to be **bit identically** reconstructable out of the event log: rewinding is "new
engine, replay the log from the start". Everything that does not come from the log breaks that.

### 3.1 Forbidden identifiers

In layers 0 and 1 these strings do not occur (checker: purity grep, with comments removed
beforehand):

```
Math.random   Date.now   performance.now   new Date
window.   document.   localStorage   toLocale
```

`Math.random` is obvious. The clocks are less so: an animation phase from `performance.now()`
looks right live and delivers a different picture on rewinding than the first time. `toLocale*`
depends on the time zone and the language of the browser, so the same log would give two
different timelines in two tabs. Formatting belongs in layer 2.

### 3.2 Variation comes from the seed

Every "randomness" (hairstyle, skin tone, stride length, wobble phase, which chair, which line)
is a pure function of `ActorState.seed`:

```ts
const gait = 1 + (rnd01(mix(seed, SALT_PACE)) - 0.5) * 2 * PACE_SPREAD;
```

`seed` itself is `hash32(agentId)`. Every place of use gets its **own** `SALT` as a named
constant; two places with the same salt are correlated (all the slow ones have red hair), and
that stands out late and embarrassingly.

**Two seeds, not one: `ActorState.seed` (individual) and the appearance seed (role).**

`ActorState.seed` is `hash32("run:8871")`, the **run** id. Everything from it is therefore new per
run, and exactly that was too much: the same `developer` looked different yesterday than today,
and nobody could be recognised. That is why there is `rollenSeed(role, seed)` =
`mix(hash32(role), SALT_ROLLE)` beside it (`pixel/palette.ts`). The split is **not** negotiable
cosmetics but follows the visibility at 16x24 pixels:

| trait | source | why |
|---|---|---|
| shirt colour | **role** | the largest contiguous colour area of a sprite, *the* information from three metres |
| hair colour | **role** | the second largest area; together with the shirt a coat of arms |
| torso (shape) | **role** | the shoulder silhouette, which carries the role from behind as well (the chief's seat shows `DIR_BACK`) |
| head, skin, arms, legs, hair shape, trouser colour | **`seed`** | so that twelve `developer` stay distinguishable |
| all seven `gaitOf` fields | **`seed`** | the movement is seen before the hairstyle, so it is the actual carrier of individuality |
| `Priv.pace`, leaving spread, breathing, `fx.seed` | **`seed`** | otherwise everybody goes through the same door at the same time |
| seat (`seatOf(a.id)`) | **run id** | `seatOf` probes linearly, so twelve `developer` would get twelve **consecutive** seats and the left bench would be a monoculture |

Three things about that are contract, not taste:

- **There is no role colour table.** Roles are data (`developer`, `assistent`, `architect`,
  `code_reviewer`, `project_manager`, `uniwar-operator`, `news`, and an eighth tomorrow), not an
  enumeration. A table would need maintenance with every new agent and would have no entry at all
  for unknown roles; `hash32(role)` gives every role a stable colour forever.
- **An empty role means the run seed.** A nameless figure should not make all nameless figures
  alike. Because `role === seed` hits exactly the old salts in the process, the role-less case is
  bit identical to the behaviour before the split.
- **The appearance is resolved while drawing, never written back into a seed.** `engine.ts` sets
  `a.role` only *after* `ensureActor`, and `wake(id)` calls `ensureActor` without a role at all.
  An appearance seed stored once would therefore have to be overwritten afterwards; with the
  resolution in layer 1 the figure simply gets its role appearance in the first frame in which
  the role is known. That is why the whole thing lives in `pixel/palette.ts` (layer 1, which may
  import `ids.ts`) and **not** in layer 0: `ActorState.role` already stands in the `Frame`, so no
  new field is needed, and thereby check 3 (the golden frame JSON) stays byte identical while
  only check 8 (pixel ops) changes.

### 3.3 Routes are computed once, never per tick

A figure does not walk in a straight line any more: it walks around the furniture. The route
comes from `room.route()`, a visibility graph over the corners of a fixed list of rectangles
(`room.BLOCKED`), and it is computed **once**, in `startTrip`. From then on the trip is a
function of time exactly as before, only along a polyline instead of a segment.

> **Why once and not per tick**
>
> A route recomputed while walking would depend on where the figure happens to be when a tick
> lands, so on the tick size, and rule 3.4 would be gone: `tick(200)` would give a different
> path from `tick(25)` eight times, and the timeline would show a different room from the
> stage.

From that follow two limits, and both are deliberate:

- **Figures are not obstacles.** Two people may walk through each other. They move, so avoiding
  them would mean recomputing, which is the thing that must not happen. Furniture does not
  move, which is exactly why it can be avoided.
- **A rectangle containing the start or the goal does not block.** A seat stands in front of
  its own desk and the coffee target is the machine itself. Without that exception the figure
  would stand still forever, which is a far worse bug than walking through a table.

The checker holds it: `routes avoid the furniture` walks every route between every pair of
places anybody actually goes to, samples the polyline every 8 units and asserts that no sample
lies inside a rectangle. It found two real errors on its first run: the standing place in front
of the server rack had come to lie on a desk, and the box of the meeting table reached over the
seat of the nearest cluster.

### 3.4 Subpixel accumulator

Positions are integrated as floating point and rounded **only while rendering**:

```ts
a.sub.x += vx * dt;                       // fractions are kept
const step = Math.trunc(a.sub.x);
a.x += step; a.sub.x -= step;             // whole scene pixels wander into x
```

Whoever rounds per tick instead loses every movement with small `dt` (`round(0.4) = 0`) and
thereby violates rule 3.4 immediately.

### 3.5 dt split invariance

> `tick(200)` has to give the same state as `tick(25)` eight times.

That is the rule that equates live operation and replay in the first place: live the ticks come in
the rAF beat, while rewinding they come in `REPLAY_STEP_MS` steps. If the results differed, the
timeline would show a different room from the stage.

From that follows hard:

- **All phases come from `engine.t`**, never from a tick counter.
  Right: `const frame = Math.floor(t / 120) % 4;` — wrong: `a.frame = (a.frame + 1) % 4;`
- No threshold of the form "every 10 ticks"; thresholds are moments (`if (t >= a.busy)`).
- Randomness must not be drawn per tick but per event (see 3.2).
- `dt` itself is clamped: `dt = min(MAX_GAP_MS, max(0, ts - prev))`, on **both sides**, because
  under `WORKER_CONCURRENCY > 1` `ts` can run backwards relative to `seq`.

### 3.6 Iteration in insertion order

Actors, tools and effects live in a `Map`, and iteration happens over `map.values()`, never over
`Object.keys(obj)`. With an object the order depends on whether a key looks like a number (`"12"`
wanders before `"run:8871"`); with `run:` ids that would go well for a long time and break exactly
when an id is once purely numeric.

Whoever hands a collection out hands out a **copy** (`[...map.values()]`). A live cursor on a
`shift()` queue otherwise swallows entries as soon as the head is discarded: the indices slide
away under it, and a whole agent never appears in the room.

---

## Rule 4: layers

| layer | files | may import |
|---|---|---|
| **0** pure domain | `types`, `ids`, `const`, `toolAct`, `mapEvent`, `room`, `engine`, `recorder`, `replay`, `timeline` | layer 0 only |
| **1** pure pixels | `pixel/palette`, `pixel/art`, `pixel/person`, `pixel/furniture`, `pixel/props`, `pixel/scene` | layer 1 plus `types`/`ids`/`const`, **never** the engine |
| **2** React | `api`, `useOfficeFeed`, `useTheme`, `OfficeView`, `Stage`, `TopBar`, `Timeline`, `Dock`, `Inspector` | everything |

In addition: **layers 0 and 1 import no packages.** No `react`, no `@tanstack/*`, nothing from
`node_modules`; otherwise they would no longer be executable without a bundler (rule 5).

Why layer 1 must not know the engine: the drawing layer gets a `Frame`, nothing else. If it saw
the engine, it could write the state on while drawing, and the picture would depend on how often
it was drawn. That would kill the replay.

Why layer 0 must see nothing from layer 1: the domain has to stay testable without a canvas; the
checker loads it bare under Node, where there is no `CanvasRenderingContext2D`.

### The one documented exception

Blitting the 480x270 buffer onto the visible canvas needs exactly one `drawImage`:

```ts
// Stage.tsx (layer 2): the only place exempted from the pixel contract.
vis.imageSmoothingEnabled = false;
vis.drawImage(buffer, 0, 0, PIX.w, PIX.h, 0, 0, PIX.w * z, PIX.h * z);
```

The blit covers the backing store **completely** (no offset, no letterbox): onto the visible area
it is pulled by CSS, see rule 1.

This `drawImage` lives in **`Stage.tsx`, layer 2**, and is explicitly exempted from the contract.
It is the only exception; a second `drawImage` anywhere else is a bug, not a precedent.

---

## Rule 5: tool restriction, layers 0 and 1 run bare under Node

There is no test runner in the frontend, and vitest is not worth it (the Dockerfile runs
`npm install` without a lockfile on every build). Instead layers 0 and 1 run **directly** under
Node:

```bash
docker run --rm -v "$PWD/frontend":/w -v "$PWD/backend":/backend -w /w node:22-alpine \
  node --experimental-strip-types tools/office-check.mjs
```

`backend/` is mounted as well, because the completeness check of the tool table **draws** its
target list from `backend/app/worker/*.py`. A copied list would only check whether the copy
matches itself, and the drift it is all about it would never see. If the mount is missing, the
checker breaks and says what to do.

`--experimental-strip-types` replaces types by spaces; it does not transpile. From that follow
four prohibitions for layers 0 and 1:

- **no `enum`** (it produces runtime code); instead an `as const` object plus a `typeof` union,
- **no `namespace`**, no `module {}`,
- **no parameter properties** (`constructor(private x: number)`),
- **no `import =` / `export =`**.

Allowed is everything that disappears by mere deletion: type annotations, `interface`, `type`,
generics, `as`, `satisfies`, `import type`.

Two further rules that follow from Node's ESM resolution:

- **Relative imports carry the `.ts` extension**: `import { mix } from "./ids.ts";`
  Node does not resolve extensionless specifiers in ESM. Vite and `tsc`
  (`allowImportingTsExtensions: true`) cope with it, and the checker enforces it.
- **Type imports are called `import type`.** An `import { Ev } from "./types.ts"` would stay as a
  real import after the deletion and blow up at runtime, because `types.ts` exports no value.

That costs nothing and gives us a test runner with zero dependencies.

---

## What the checker checks

`frontend/tools/office-check.mjs`, `npm run check:office` (from `frontend/`, where the backend
lies under `../backend`). Deliberately **not** part of `npm run build`: the Docker build must not
depend on it.

| check | rule | state |
|---|---|---|
| purity grep (forbidden identifiers) | 3.1 | built |
| layer import rule plus `.ts` extension plus no packages | 4, 5 | built |
| golden picture: `frameAt` at 8 moments against `tools/golden.json` | 3 | built |
| seek idempotency (`seek(t)` twice equals once, and `frameAt` equals `seek`) | 3 | built |
| `seek` equals `advance` (5 step sizes, odd ones as well) | 3 | built |
| dt split invariance `tick(200)` equals `tick(25)x8`, bare and over the command script | 3.4 | built |
| the pixel contract as a `ctx` proxy (everything except `fillStyle`/`globalAlpha`/`fillRect` raises, plus integer coordinates) | 2.1, 2.3 | built |
| golden pixel ops hashes (8 pictures x day/evening) | 2 | built |
| completeness of the tool table (target list from `backend/app/worker`) | — | built |
| seat geometry: `SEATS_PX[i]` equals `round(ROOM.seats[i].sit × POS_SCALE)` | 4 | built |
| rack geometry: `RACK_PX` equals `round(ROOM.rack × POS_SCALE)` | 4 | built |
| the rack lights up in the fixture (>= 2 states over the 8 golden pictures) | — | built |

The last check is not geometry but a statement about the checking itself: the ops hashes only
report "the same calls as at the last bless" and are blind to whether they ever entered a drawing
branch. If no golden picture contained a glowing rack, the LED drawing would be unchecked, and a
check that does not execute the new code is theatre.

### The server rack: the only state in the `Frame` not bound to an actor

`Frame.rack` (`{ state, since, label }`) stands beside `actors`, because a deployment belongs to
the **room** and to no figure: the run that set it off goes through the door long before the
building is finished. Two conclusions are contract, not taste:

- **The phase comes from `(t - since)`**, as with every `Fx` (rule 3.4). `since` is `engine.t`.
- **No expiry.** The state is set by the `start` and superseded by the `ok`/`fail`/`back`; there
  is no `until`. That is the explicit opposite of `TOOL_BUSY_MS`, which exists only because a tool
  row from old data knows no interval; with a deployment **both ends are real events**. If the end
  never comes (the deployer is dead), the rack keeps glowing. That is the truth, not a bug:
  something is running whose outcome nobody knows.

`drawRack` enters the LED block **exclusively** with `state !== "idle"`. With `idle` the same
`fillRect` calls arise byte for byte in the same order as without a `rack`; otherwise every one of
the 16 ops hashes would hang off the state of the rack and the intention of a bless diff would
disappear in the noise.

The server rack is **20x34** (tall instead of wide) and stands in the wall gap between the
whiteboard and the clock; the old 22x20 box with three slots read as a filing cabinet with drawer
handles and therefore did not carry the meaning. The filing cabinet has stayed itself: 16x15,
`drawCabinet`, without any meaning, a shelf for the potted plant.

The fixture for it stands in `frontend/tools/fixture.mjs` and is reproduced from the shape of
`services/office.py::step_events`, not invented. If the expected behaviour changes, `--bless`
rewrites the golden pictures: **a decision, not a repair**, and the reasoning belongs in the commit
message.

In addition, outside the checker: `tsc -b` has to pass (`strict: true`).
