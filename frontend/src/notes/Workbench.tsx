// The stylesheet of this area. Imported here and not in the page, so it travels
// in the same lazy chunk as the code that needs it.
import './notes.css';
import { useEffect, useRef, useState } from 'react';
import { api } from './lib/api';
import { useStore } from './lib/store';
import { runCommand } from './lib/commands';
import { hotkeyCommand, loadHotkeys } from './lib/hotkeys';
import { registerCoreCommands } from './lib/coreCommands';
import { installHoverPreview } from './lib/hoverPreview';
import { loadVaultSnippets } from './lib/snippets';
import Ribbon from './components/Ribbon';
import Sidebar from './components/Sidebar';
import RightSidebar from './components/RightSidebar';
import Workspace from './components/Workspace';
import CommandPalette from './components/CommandPalette';
import TemplatePicker from './components/TemplatePicker';
import AttachSheet from './components/AttachSheet';
import AskDialog from './components/AskDialog';
import Settings from './components/Settings';
import VersionHistory from './components/VersionHistory';
import TrashView from './components/TrashView';
import ContextMenu from './components/ContextMenu';
import FolderPicker from './components/FolderPicker';
import TabSwitcher from './components/TabSwitcher';
import MobileSearch from './components/MobileSearch';
import Presentation from './components/Presentation';
import ShareTarget from './components/ShareTarget';
import { initUrlSync } from './lib/urlsync';
import { installOverlayHistory } from './lib/overlayHistory';
import { installOffline, keepOffline } from './lib/offline';
import { useIsMobile } from './lib/useIsMobile';
import { invalidateDataviewCache } from './lib/dataview';

