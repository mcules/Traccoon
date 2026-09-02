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
// ── Where the data comes from ───────────────────────────────────────────────────────────────
//
// `/api/notes/*`, which is the bridge in `backend/app/api/notes.py`. The workspace behind it
// is still a service of its own while it is being rewritten; nothing here needs to know that.
// The one thing this page has to do on arrival is ask for the reading cookie: a picture in a
// note is an `<img src>` and carries no token, so without that call every image stays blank.
import { useEffect, useState } from "react";
import { api } from "../api";
import { tr } from "../i18n";
import { RAIL_LEAVEBLANK } from "../nav";
import { Area, Spinner, State } from "../components/ui";

type TreeNode = {
  name: string;
  path: string;
  type: "file" | "folder";
  children?: TreeNode[];
};

export default function Notes(): JSX.Element {
  const [tree, setTree] = useState<TreeNode | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    // The cookie first, the tree second: an image asked for before the cookie
    // exists comes back as a 401 the browser then caches as a broken picture.
    api
      .post("/notes/session", {})
      .then(() => api.get<TreeNode>("/notes/files/"))
      .then((t) => alive && setTree(t))
      .catch((e) => alive && setError(String(e?.message || e)));
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div className={`fixed inset-0 z-30 flex flex-col bg-surface ${RAIL_LEAVEBLANK}`}>
      <div className="flex items-center gap-3 border-b border-line px-4 py-2">
        <span className="text-sm font-semibold text-ink">{tr("nav.notes")}</span>
        {tree && <span className="text-xs text-muted">{tree.name}</span>}
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-4">
        {error && <Area><State color="red" text={error} /></Area>}
        {!error && !tree && <Area><Spinner /></Area>}
        {tree && (
          <Area>
            <ul className="text-sm text-ink">
              {(tree.children ?? []).map((c) => (
                <li key={c.path} className="py-0.5">
                  {c.type === "folder" ? "📁" : "📄"} {c.name}
                </li>
              ))}
            </ul>
          </Area>
        )}
      </div>
    </div>
  );
}
