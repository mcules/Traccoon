import { useStore } from './store';

type Store = ReturnType<typeof useStore.getState>;

/**
 * The back gesture closes what is on top, instead of leaving the app.
 *
 * On a phone, back is the universal "undo this screen": it should close the
 * drawer, the dialog, the tab list. The browser only knows about pages, so
 * every overlay pushes a history entry of its own when it opens; back pops
 * that entry, and we close the overlay it belongs to. Only when nothing is
 * open does back mean what it always meant — the previously opened note.
 *
 * Overlays that are closed by other means (a tap on the backdrop, Escape) take
 * their history entry with them, so the stack never drifts away from what is
 * actually on screen.
 */

interface Overlay {
  id: string;
  open: (s: Store) => boolean;
  close: () => void;
}

/** Top of the list is closed first when several are open at once. */
const OVERLAYS: Overlay[] = [
  { id: 'context', open: (s) => !!s.contextMenu, close: () => useStore.getState().closeContextMenu() },
  { id: 'palette', open: (s) => s.paletteOpen, close: () => useStore.getState().setPalette(false) },
  { id: 'folderPicker', open: (s) => !!s.movePath, close: () => useStore.getState().setMovePath(null) },
  { id: 'template', open: (s) => s.templatePickerOpen, close: () => useStore.getState().setTemplatePicker(false) },
  { id: 'attach', open: (s) => s.attachSheetOpen, close: () => useStore.getState().setAttachSheet(false) },
  { id: 'history', open: (s) => !!s.versionHistoryPath, close: () => useStore.getState().setVersionHistory(null) },
  { id: 'trash', open: (s) => s.trashOpen, close: () => useStore.getState().setTrash(false) },
  { id: 'settings', open: (s) => s.settingsOpen, close: () => useStore.getState().setSettings(false) },
  { id: 'present', open: (s) => !!s.presenting, close: () => useStore.getState().setPresenting(null) },
  { id: 'tabs', open: (s) => s.tabSwitcherOpen, close: () => useStore.getState().setTabSwitcher(false) },
  { id: 'search', open: (s) => s.mobileSearchOpen, close: () => useStore.getState().setMobileSearch(false) },
  { id: 'drawer', open: (s) => !!s.mobileDrawer, close: () => useStore.getState().setMobileDrawer(null) },
];

/** Overlay ids currently on screen, in the order they must be closed. */
function openIds(s: Store): string[] {
  return OVERLAYS.filter((o) => o.open(s)).map((o) => o.id);
}

let stack: string[] = [];
/** History entries we popped ourselves — their popstate is not a navigation. */
let selfBacks = 0;
/** Set while a popstate was consumed by an overlay, read by the URL sync. */
let consumed = false;
let installed = false;

export const popConsumedByOverlay = () => consumed;

export function installOverlayHistory(): () => void {
  if (installed) return () => {};
  installed = true;

  const unsub = useStore.subscribe((state, prev) => {
    const now = openIds(state);
    const before = openIds(prev);
    if (now.join() === before.join()) return;

    // Newly opened overlays get an entry each, in the order they appear.
    for (const id of now) {
      if (!stack.includes(id)) {
        stack.push(id);
        history.pushState({ overlay: id, depth: stack.length }, '', location.href);
      }
    }
    // Overlays closed by a tap or Escape give their entry back. Going back is
    // asynchronous, so the count is remembered and the popstate ignored.
    const gone = stack.filter((id) => !now.includes(id));
    if (gone.length) {
      stack = stack.filter((id) => now.includes(id));
      selfBacks += gone.length;
      history.go(-gone.length);
    }
  });

  const onPop = (e: PopStateEvent) => {
    consumed = false;
    if (selfBacks > 0) {
      // Our own history.go() coming back — the overlay is already closed.
      selfBacks--;
      consumed = true;
      e.stopImmediatePropagation();
      return;
    }
    const id = stack.at(-1);
    if (!id) return; // nothing open: a real navigation, the URL sync takes it
    stack.pop();
    consumed = true;
    e.stopImmediatePropagation();
    OVERLAYS.find((o) => o.id === id)?.close();
  };
  // Registered before the URL sync so it can stop the event reaching it.
  window.addEventListener('popstate', onPop);

  return () => {
    unsub();
    window.removeEventListener('popstate', onPop);
    installed = false;
  };
}