export default function App() {
  const authed = useStore((s) => s.authed);
  const setAuthed = useStore((s) => s.setAuthed);
  const loadTree = useStore((s) => s.loadTree);
  const leftOpen = useStore((s) => s.leftOpen);
  const rightOpen = useStore((s) => s.rightOpen);
  const mobileDrawer = useStore((s) => s.mobileDrawer);
  const setMobileDrawer = useStore((s) => s.setMobileDrawer);
  const activePath = useStore((s) => s.activePath);
  const isMobile = useIsMobile();
  const setPalette = useStore((s) => s.setPalette);
  const save = useStore((s) => s.save);
  const toast = useStore((s) => s.toast);
  const [checking, setChecking] = useState(true);
  // Light or dark is the house's decision, not a second setting of this area.
  // It stands on `<html data-theme>`, and the wrapper class here only translates
  // it into the vocabulary this stylesheet was written in. Watched rather than
  // read once: switching it in the account page has to reach an open note
  // without a reload, and the switch happens outside this tree entirely.
  const [theme, setTheme] = useState<'theme-dark' | 'theme-light'>(
    () => (document.documentElement.dataset.theme === 'light' ? 'theme-light' : 'theme-dark'),
  );
  useEffect(() => {
    const root = document.documentElement;
    const apply = () => setTheme(root.dataset.theme === 'light' ? 'theme-light' : 'theme-dark');
    apply();
    const watch = new MutationObserver(apply);
    watch.observe(root, { attributes: true, attributeFilter: ['data-theme'] });
    return () => watch.disconnect();
  }, []);
  const appRef = useRef<HTMLDivElement>(null);
  // A share from another app arrives as a page load on /share-to; the address
  // is cleaned up as soon as it has been dealt with, so a reload does not ask
  // again about something already filed.
  const [sharing, setSharing] = useState(() => window.location.pathname === '/share-to');

  // Who is signed in is the house's question, and it has answered it before this
  // page renders at all. What is left to do on arrival is ask the bridge for the
  // reading cookie: a picture in a note is an `<img src>` and carries no token,
  // and one asked for before the cookie exists comes back as a 401 the browser
  // then keeps as a broken image.
  useEffect(() => {
    api
      .openNotesSession()
      .catch(() => {
        // No cookie means no pictures, and nothing else. Saying so in the log is
        // more use than a dialog over an editor that otherwise works.
        console.warn('notes: no reading cookie, images will stay blank');
      })
      .finally(() => {
        setAuthed(true);
        setChecking(false);
      });
  }, [setAuthed]);

  useEffect(() => {
    if (!authed) return;
    loadTree();
    // Before the URL sync, so back closes an open drawer or dialog first and
    // only navigates when there is nothing on top to close.
    const stopOverlayHistory = installOverlayHistory();
    installOffline();
    // Deep link (/note/<path>) wins over the restored workspace's active note.
    const deepLink = initUrlSync();
    useStore
      .getState()
      .loadUiState() // restore workspace from server + open note(s)
      .then(() => {
        if (deepLink && deepLink !== useStore.getState().activePath) {
          return useStore.getState().openFile(deepLink);
        }
      })
      .catch(() => {});
    // Commands first, then the vault's key bindings on top of their defaults.
    registerCoreCommands();
    api
      .vaultConfig()
      .then((c) => loadHotkeys(c.hotkeys))
      .catch(() => {});
    const stopHover = installHoverPreview();
    void loadVaultSnippets();
    // websocket live updates
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    let treeTimer: number | undefined;
    let retryTimer: number | undefined;
    let ws: WebSocket | null = null;
    let closed = false;
    let attempt = 0;

    /**
     * When to fetch the file list again.
     *
     * Fetching it costs about thirty milliseconds even for six thousand notes,
     * so the delay is there only to fold a burst into one call — a rename
     * arrives as a removal and an addition, a folder as one event per file.
     * A short wait does that; the ceiling makes sure a long burst still shows
     * something on the way rather than only at the end.
     */
    const TREE_QUIET_MS = 120;
    const TREE_MAX_WAIT_MS = 600;
    let treeFirstPending = 0;
    const scheduleTreeRefresh = () => {
      const now = Date.now();
      if (!treeFirstPending) treeFirstPending = now;
      window.clearTimeout(treeTimer);
      const run = () => {
        treeFirstPending = 0;
        void loadTree();
      };
      if (now - treeFirstPending >= TREE_MAX_WAIT_MS) run();
      else treeTimer = window.setTimeout(run, TREE_QUIET_MS);
    };

    const onMessage = (ev: MessageEvent) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === 'fs') {
          // A note changed on disk → the cached dataview pages are stale.
          invalidateDataviewCache();
          // The open note follows the file: pull the new text in and merge it
          // with whatever is unsaved, rather than waiting for a tab switch.
          if (typeof msg.path === 'string') {
            void useStore.getState().externalChange(msg.path, msg.hash);
          }
          // A burst of events is one refresh, but a burst must not be able to
          // postpone it for ever: Syncthing writing a hundred files kept
          // resetting the timer, so the tree stood still exactly when it was
          // changing most. Now the wait is short, and there is a ceiling on it.
          scheduleTreeRefresh();
        } else if (msg.type === 'open' && typeof msg.path === 'string') {
          // Something outside asked for a note to be put in front of a person —
          // an agent, a script, the REST API. Here is the person.
          void useStore.getState().openFile(msg.path);
        } else if (msg.type === 'reminders') {
          // Due tasks, found by the server — it keeps looking whether or not a
          // tab is open, so what arrives here is news, not a poll result.
          const n = msg.reminders?.length ?? 0;
          if (n) {
            const first = msg.reminders[0];
            const late = first.daysAway < 0 ? ` (${-first.daysAway} Tage überfällig)` : '';
            useStore
              .getState()
              .notify(
                n === 1
                  ? `Fällig: ${String(first.text).slice(0, 60)}${late}`
                  : `${n} Aufgaben fällig — erste: ${String(first.text).slice(0, 40)}${late}`,
                8000,
              );
          }
        } else if (msg.type === 'uistate') {
          // another tab/device changed the workspace → sync live
          useStore.getState().applyRemoteState(msg.state, msg.originId);
        }
      } catch {
        /* ignore */
      }
    };

    // A socket that is never rebuilt dies quietly on the first sleep or network
    // change — and with it every live update, without anything looking broken.
    const connect = () => {
      if (closed) return;
      // Through the bridge, like everything else. The reading cookie is what
      // gets it past the door: a browser cannot set a header on a socket.
      ws = new WebSocket(`${proto}://${location.host}/api/notes/ws`);
      ws.onmessage = onMessage;
      ws.onopen = () => {
        attempt = 0;
        // Whatever happened while we were deaf: refresh the tree and re-check
        // the open note against disk.
        void loadTree();
        const { activePath } = useStore.getState();
        if (activePath) void useStore.getState().externalChange(activePath);
      };
      ws.onclose = () => {
        if (closed) return;
        const wait = Math.min(30000, 500 * 2 ** attempt++);
        retryTimer = window.setTimeout(connect, wait);
      };
      ws.onerror = () => ws?.close();
    };
    connect();

    // Coming back from a locked screen usually finds a socket that is already
    // dead but not yet reported — nudge it.
    const wake = () => {
      if (document.visibilityState !== 'visible') return;
      if (!ws || ws.readyState === WebSocket.CLOSED) {
        window.clearTimeout(retryTimer);
        attempt = 0;
        connect();
      }
    };
    document.addEventListener('visibilitychange', wake);
    window.addEventListener('online', wake);

    return () => {
      stopHover();
      stopOverlayHistory();
      closed = true;
      document.removeEventListener('visibilitychange', wake);
      window.removeEventListener('online', wake);
      window.clearTimeout(treeTimer);
      window.clearTimeout(retryTimer);
      ws?.close();
    };
  }, [authed, loadTree]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey;
      if (!mod) return;
      const k = e.key.toLowerCase();
      const s = useStore.getState();
      // Shortcuts that belong to the shell itself, not to a command.
      if (k === 'p') { e.preventDefault(); setPalette(true, 'commands'); return; }
      if (k === 'o') { e.preventDefault(); setPalette(true, 'files'); return; }
      if (k === 'e') { e.preventDefault(); s.setViewMode(s.viewMode === 'reading' ? 'live' : 'reading'); return; }
      if (k === 'f' && e.shiftKey) { e.preventDefault(); s.setLeftPanel('search'); return; }
      if (k === '\\') { e.preventDefault(); s.toggleLeft(); return; }
      // Everything else is looked up in the command registry, so a command's
      // key lives with the command instead of in a second table.
      const id = hotkeyCommand(e);
      if (id && runCommand(id)) e.preventDefault();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [setPalette, save]);

  // Hand the recently opened notes to the service worker, so the notes one
  // actually reads are the ones that survive a dead spot.
  const recent = useStore((s) => s.recent);
  useEffect(() => {
    keepOffline([activePath, ...recent].filter(Boolean).slice(0, 30) as string[]);
  }, [recent, activePath]);

  // Mobile: close the overlay drawer once a note is opened (tap note → read it).
  useEffect(() => {
    if (isMobile && useStore.getState().mobileDrawer) setMobileDrawer(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activePath]);

  // Mobile: the drawers follow the finger instead of jumping.
  //
  // A swipe that only decides open-or-closed at the end feels like a button
  // that fires late; a drawer that moves with the thumb shows how far it has
  // come and lets one change one's mind halfway. The gesture starts at the
  // screen edge when nothing is open, and anywhere once a drawer is out.
  useEffect(() => {
    if (!isMobile) return;
    const root = appRef.current;
    if (!root) return;
    const DRAWER = () => Math.min(window.innerWidth * 0.86, 340);
    let sx = 0, sy = 0, side: 'left' | 'right' | null = null;
    let wasOpen = false, dragging = false, decided = false, progress = 0, lastX = 0, lastT = 0, speed = 0;

    const paint = (p: number) => {
      root.style.setProperty(side === 'left' ? '--dl' : '--dr', String(p));
    };
    const finish = (open: boolean) => {
      // The class change is React's; the inline value has to outlive it by a
      // frame, or the drawer snaps back to where the old class had it.
      setMobileDrawer(open ? side : null);
      requestAnimationFrame(() =>
        requestAnimationFrame(() => {
          root.classList.remove('dragging');
          root.style.removeProperty('--dl');
          root.style.removeProperty('--dr');
        }),
      );
    };

    const onStart = (e: TouchEvent) => {
      if (e.touches.length !== 1) return;
      const t = e.touches[0];
      sx = lastX = t.clientX;
      sy = t.clientY;
      lastT = e.timeStamp;
      speed = 0;
      decided = dragging = false;
      const open = useStore.getState().mobileDrawer;
      wasOpen = !!open;
      if (open) side = open;
      else if (sx <= 28) side = 'left';
      else if (sx >= window.innerWidth - 28) side = 'right';
      else side = null;
    };

    const onMove = (e: TouchEvent) => {
      if (!side || e.touches.length !== 1) return;
      const t = e.touches[0];
      const dx = t.clientX - sx, dy = t.clientY - sy;
      if (!decided) {
        if (Math.abs(dx) < 8 && Math.abs(dy) < 8) return;
        decided = true;
        // A mostly-vertical start is a scroll, and stays one.
        if (Math.abs(dy) > Math.abs(dx)) { side = null; return; }
        dragging = true;
        root.classList.add('dragging');
      }
      if (!dragging) return;
      const dt = e.timeStamp - lastT;
      if (dt > 0) speed = (t.clientX - lastX) / dt;
      lastX = t.clientX;
      lastT = e.timeStamp;
      const w = DRAWER();
      const base = wasOpen ? 1 : 0;
      const delta = side === 'left' ? dx / w : -dx / w;
      progress = Math.max(0, Math.min(1, base + delta));
      paint(progress);
      e.preventDefault(); // the page must not scroll sideways underneath
    };

    const onEnd = () => {
      if (!dragging || !side) { side = null; return; }
      dragging = false;
      // A flick decides on its own; a slow drag decides by how far it got.
      const flick = Math.abs(speed) > 0.5;
      const towardsOpen = side === 'left' ? speed > 0 : speed < 0;
      finish(flick ? towardsOpen : progress > 0.45);
      side = null;
    };

    window.addEventListener('touchstart', onStart, { passive: true });
    window.addEventListener('touchmove', onMove, { passive: false });
    window.addEventListener('touchend', onEnd, { passive: true });
    window.addEventListener('touchcancel', onEnd, { passive: true });
    return () => {
      window.removeEventListener('touchstart', onStart);
      window.removeEventListener('touchmove', onMove);
      window.removeEventListener('touchend', onEnd);
      window.removeEventListener('touchcancel', onEnd);
    };
  }, [isMobile, setMobileDrawer]);

  if (checking) return <div className={theme} style={{ height: '100%' }} />;

  // On mobile the sidebars are overlay drawers (always mounted, slid in/out by
  // CSS), driven by the device-local `mobileDrawer` state — not the persisted
  // leftOpen/rightOpen that sync across desktops.
  const showLeft = isMobile || leftOpen;
  const showRight = isMobile || rightOpen;
  const appCls = [
    'app',
    leftOpen ? '' : 'left-closed',
    rightOpen ? '' : 'right-closed',
    isMobile ? 'mobile' : '',
    isMobile && mobileDrawer === 'left' ? 'drawer-left-open' : '',
    isMobile && mobileDrawer === 'right' ? 'drawer-right-open' : '',
  ].filter(Boolean).join(' ');

  return (
    <div className={theme}>
      <div className={appCls} ref={appRef}>
        <Ribbon />
        {showLeft && <Sidebar />}
        <Workspace />
        {showRight && <RightSidebar />}
        {/* Always mounted on a phone: while a drawer is being dragged it has to
            darken gradually, which it cannot do if it appears only once open. */}
        {isMobile && <div className="drawer-backdrop" onClick={() => setMobileDrawer(null)} />}
      </div>
      <CommandPalette />
      <TemplatePicker />
      <AttachSheet />
      <AskDialog />
      <Settings />
      <VersionHistory />
      <TrashView />
      <ContextMenu />
      <FolderPicker />
      {sharing && (
        <ShareTarget
          onDone={() => {
            setSharing(false);
            window.history.replaceState(null, '', '/');
          }}
        />
      )}
      <TabSwitcher />
      <MobileSearch />
      <Presentation />
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}
