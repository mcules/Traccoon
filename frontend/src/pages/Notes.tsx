// The note workspace, `/notes`.
//
// ── Why it covers the screen, and how ───────────────────────────────────────────────────────
//
// Same model as `Office.tsx` and `WorkflowEditor.tsx`: an ordinary route inside
// `<PageChromeProvider><Layout>` that renders `fixed inset-0 z-30`. Both halves matter and
// neither is an oversight:
//
//   · **`z-30` covers the header.** That one is `sticky top-0 z-10`, so anything below would
//     lie under it instead of above it.
//   · **No `usePageChrome`.** The hook cleans up when a page is left, so not calling it is
//     what wipes the sub-menu of the previous page away. A writing surface has its own
//     header anyway; a second row above it would only cost the room it needs.
//
// `RAIL_LEAVEBLANK` keeps the area rail free. A full screen with no way out is a trap, and
// this is the area one stands in longest.
//
// Below `md` there is no rail: the way to every other area is the burger in the header, and
// the header is what a full screen covers. So on a phone this page starts under it instead
// of over it. Measured rather than guessed: that header is 57px there. Without this the area
// was exactly the trap the paragraph above is about, and worse, because the browser's back
// button was the only way out.
//
// ── What is loaded here, and what is not ────────────────────────────────────────────────────
//
// The workspace under `src/notes/` is a large piece of a former application: an editor, a
// canvas, a graph, a calendar. It is loaded lazily, and its stylesheet with it, so none of
// that weight travels in the bundle of a page that never opens it. Vite gives a lazy chunk
// its own CSS file, which is what makes the import below cost nothing anywhere else.
//
// It talks to `/api/notes/*`, the bridge in `backend/app/api/notes.py`. Behind that bridge
// the workspace is still a service of its own while it is being rewritten; nothing on this
// side needs to know that, and nothing here should start to depend on it.
import { Suspense, lazy } from "react";
import { RAIL_LEAVEBLANK } from "../nav";
import { Spinner } from "../components/ui";

const Workbench = lazy(() => import("../notes/Workbench"));

export default function Notes(): JSX.Element {
  return (
    <div className={`fixed inset-x-0 bottom-0 top-[57px] z-30 flex flex-col bg-surface md:top-0 ${RAIL_LEAVEBLANK}`}>
      <Suspense fallback={<div className="p-6"><Spinner /></div>}>
        <Workbench />
      </Suspense>
    </div>
  );
}
