# Designing the interface

A guide so that every page looks as if it were cut from one cloth. It describes **which
building blocks exist** and **when each of them is taken** — not how they are built inside.

The building blocks live in `src/components/ui.tsx`. Whoever builds a chain of classes by
hand that already stands there produces exactly the differences this file is meant to abolish.

## The three levels

The interface knows exactly three surfaces, and their order is the order of the picture:

| Level | Colour | What for |
|---|---|---|
| Page | `bg-surface` | the background everything stands on |
| Card | `bg-card` | an area of the page (`Area`) |
| Row | `bg-surface` | a line **inside** a card (`ListRow`) |

Out of that follows the most important rule: **a list needs a card around it.** Rows carry the
page colour; without a card they would stand on the page background in the same colour, and
one would see nothing but text. The other way round: what stands directly on the page (a
popover, a menu, a tile without an area) carries `bg-card`.

Borders are always `border-line`, text is `text-ink` (what matters) or `text-muted`
(everything explanatory). The accent colour is `brand` — it marks where one is and what the
main way is.

## Building blocks

### `Button` — every action

**A button is blue. Grey means switched off, nothing else.**

It used to be the other way round: most buttons had a grey border, and in a header with four
of them every single one went under — while the colour that actually means "there is nothing
to get here" was the normal state.

Four variants, no more are needed:

**Blue is the surface, not the writing.** A button with a blue border and blue letters is
still mostly background — and therefore almost as quiet as the grey one it was meant to
replace.

| Variant | Look | What for |
|---------|------|----------|
| `primary` | filled blue | the one action this surface is about |
| `secondary` (the default) | filled blue | everything else one can do |
| `confirm` | filled green | agree, approve, accept |
| `danger` | filled red | what one does not do by accident |

`primary` and `secondary` look the same. The difference stands in the code, not in the
picture: it says what the surface is about, and it is no promise of a different look. Whoever
wants to grade them later changes one line in `ui.tsx`.

```tsx
<Button variant="primary" onClick={save} disabled={!changed}>Save</Button>
<Button onClick={check} symbol="✓" state={checked ? "good" : "open"}>Check</Button>
<Button variant="danger" onClick={() => setDelete(true)}>Delete</Button>
```

**A button that can do nothing is switched off** — not clickable with an error message
afterwards. "Save" without a change, "Publish" without a new state: both `disabled`, with a
`title` that names the reason. That is the difference between an interface that shows the
state and one that keeps it quiet and scolds afterwards.

`symbol` is the short sign for narrow screens: there only it stands, otherwise the text.

`state` hangs a result on the button: `good` (a green tick), `bad` (a red cross), `open`
(nothing). For actions whose outcome one wants to see later without repeating them — a check,
for instance. Important: the result belongs to ONE state of the matter. If that changes, the
button stands on `open` again, otherwise it shows a result that applied to something else.

In rows and toolbars the same buttons in small: `BUTTON_SMALL.*`. Colour and meaning stay,
only the height does not pull the row apart.

`IconButton` follows the same rule — a blue border, a blue sign, but without a fill: a list of
twenty rows with three handles each would otherwise be fireworks.

Buttons with a mechanism of their own (a toggle, a file picker, a tab) stay `<button>` but
take the same classes: `className={BUTTON.primary}` / `BUTTON.secondary` / `BUTTON.danger`.
One source, two ways in — new code is written with `<Button>`.

Actions **without a surface** (a "show more", a × to remove something, a link in the middle of
prose) take `BUTTON_TEXT.secondary` or `.danger`: blue or red letters, no surface. Here too
grey means switched off.

**Not** covered by that are real toggles (a tab that is active or inactive): there grey means
"not chosen right now", not "switched off", and blue marks the choice.

### `Area` — the frame of a tab

Every tab, every self-contained unit of a page sits in exactly one `Area`: the explaining
sentence at the top (`hint`), below it optionally a toolbar (`tools`: filters, counters,
toggles), then the content.

```tsx
<Area hint={tr("jobs_panel.intro")} tools={<>
  <label>…filter…</label><div className="flex-1" /><span className="text-xs text-muted">12 of them</span>
</>}>
  <Listing>…</Listing>
</Area>
```

No hand-written `rounded-lg border border-line bg-card p-4` any more. Two areas below each
other (triggers: triggers plus events) stand in a `space-y-4`.

### `Listing`, `ListRow`, `ListingEmpty` — the entries

One surface, entries separated by lines, no sea of tiles.

```tsx
<Listing>
  {things.map((t) => (
    <ListRow key={t.id} dimmed={!t.enabled} warning={t.stuck} onClick={() => open(t)}>
      …
    </ListRow>
  ))}
  {things.length === 0 && <ListingEmpty>Nothing here yet.</ListingEmpty>}
</Listing>
```

