// Schicht 2 — das Büro als Projekt-Reiter.
//
// Die begrenzte Fassung: Kopfzeile, Bühne, Zeitleiste. Kein Dock, kein Inspektor — dafür ist
// der Reiter zu schmal, und wer sie braucht, ist einen Klick vom Vollbild entfernt.
//
// ── Warum hier **kein** `fixed inset-0` steht ───────────────────────────────────────────────
//
// `<main>` in `Layout.tsx` ist `mx-auto max-w-[1400px] p-3` — eine begrenzte, mitlaufende
// Spalte unter einer `sticky`-Kopfzeile. Eine Bühne, die sich daraus mit `fixed` herauslöst,
// läge über der Kopfzeile und über dem Untermenü des Projekts. Deshalb bekommt sie eine
// Seitenverhältnis-Kachel (`aspect-[16/9] w-full`, gesetzt in `OfficeView`) und bleibt im
// Fluss. Die Vollbildansicht ist eine eigene Seite (`pages/Office.tsx`), kein CSS-Trick.

import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import type { Project } from "../../api";
import OfficeView from "./OfficeView.tsx";
import type { Scope } from "./api.ts";

export default function OfficeTab({ project }: { project: Project }): JSX.Element {
  const navigate = useNavigate();
  // Stabile Identität: `useOfficeFeed` hängt seinen Socket zwar an einem abgeleiteten
  // Schlüssel auf, aber ein Objekt, das sich bei jedem Render erneuert, ist eine Falle,
  // die man nicht auslegen muss.
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
