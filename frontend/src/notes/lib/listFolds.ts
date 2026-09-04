import { StateEffect, StateField } from '@codemirror/state';

/**
 * Which list items are folded, on its own.
 *
 * It used to live in `livePreview`, which is where it is used — but a note
 * script folds lists too, and `dataview/dvjs` therefore reached back into
 * `livePreview` while `livePreview` was importing the dataview barrel. A
 * circle, and one that cost nothing until the bundler happened to order the
 * two the other way round: then a CodeMirror extension was read before it
 * existed and the whole note view came up as "Cannot access … before
 * initialization".
 *
 * A leaf module cuts it. Both sides import from here, neither from the other.
 */

/** Toggle the fold of the list item whose line starts at this position. */
export const toggleListFold = StateEffect.define<number>();

/** Replace the whole fold set — used by note scripts that fold on render. */
export const setListFolds = StateEffect.define<readonly number[]>();

export const listFoldState = StateField.define<readonly number[]>({
  create: () => [],
  update(value, tr) {
    let v = tr.docChanged ? value.map((p) => tr.changes.mapPos(p, 1)) : [...value];
    for (const e of tr.effects) {
      if (e.is(toggleListFold)) v = v.includes(e.value) ? v.filter((x) => x !== e.value) : [...v, e.value];
      else if (e.is(setListFolds)) v = [...e.value];
    }
    return v;
  },
});