- `dimmed` — switched off: visible, but visibly out of service.
- `warning` — a stripe on the left, for what needs attention (a stuck run, a series running
  empty). No coloured surface: the text has to stay readable.
- `onClick` — the whole row becomes the way in. Buttons inside it catch their own click.
- `columns` — a grid template (`sm:grid-cols-[…]`) when values really should line up under
  each other. **The header and the rows have to use the same template and the same column
  gap**, otherwise the heading stands beside its column instead of above it.

How a row is built: the **name** carries it (`font-medium text-ink`), everything technical
stands one floor lower and quieter (the key in `font-mono text-xs text-muted`, the kind as a
tag). On the right the state, on the far right the handles.

`ListHeader` exists for lists with many rows and real columns. With a handful of entries leave
it out — a heading row above five rows of content is noise.

### `SortBar` — the order of a list

Sorting stands in the `tools` row of the `Area`, not as a heading row above the entries.
Only a part of the lists really has columns; a run is a tag, a name, a state and a time in
one line, and a heading above it would point at nothing. The bar reads the same in every
list, whatever a row looks like inside.

```tsx
<Area hint={…} tools={<>
  <div className="flex-1" />
  <SortBar by={sort.by} dir={sort.dir} onSort={sort.toggle}
    fields={[{ key: "name", label: tr("sort.name") }, { key: "state", label: tr("sort.state") }]} />
</>}>
```

The active field carries the arrow, a click on it turns the direction round, a click on
another sorts by that one ascending. Leave the bar out where there is nothing to sort (one
entry) — a control that changes nothing is noise.

The state comes from `useListSort(list, fallback, values)`: `values` says how a row answers
per field, the hook holds the order in the profile of the person (`users.list_sort`) and
saves quietly. A new list needs its fields in `api/me.py` (`SORTABLE`) as well — the value
comes out of the browser and stays in the profile, so a typo would stay for good.

### `Tag` — a short, recurring value

A kind, a mode, an origin, a count, a project key. The colour is a **role**, not a colour
value:

| Colour | Meaning |
|---|---|
| `neutral` | a statement without a judgement (the default) |
| `green` | fine, running, done |
| `yellow` | watch out, unfinished, a deviation |
| `red` | broken, switched off, failed |
| `blue` | in progress |
| `violet` | an event, a listener, a set of one's own |
| `brand` | the one that applies right now |

Never `bg-amber-500/15` and its relatives by hand — the same meaning looked different on three
pages that way.

### `State` — a dot plus a word

For **the one** state of an entry (published · draft only · off). The colour carries the
urgency, the word the meaning — a colour alone is no information.

### Actions

- `Actions` plus `IconButton` — the handles at the right end of a row (edit, on/off, delete).
  `danger` turns red only on hover: the warning belongs to the moment, not to the resting
  state.
- `Rowbutton` — a named secondary action in a row ("Versions", "History", "Customise").
- The **one** main thing of a page is a button in the brand colour: `rounded bg-brand px-3
  py-1.5 text-sm text-white`, below the list ("+ New flow").

### Forms and dialogs

`Dialog` plus `DialogFoot` for creating and editing, `Field` for a label and a hint,
`INPUT_VALUE` as the class chain for input fields, `Errorrow` for an error above a list or in
a dialog. An error is never built as a raw red `div`.

### Tabs inside a page

`Tab` — for toggles that do **not** change the address (the assistant: chat · incoming ·
rules; a filter above a list). It looks like the page navigation because it does the same
thing; the eye should not have to learn a second language for the same movement.

### `LINE`

The classes of an entry as a constant — for the cases where the entry has to be a `<Link>`
(middle click, context menu) and therefore cannot be a `ListRow`. Otherwise always take the
component.

## Navigation

The tab rail (`usePageChrome`) has **no** frame of its own: below it stand cards, and a
navigation in the same frame as the content reads as another box. It is carried by a line — on
the side to the right, on top below. The active tab carries the brand colour.

**Always `layout="side"`** — the narrow column beside the content. One movement, one shape:
the flows page was the only one standing across the top and fell out of the picture because of
it, although nothing else about it was wrong. `"top"` is left only for a page that really
needs its full width. Below `md` both look the same, because there is no room beside the
content.

## Language

The source language of the interface is English. Labels say what happens ("Report the sorting
out", not "Notify"); the explaining sentence of an area is a sentence, not a heading; numbers
get their unit ("12 matters", not "12"). Every visible text is a key in `src/i18n/en.json`,
never a literal in the component — the German catalog and everything an admin translates hang
off exactly those keys.

## When something is missing

Look here first, then in `ui.tsx`. If the building block really is missing, it goes **there**
and into this file — not as a chain of classes into your own page. That is exactly how the
five different flow tabs came about.
