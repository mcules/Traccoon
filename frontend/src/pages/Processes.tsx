import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiError, api, processApi, workflowApi,
  type ProcAusloeser, type ProcLauf, type ProcSlot, type User,
} from "../api";
import { usePageChrome } from "../pageChrome";
import OwnWorkflowsPanel from "../components/workflow/OwnWorkflowsPanel";
import WorkflowInstanceView from "../components/workflow/WorkflowInstanceView";

/**
 * Prozess-Verwaltung — die Sicht über alle Abläufe hinweg.
 *
 * Seit jeder Ablauf ein Graph ist, verteilt sich das Wissen darüber auf Sätze, Projekt-Kopien,
 * Versionen und laufende Vorgänge. Diese Seite führt es zusammen: was ist der Standard, wer
 * weicht ab, was läuft gerade, und was stößt es an.
 *
 * Der erste Reiter sind die **eigenen** Abläufe: frei angelegt, an keinen Slot und an kein
 * Projekt gebunden. Sie standen früher in den Einstellungen — am falschen Ort, denn Abläufe
 * sind neben dem Assistenten und den Projekten ein tragender Teil von Traccoon und keine
 * Nebeneinstellung.
 */
type Tab = "eigene" | "standard" | "betrieb" | "ausloeser";
const TABS: [Tab, string][] = [
  ["eigene", "Eigene"], ["standard", "Standard-Satz"], ["betrieb", "Betrieb"],
  ["ausloeser", "Auslöser"],
];
const TAB_KEYS = TABS.map(([k]) => k);

export default function Processes() {
  const { tab: tabParam } = useParams();
  const tab: Tab = (TAB_KEYS.includes(tabParam as Tab) ? tabParam : "eigene") as Tab;
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: () => api.get<User>("/auth/me") });
  usePageChrome("Prozesse", TABS.map(([key, label]) => ({
    key, label, to: `/processes/${key}`,
    icon: { eigene: "✍️", standard: "🔀", betrieb: "📡", ausloeser: "⚡" }[key],
  })));
  return (
    <div>
      {tab === "eigene" && <OwnWorkflowsPanel isAdmin={me?.global_role === "admin"} />}
      {tab === "standard" && <StandardSatz />}
      {tab === "betrieb" && <Betrieb />}
      {tab === "ausloeser" && <Ausloeser />}
    </div>
  );
}

// ── Standard-Satz ────────────────────────────────────────────────────────────

function StandardSatz() {
  const nav = useNavigate();
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: () => api.get<User>("/auth/me") });
  const admin = me?.global_role === "admin";
  const [offen, setOffen] = useState<number | null>(null);

  const { data: slots } = useQuery({ queryKey: ["proc-slots"], queryFn: () => processApi.slots() });

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">
        Der ausgelieferte Standard gilt für jedes Projekt, das keine eigene Kopie angelegt hat —
        eine Änderung hier wirkt sofort überall dort.{" "}
        {admin
          ? "Laufende Vorgänge bleiben unberührt: sie hängen an der Version, mit der sie gestartet sind."
          : "Ändern darf ihn nur ein Admin; ansehen lohnt sich trotzdem, denn er beschreibt, wie Traccoon arbeitet."}
      </p>

      <div className="space-y-2">
        {slots?.map((s) => (
          <SlotZeile
            key={s.slot} s={s} admin={admin}
            offen={offen === s.definition_id}
            onToggle={() => setOffen(offen === s.definition_id ? null : s.definition_id)}
            onEdit={() => s.definition_id && nav(`/workflows/${s.definition_id}`, { state: { from: "/processes/standard" } })}
          />
        ))}
        {slots?.length === 0 && (
          <div className="rounded border border-line bg-card p-3 text-sm text-muted">
            Kein Standard-Satz vorhanden.
          </div>
        )}
      </div>
    </div>
  );
}

