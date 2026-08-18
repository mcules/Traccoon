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
//   `?kiosk=1`      Wandschirm: keine Bedienung, keine Kopfzeile, Selbstheilung an.
//
// Geschrieben wird mit `replace: true`: ein geteilter Link auf einen Moment ist die ganze
// Absicht, aber jeder Klick auf die Zeitleiste einen Eintrag im Verlauf wäre eine Zumutung
// für den Zurück-Knopf.
//
// ══ Der Kiosk ═══════════════════════════════════════════════════════════════════════════════
//
// **Ein Parameter, keine zweite Route.** Eine eigene Route wäre eine Kopie dieser Datei
// gewesen, und `?project=`/`?sid=`/`?at=` hätten von da an zweimal gepflegt werden müssen.
// Vorgabe ist der **globale** Umfang: ein Wandschirm beantwortet „was tut das Haus gerade";
// 516 von 632 Läufen sind unter fünf Minuten fertig, ein einzelner Projektraum wäre die
// meiste Zeit ein leerer Schreibtisch. `?kiosk=1&project=TRA` bleibt trotzdem möglich.
//
// **Vollbild wird nicht automatisch angefordert.** `requestFullscreen()` verlangt eine
// Nutzergeste und scheitert sonst still — deshalb gibt es in der Ansicht einen ⛶-Knopf und
// hier keine Zeile dazu. In der Praxis startet die Wand als `chromium --kiosk`; wer das
// später „repariert", baut eine Funktion, die nie funktioniert hat.
//
// ── Selbstheilung: was hier wacht, und was schon vorher heilte ──────────────────────────────
//
//   · **WS-Abbruch** — heilt `useOfficeFeed` selbst (Backoff plus voller Schnappschuss).
//     Hier steht bewusst nichts.
//   · **Token abgelaufen** — `useTokenKeepalive` erneuert alle sechs Stunden; ohne das wäre
//     der Schirm nach `jwt_expire_minutes` (720) ein Anmeldeformular.
//   · **Socket-Schließcode 4401/4403** — dagegen hilft kein Wiederverbinden, wohl aber ein
//     frischer Seitenaufbau mit frischem Token: Neuladen nach `WACHHUND_AUTH_MS`.
//   · **Vertragsbruch (`hello.v` ≠ `EVENT_VERSION`)** — sofort neu laden. Das heißt, es wurde
//     ein neues Frontend ausgerollt; Neuladen ist hier nicht die Notbremse, sondern genau die
//     richtige Antwort.
//   · **Jeder andere Dauerfehler** — nach `WACHHUND_FEHLER_MS`.
//   · **Renderausnahme** — fängt die `ErrorBoundary` und lädt nach zehn Sekunden neu.
//   · **Einmal je Nacht** — der Recorder hält bis `REPLAY_CAP` Einträge und das DOM lief
//     einen ganzen Tag; ein Schnitt zu einer festen Stunde kostet nichts und räumt alles.
//
// Alle Neuladewege laufen über `sicheresNeuladen` und sind damit gegen die eine Gefahr
// gesichert, die schlimmer ist als ein toter Schirm: die Neulade-Schleife.
//
// ── Warum der Wachhund die Fehlermeldung liest statt eines Fehlercodes ──────────────────────
//
// `useOfficeFeed` meldet seinen Zustand heute als **einen** deutschen Satz. Ihn dort um einen
// maschinenlesbaren Grund zu erweitern, hieße eine Datei anzufassen, die einer anderen Welle
// gehört. Deshalb erkennt `frist()` die zwei Sonderfälle an einem Textstück — eng an der
// Quelle, mit Verweis, und fail-safe: wer nichts erkennt, bekommt die lange Frist.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { tr } from "../i18n";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, type Project } from "../api";
import OfficeView from "../components/office/OfficeView.tsx";
import ErrorBoundary, { sicheresNeuladen } from "../components/ErrorBoundary.tsx";
import type { Scope } from "../components/office/api.ts";
import { useWakeLock } from "../hooks/useWakeLock.ts";
import { useTokenKeepalive } from "../hooks/useTokenKeepalive.ts";

// ── Stellschrauben des Wachhunds ────────────────────────────────────────────────────────────

/** So lange darf ein Fehler stehen, bevor neu geladen wird. Zwei Minuten sind länger als
 *  jeder Backend-Neustart und als die volle Wiederverbindungstreppe des Feeds — was so lange
 *  bleibt, geht von allein nicht mehr weg. */
const WACHHUND_FEHLER_MS = 120_000;

/** Authentifizierungsfehler des Sockets (4401/4403). Kürzer, weil hier ein frischer
 *  Seitenaufbau mit erneuertem Token tatsächlich etwas ändern kann. */
const WACHHUND_AUTH_MS = 60_000;

/** Mindestabstand zweier automatischer Neuladeversuche desselben Grundes. */
const NEULADEN_ABSTAND_MS = 10 * 60_000;

/** Renderausnahme: kurz warten (vielleicht war es ein Ereignis, das gleich vorbei ist),
 *  dann neu aufbauen. */
const BOUNDARY_RELOAD_MS = 10_000;

/** Der nächtliche Schnitt, Ortszeit. 4 Uhr: nach dem 19-Uhr-Gipfel und vor allem, was
 *  morgens anläuft. */
const NACHT_STUNDE = 4;

/**
 * Wie lange dieser Fehler stehen darf. `0` heißt sofort.
 *
 * Die Textstücke stammen wörtlich aus `components/office/useOfficeFeed.ts` (Socket-`onclose`
 * bzw. `hello`-Behandlung). Ändert sich dort die Formulierung, greift hier still die
 * lange Frist — schlechter, aber nie falsch.
 */
