// Die globale Vollbildseite des Büros — `/buero`.
//
// ── Warum sie innerhalb von `<Layout>` bleibt und trotzdem alles bedeckt ────────────────────
//
// Genau nach dem Vorbild von `WorkflowEditor.tsx`: die Seite bleibt eine gewöhnliche Route
// innerhalb von `<PageChromeProvider><Layout>` und rendert `fixed inset-0 z-30`. Zwei Dinge
// hängen daran, und beide wären ohne sie kaputt:
//
//   · **`z-30` deckt die Kopfzeile.** Die ist `sticky top-0 z-10`; alles darunter läge sonst
//     unter ihr statt über ihr.
//   · **Kein `usePageChrome`.** Der Hook räumt beim Verlassen auf (`setChrome({title:"",tabs:[]})`),
//     also wischt allein das *Nicht*-Aufrufen das Untermenü der vorigen Seite weg. Würde diese
//     Seite eigene Reiter setzen, stünden sie unter dem Vollbild und niemand sähe sie.
//
// ── Was in der URL steht ────────────────────────────────────────────────────────────────────
//
//   `?project=KEY`  Umfang. Fehlt er, ist der Umfang global — alle Projekte, die der Nutzer
//                   sehen darf (autorisiert wird serverseitig, nie hier).
//   `?sid=issue:412` Der Raum. `officeApi.sessions` liefert die Liste; ohne Angabe nimmt die
//                   Ansicht die jüngste. Ohne diesen Parameter wäre `?at=` sinnlos — ein
//                   Zeitpunkt ohne Raum zeigt auf nichts.
//   `?at=<epoch-ms>` Startpunkt der Wiedergabe.
//
// Geschrieben wird mit `replace: true`: ein geteilter Link auf einen Moment ist die ganze
// Absicht, aber jeder Klick auf die Zeitleiste einen Eintrag im Verlauf wäre eine Zumutung
// für den Zurück-Knopf.

import { useCallback, useMemo, useRef } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, type Project } from "../api";
import OfficeView from "../components/office/OfficeView.tsx";
import type { Scope } from "../components/office/api.ts";

export default function Office(): JSX.Element {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const projectKey = params.get("project");

  // Dieselbe Abfrage wie im Kopfbereich (`ProjectSwitcher`) — aus dem Cache, nicht neu.
  const { data: projects, isLoading } = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.get<Project[]>("/projects"),
  });
  const project = projectKey ? projects?.find((p) => p.key === projectKey) : undefined;

  // Nur **einmal** gelesen: die Ansicht besitzt Sprungpunkt und Raum danach selbst, und ein
  // Rücklesen aus der URL machte aus jedem Schreiben einen Kreis.
  const start = useRef<{ at: number | null; sid: string | null }>({
    at: (() => {
      const roh = params.get("at");
      const n = roh === null ? NaN : Number(roh);
      return Number.isFinite(n) && n > 0 ? n : null;
    })(),
    sid: params.get("sid"),
  });

  const scope = useMemo<Scope>(
    () => (project
      ? { kind: "project", projectId: project.id, projectKey: project.key }
      : { kind: "global" }),
    [project?.id, project?.key],
  );

  /** Ein Schreiber für beide Parameter — sie ändern sich nie unabhängig voneinander. */
  const schreibe = useCallback((feld: "at" | "sid", wert: string | null) => {
    setParams((vorher) => {
      const next = new URLSearchParams(vorher);
      if (wert === null) next.delete(feld);
      else next.set(feld, wert);
      return next;
    }, { replace: true });
  }, [setParams]);

  const onAtChange = useCallback(
    (ts: number | null) => schreibe("at", ts === null ? null : String(ts)),
    [schreibe],
  );
  const onSidChange = useCallback(
    (sid: string | null) => schreibe("sid", sid),
    [schreibe],
  );

  // Zurück dorthin, wo das Büro herkommt: in den Projekt-Reiter, wenn ein Projekt im Spiel
  // ist, sonst auf die Projektliste.
  const zurueck = () => navigate(projectKey ? `/projects/${projectKey}?tab=buero` : "/");

  // Steht `?project=` in der URL, aber die Projektliste ist noch unterwegs, wäre der Umfang
  // für einen Wimpernschlag „global" — und der Feed baute seinen Socket zweimal auf.
  const wartet = !!projectKey && isLoading;

  return (
    <div className="fixed inset-0 z-30 flex flex-col bg-surface">
      <div className="flex shrink-0 items-center gap-3 border-b border-line bg-card px-4 py-2">
        <button
          onClick={zurueck}
          className="rounded border border-line px-2 py-1 text-sm text-muted hover:text-ink"
        >
          ← {projectKey ? "Zurück zum Projekt" : "Zurück zur Übersicht"}
        </button>
        <h1 className="text-sm font-semibold">🏢 Büro</h1>
        <span className="font-mono text-xs text-muted">
          {projectKey ?? "alle Projekte"}
        </span>
        {!!projectKey && !isLoading && !project && (
          <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted"
            title="Kein Projekt mit diesem Schlüssel — die Ansicht zeigt deshalb alle Projekte.">
            unbekanntes Projekt
          </span>
        )}
      </div>

      {wartet ? (
        <div className="p-4 text-sm text-muted">Büro lädt…</div>
      ) : (
        <OfficeView
          scope={scope}
          variant="full"
          initialAt={start.current.at}
          onAtChange={onAtChange}
          initialSid={start.current.sid}
          onSidChange={onSidChange}
          onClose={zurueck}
          className="min-h-0 flex-1 p-3"
        />
      )}
    </div>
  );
}