function SlotZeile({ s, admin, offen, onToggle, onEdit }: {
  s: ProcSlot; admin: boolean; offen: boolean; onToggle: () => void; onEdit: () => void;
}) {
  const nav = useNavigate();
  return (
    <div className="rounded border border-line bg-card p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{s.name}</span>
        {s.published ? (
          <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted">v{s.version}</span>
        ) : (
          <span className="rounded bg-yellow-500/15 px-1.5 py-0.5 text-xs text-yellow-300">
            nicht veröffentlicht
          </span>
        )}
        {s.abweichungen.length > 0 && (
          <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-xs text-amber-300"
                title="Diese Projekte haben eine eigene Kopie und folgen dem Standard nicht mehr">
            {s.abweichungen.length} Abweichung{s.abweichungen.length === 1 ? "" : "en"}
          </span>
        )}
        <div className="flex-1" />
        <button onClick={onToggle}
                className="rounded border border-line px-2 py-1 text-xs hover:border-brand">
          {offen ? "Versionen ausblenden" : "Versionen"}
        </button>
        {s.definition_id && (
          <button onClick={onEdit}
                  className="rounded border border-line px-2 py-1 text-xs hover:border-brand">
            {admin ? "Bearbeiten" : "Ansehen"}
          </button>
        )}
      </div>
      <div className="mt-1 text-xs text-muted">{s.description}</div>

      {s.abweichungen.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
          <span>Eigene Kopie:</span>
          {s.abweichungen.map((a) => (
            <button
              key={a.project_id}
              onClick={() => nav(`/projects/${a.project_key}/workflows/${a.definition_id}`, { state: { from: "/processes/standard" } })}
              className="rounded bg-amber-500/10 px-1.5 py-0.5 text-amber-300 hover:bg-amber-500/20"
              title={`${a.project_name} — eigene Fassung ansehen`}
            >
              {a.project_key}
            </button>
          ))}
        </div>
      )}

      {offen && s.definition_id && <Versionen defId={s.definition_id} darfSchreiben={admin} />}
    </div>
  );
}

