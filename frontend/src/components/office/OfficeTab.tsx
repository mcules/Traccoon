// Layer 2: the office as a project tab.
//
// The limited version: header, stage, timeline. No dock, no inspector, because the tab is too
// narrow for them, and whoever needs them is one click away from the full screen.
//
// ── Why there is **no** `fixed inset-0` here ────────────────────────────────────────────────
//
// `<main>` in `Layout.tsx` is `mx-auto max-w-[1400px] p-3`, a limited column running along
// under a `sticky` header. A stage that breaks out of it with `fixed` would lie over the
// header and over the sub-menu of the project. That is why it gets an aspect ratio tile
// (`aspect-[16/9] w-full`, set in `OfficeView`) and stays in the flow. The full screen view
// is a page of its own (`pages/Office.tsx`), not a CSS trick.

import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import type { Project } from "../../api";
import OfficeView from "./OfficeView.tsx";
import type { Scope } from "./api.ts";

export default function OfficeTab({ project }: { project: Project }): JSX.Element {
  const navigate = useNavigate();
  // Stable identity: `useOfficeFeed` does hang its socket off a derived key, but an object
  // that renews itself on every render is a trap one does not have to lay.
  const scope = useMemo<Scope>(
    () => ({ kind: "project", projectId: project.id, projectKey: project.key }),
    [project.id, project.key],
  );

  return (
    <OfficeView
      scope={scope}
      variant="tab"
      onFullscreen={() => navigate(`/buero?project=${encodeURIComponent(project.key)}`)}
    />
  );
}
