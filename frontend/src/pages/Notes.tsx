// The note workspace, `/notes`.
//
// ── Why it is a frame and no longer a full screen ───────────────────────────────────────────
//
// It used to render `fixed inset-0 z-30` after the model of `Office.tsx` and
// `WorkflowEditor.tsx`, deliberately covering the house's header: a writing surface has its
// own header, and a second row above it costs the room it needs.
//
// Two things took that reason away. The assistant moved out of the note sidebar into the
// house's header, so the row above is where one of the buttons now lives; and covering the
// header meant the bell, the user menu and the project switcher were missing on exactly the
// page one stands in longest. It also needed a measured pixel value for the phone, where the
// header is not covered, and a magic number that must match a `sticky` element elsewhere is a
// number that drifts.
//
// So it uses what the house already has for pages that fill the window: `frame` in
// `usePageChrome`. The main area then becomes a column that ends at the lower edge, the page
// fills it with `h-full`, and the header stays where it belongs. `wide` because a vault tree
// beside an editor beside a sidebar is not a reading column.
//
// The sub-menu row stays empty on purpose: the workspace carries its own tabs, and two rows
// of tabs above each other is the picture the design guide is about. `flush` takes the main
// area's padding away for the same reason: the tree stands against the left border and the
// tab bar against the top one, so a strip of background between frame and content would look
// like a mistake, and be one.
//
// ── What is loaded here, and what is not ────────────────────────────────────────────────────
//
// The workspace under `src/notes/` is a large piece of a former application: an editor, a
// canvas, a graph, a calendar. It is loaded lazily, and its stylesheet with it, so none of
// that weight travels in the bundle of a page that never opens it. Vite gives a lazy chunk
// its own CSS file, which is what makes the import below cost nothing anywhere else.
//
// It talks to `/api/notes-native/*`, which is this backend.
import { Suspense, lazy } from "react";
import { usePageChrome } from "../pageChrome";
import { Spinner } from "../components/ui";

const Workbench = lazy(() => import("../notes/Workbench"));

export default function Notes(): JSX.Element {
  usePageChrome("", [], undefined, "top", { wide: true, frame: true, flush: true });
  return (
    <div className="flex h-full min-h-0 w-full flex-col overflow-hidden bg-surface">
      <Suspense fallback={<div className="p-6"><Spinner /></div>}>
        <Workbench />
      </Suspense>
    </div>
  );
}