/** Versionshistorie mit Zurückrollen — die alte Fassung wird als neue veröffentlicht. */
function Versionen({ defId, darfSchreiben }: { defId: number; darfSchreiben: boolean }) {
  const qc = useQueryClient();
  const [err, setErr] = useState("");
  const { data: versionen } = useQuery({
    queryKey: ["wf-versions", defId], queryFn: () => workflowApi.versions(defId),
  });
  const { data: def } = useQuery({
    queryKey: ["wf-def", defId], queryFn: () => workflowApi.get(defId),
  });

  const rollback = useMutation({
    mutationFn: (vid: number) => workflowApi.rollback(defId, vid),
    onSuccess: () => {
      setErr("");
      qc.invalidateQueries({ queryKey: ["wf-versions", defId] });
      qc.invalidateQueries({ queryKey: ["wf-def", defId] });
      qc.invalidateQueries({ queryKey: ["proc-slots"] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });

  return (
    <div className="mt-3 border-t border-line pt-2">
      {err && <div className="mb-2 rounded border border-red-500/40 bg-red-500/10 p-2 text-xs text-red-300">{err}</div>}
      <div className="space-y-1">
        {versionen?.map((v) => {
          const aktuell = def?.current_version_id === v.id;
          return (
            <div key={v.id} className="flex flex-wrap items-center gap-2 text-xs">
              <span className={`w-10 shrink-0 ${aktuell ? "font-medium text-ink" : "text-muted"}`}>
                v{v.version}
              </span>
              <span className={`rounded px-1.5 py-0.5 ${
                v.status === "published" ? "bg-green-500/15 text-green-300" : "bg-surface text-muted"
              }`}>
                {v.status === "published" ? "veröffentlicht" : "Entwurf"}
              </span>
              {aktuell && (
                <span className="rounded bg-brand/20 px-1.5 py-0.5 text-brand">aktuell</span>
              )}
              <span className="min-w-0 flex-1 truncate text-muted" title={v.notes || ""}>
                {v.notes || "—"}
              </span>
              <span className="shrink-0 text-muted">
                {v.published_at ? new Date(v.published_at).toLocaleDateString("de-DE") : ""}
              </span>
              {darfSchreiben && !aktuell && v.status === "published" && (
                <button
                  onClick={() => rollback.mutate(v.id)}
                  disabled={rollback.isPending}
                  className="shrink-0 rounded border border-line px-1.5 py-0.5 hover:border-amber-400 disabled:opacity-50"
                  title="Diese Fassung wieder in Kraft setzen (als neue Version)"
                >
                  Zurückrollen
                </button>
              )}
            </div>
          );
        })}
        {versionen?.length === 0 && <div className="text-xs text-muted">Noch keine Version.</div>}
      </div>
    </div>
  );
}

// ── Betrieb ──────────────────────────────────────────────────────────────────

const STATUS_STIL: Record<ProcLauf["status"], string> = {
  running: "bg-blue-500/15 text-blue-300",
  waiting: "bg-surface text-muted",
  failed: "bg-red-500/15 text-red-300",
  completed: "bg-green-500/15 text-green-300",
  cancelled: "bg-surface text-muted",
};
const STATUS_TEXT: Record<ProcLauf["status"], string> = {
  running: "läuft", waiting: "wartet", failed: "gescheitert",
  completed: "fertig", cancelled: "abgebrochen",
};
const WARTET_AUF: Record<string, string> = {
  human_task: "auf eine Person", approval: "auf eine Freigabe", agent: "auf den Agenten",
  timer: "auf einen Zeitpunkt", event: "auf ein Ereignis", gate: "auf ein freies Zeitfenster",
  subflow: "auf einen Unterprozess",
};

function Betrieb() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [nurHaengt, setNurHaengt] = useState(false);
  const [mitFertigen, setMitFertigen] = useState(false);
  const [err, setErr] = useState("");
  // Aufgeklappter Lauf: Graph plus Protokoll. Ein Vorgang, der hängt oder gescheitert ist,
  // wirft immer dieselbe Frage auf — was kam zurück, und warum ging es dann dort weiter?
  const [offen, setOffen] = useState<number | null>(null);

  const { data: laeufe } = useQuery({
    queryKey: ["proc-running", nurHaengt, mitFertigen],
    queryFn: () => processApi.running({ onlyStuck: nurHaengt, includeDone: mitFertigen }),
    refetchInterval: 20000,
  });

  const abbrechen = useMutation({
    mutationFn: (iid: number) => workflowApi.cancel(iid),
    onSuccess: () => {
      setErr("");
      qc.invalidateQueries({ queryKey: ["proc-running"] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });

  const haengen = laeufe?.filter((l) => l.haengt).length ?? 0;

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">
        Alle offenen Vorgänge über die Projekte hinweg. „Wartet“ ist der Normalfall — ein Ablauf,
        der auf einen Menschen wartet, ist nicht kaputt. Auffällig wird er, wenn er länger als
        einen Tag am selben Schritt steht oder gescheitert ist.
      </p>

      <div className="flex flex-wrap items-center gap-3 text-sm">
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={nurHaengt} onChange={(e) => setNurHaengt(e.target.checked)} />
          Nur Auffälliges{haengen > 0 && !nurHaengt ? ` (${haengen})` : ""}
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={mitFertigen} onChange={(e) => setMitFertigen(e.target.checked)} />
          Abgeschlossene mitzeigen
        </label>
        <div className="flex-1" />
        <span className="text-xs text-muted">
          {laeufe?.length ?? 0} {laeufe?.length === 1 ? "Vorgang" : "Vorgänge"}
        </span>
      </div>

      {err && <div className="rounded border border-red-500/40 bg-red-500/10 p-2 text-sm text-red-300">{err}</div>}

      <div className="space-y-2">
        {laeufe?.map((l) => (
          <div key={l.id}
               className={`rounded border p-3 ${l.haengt ? "border-amber-500/40 bg-amber-500/5" : "border-line bg-card"}`}>
            <div className="flex flex-wrap items-center gap-2">
              <span className={`rounded px-1.5 py-0.5 text-xs ${STATUS_STIL[l.status]}`}>
                {STATUS_TEXT[l.status]}
              </span>
              <span className="font-medium">{l.definition_name}</span>
              {l.project_key && (
                <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted">{l.project_key}</span>
              )}
              {l.subject_ref && (
                <button
                  onClick={() => nav(l.subject_ref?.startsWith("HW-")
                    ? `/projects/${l.project_key}/hardware`
                    : `/projects/${l.project_key}/tickets/${l.subject_ref}`)}
                  className="rounded bg-surface px-1.5 py-0.5 text-xs text-ink hover:text-brand"
                >
                  {l.subject_ref}
                </button>
              )}
              <div className="flex-1" />
              {l.stunden != null && (
                <span className={`text-xs ${l.haengt ? "text-amber-300" : "text-muted"}`}>
                  {l.stunden < 48 ? `${l.stunden} h` : `${Math.round(l.stunden / 24)} Tage`}
                </span>
              )}
              <button
                onClick={() => setOffen(offen === l.id ? null : l.id)}
                className="rounded border border-line px-2 py-1 text-xs hover:border-brand"
                title="Verlauf des Vorgangs"
              >
                {offen === l.id ? "Verlauf zu" : "Verlauf"}
              </button>
              {(l.status === "running" || l.status === "waiting") && (
                <button
                  onClick={() => abbrechen.mutate(l.id)}
                  className="rounded border border-line px-2 py-1 text-xs hover:border-red-400"
                  title="Vorgang abbrechen"
                >
                  Abbrechen
                </button>
              )}
            </div>
            <div className="mt-1 text-xs text-muted">
              steht bei <span className="text-ink">{l.node_label || "—"}</span>
              {l.waiting_for && ` — wartet ${WARTET_AUF[l.waiting_for] || `auf ${l.waiting_for}`}`}
            </div>
            {l.error && <div className="mt-1 text-xs text-red-300">{l.error}</div>}
            {offen === l.id && (
              <div className="mt-3 border-t border-line pt-3">
                <WorkflowInstanceView iid={l.id} projectId={l.project_id ?? null}
                                     height="260px" compact />
              </div>
            )}
          </div>
        ))}
        {laeufe?.length === 0 && (
          <div className="rounded border border-line bg-card p-3 text-sm text-muted">
            {nurHaengt ? "Nichts Auffälliges — kein Vorgang hängt." : "Zurzeit läuft kein Vorgang."}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Auslöser ─────────────────────────────────────────────────────────────────

const KIND: Record<ProcAusloeser["kind"], { label: string; cls: string }> = {
  event: { label: "Ereignis", cls: "bg-violet-500/15 text-violet-300" },
  webhook: { label: "Webhook", cls: "bg-blue-500/15 text-blue-300" },
  job: { label: "Zeitplan", cls: "bg-green-500/15 text-green-300" },
  subflow: { label: "Unterprozess", cls: "bg-surface text-muted" },
  manual: { label: "aus dem Programm", cls: "bg-surface text-muted" },
};

function Ausloeser() {
  const nav = useNavigate();
  const { data: trigger } = useQuery({ queryKey: ["proc-triggers"], queryFn: processApi.triggers });
  const { data: events } = useQuery({ queryKey: ["proc-events"], queryFn: processApi.events });

  const ohneZuhoerer = events?.filter((e) => e.listeners === 0).length ?? 0;

  return (
    <div className="space-y-5">
      <p className="text-sm text-muted">
        Was einen Ablauf in Gang setzt. Gelesen wird das aus den Graphen selbst und den Verweisen
        in Webhooks und Zeitplänen — es gibt keine zweite Liste, die damit auseinanderlaufen könnte.
      </p>

      <div className="space-y-2">
        {trigger?.map((t, i) => {
          const k = KIND[t.kind];
          return (
            <div key={`${t.definition_id}-${t.kind}-${i}`} className="rounded border border-line bg-card p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className={`rounded px-1.5 py-0.5 text-xs ${k.cls}`}>{k.label}</span>
                <span className="font-medium">{t.definition_name}</span>
                {t.project_key && (
                  <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted">{t.project_key}</span>
                )}
                {!t.enabled && (
                  <span className="rounded bg-red-500/15 px-1.5 py-0.5 text-xs text-red-300">abgeschaltet</span>
                )}
                <div className="flex-1" />
                <button
                  onClick={() => nav(t.project_key
                    ? `/projects/${t.project_key}/workflows/${t.definition_id}`
                    : `/workflows/${t.definition_id}`)}
                  className="rounded border border-line px-2 py-1 text-xs hover:border-brand"
                >
                  Ansehen
                </button>
              </div>
              <div className="mt-1 text-xs text-muted">
                {t.label}
                {t.only_project_id && " — nur für ein bestimmtes Projekt"}
              </div>
            </div>
          );
        })}
      </div>

      <div>
        <h3 className="mb-1 text-sm font-medium">Ereignisse</h3>
        <p className="mb-2 text-xs text-muted">
          Diese Ereignisse feuert Traccoon. Ein Ablauf kann sie am Start-Knoten abfangen, statt
          fest verdrahtet zu werden.
          {ohneZuhoerer === events?.length && events.length > 0 && (
            <> Derzeit hört auf <span className="text-amber-300">keines</span> davon ein Ablauf —
            alle Abläufe werden vom Programm oder von Hand gestartet.</>
          )}
        </p>
        <div className="grid gap-1.5 sm:grid-cols-2">
          {events?.map((e) => (
            <div key={e.event} className="flex items-center gap-2 rounded border border-line bg-card px-2.5 py-1.5">
              <span className="min-w-0 flex-1 truncate text-sm">{e.label}</span>
              <code className="hidden shrink-0 text-[10px] text-muted sm:block">{e.event}</code>
              <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs ${
                e.listeners ? "bg-violet-500/15 text-violet-300" : "bg-surface text-muted"
              }`}>
                {e.listeners || "—"}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