function frist(meldung: string): number {
  if (meldung.includes("Vertragsversion")) return 0;
  if (meldung.includes("Keine Berechtigung")) return WACHHUND_AUTH_MS;
  return WACHHUND_FEHLER_MS;
}

function grundVon(meldung: string): string {
  if (meldung.includes("Vertragsversion")) return "vertragsbruch";
  if (meldung.includes("Keine Berechtigung")) return "socket-auth";
  return "dauerfehler";
}

/** Millisekunden bis zur nächsten vollen `stunde` in Ortszeit. */
function bisZurStunde(stunde: number): number {
  const jetzt = new Date();
  const ziel = new Date(jetzt);
  ziel.setHours(stunde, 0, 0, 0);
  if (ziel.getTime() <= jetzt.getTime()) ziel.setDate(ziel.getDate() + 1);
  return ziel.getTime() - jetzt.getTime();
}

export default function Office(): JSX.Element {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const projectKey = params.get("project");
  const kioskRoh = params.get("kiosk");
  const kiosk = kioskRoh === "1" || kioskRoh === "true";

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

  /** Esc im Kiosk: eine Ebene zurück in die bediente Vollbildseite, nicht aus dem Büro
   *  heraus — Raum und Sprungpunkt bleiben stehen, man will ja meist genau hier eingreifen. */
  const kioskVerlassen = useCallback(() => {
    setParams((vorher) => {
      const next = new URLSearchParams(vorher);
      next.delete("kiosk");
      return next;
    }, { replace: true });
    if (document.fullscreenElement) void document.exitFullscreen?.().catch(() => {});
  }, [setParams]);

  // ── Selbstheilung ─────────────────────────────────────────────────────────────────────────

  useWakeLock(kiosk);
  useTokenKeepalive(kiosk);

  const [fehler, setFehler] = useState<string | undefined>(undefined);

  // Wachhund für Dauerfehler. Der Zeitgeber wird bei **jedem** Wechsel der Meldung neu
  // gestellt: ein Fehler, der kommt und geht, ist kein Dauerfehler, und ein anderer Fehler
  // ist ein anderer Fall.
  useEffect(() => {
    if (!kiosk || !fehler) return;
    const wartezeit = frist(fehler);
    const grund = grundVon(fehler);
    if (wartezeit === 0) {
      // Vertragsbruch: ein neues Frontend liegt auf dem Server, dieses hier ist von gestern.
      sicheresNeuladen(grund, NEULADEN_ABSTAND_MS);
      return;
    }
    const timer = window.setTimeout(
      () => sicheresNeuladen(grund, NEULADEN_ABSTAND_MS), wartezeit);
    return () => window.clearTimeout(timer);
  }, [kiosk, fehler]);

  // Der nächtliche Schnitt. Ein einziger Zeitgeber statt einer Uhrabfrage im Takt — die
  // Frist ist immer unter 24 h und passt damit bequem in `setTimeout`.
  useEffect(() => {
    if (!kiosk) return;
    const timer = window.setTimeout(
      () => sicheresNeuladen("nacht", NEULADEN_ABSTAND_MS), bisZurStunde(NACHT_STUNDE));
    return () => window.clearTimeout(timer);
  }, [kiosk]);

  // Steht `?project=` in der URL, aber die Projektliste ist noch unterwegs, wäre der Umfang
  // für einen Wimpernschlag „global" — und der Feed baute seinen Socket zweimal auf.
  const wartet = !!projectKey && isLoading;

  return (
    <div className="fixed inset-0 z-30 flex flex-col bg-surface">
      {/* Die Kopfzeile dieser Seite (Zurück-Knopf) ist Bedienung — im Kiosk fällt sie weg.
          Was der Wandschirm an Beschriftung braucht, steht in der Kopfzeile der Ansicht. */}
      {!kiosk && (
        <div className="flex shrink-0 items-center gap-3 border-b border-line bg-card px-4 py-2">
          <button
            onClick={zurueck}
            className="rounded border border-line px-2 py-1 text-sm text-muted hover:text-ink"
          >
            ← {tr(projectKey ? "office.zurueck_projekt" : "office.zurueck_uebersicht")}
          </button>
          <h1 className="text-sm font-semibold">{tr("office.buero")}</h1>
          <span className="font-mono text-xs text-muted">
            {projectKey ?? tr("office.alle_projekte")}
          </span>
          {!!projectKey && !isLoading && !project && (
            <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted"
              title={tr("office.kein_projekt_mit_diesem_schluessel_die_a")}>
              unbekanntes Projekt
            </span>
          )}
        </div>
      )}

      {wartet ? (
        <div className="p-4 text-sm text-muted">{tr("office.buero_laedt")}</div>
      ) : (
        <ErrorBoundary
          label="buero"
          reloadAfterMs={kiosk ? BOUNDARY_RELOAD_MS : undefined}
          reloadMinGapMs={NEULADEN_ABSTAND_MS}
        >
          <OfficeView
            scope={scope}
            variant={kiosk ? "kiosk" : "full"}
            initialAt={start.current.at}
            onAtChange={onAtChange}
            initialSid={start.current.sid}
            onSidChange={onSidChange}
            onErrorChange={setFehler}
            onClose={kiosk ? kioskVerlassen : zurueck}
            className="min-h-0 flex-1 p-3"
          />
        </ErrorBoundary>
      )}
    </div>
  );
}
