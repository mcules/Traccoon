import { useState } from "react";
import { formatDate } from "../lib/formatTime";
import { tr } from "../i18n";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiError, api, processApi, workflowApi,
  type ProcAusloeser, type ProcLauf, type ProcSlot, type User,
} from "../api";
import { usePageChrome } from "../pageChrome";
import {
  Bereich, Etikett, Fehlerzeile, Liste, ListeLeer, ListenZeile, Zeilenknopf,
} from "../components/ui";
import OwnWorkflowsPanel from "../components/workflow/OwnWorkflowsPanel";
import { projektPfad } from "../projectTabs";
import MessreihenPanel from "../components/workflow/MessreihenPanel";
import WorkflowInstanceView from "../components/workflow/WorkflowInstanceView";

/**
 * Process administration: the view across all flows.
 *
 * Since every flow is a graph, the knowledge about it is spread over sets, project copies,
 * versions and running processes. This page brings it together: what is the default, who
 * deviates, what is running right now, and what sets it off.
 *
 * The first tab holds the **own** flows: freely created, bound to no slot and no project.
 * They used to stand in the settings, in the wrong place, because flows are a load bearing
 * part of Traccoon beside the assistant and the projects and not a side setting.
 */
type Tab = "eigene" | "standard" | "betrieb" | "ausloeser" | "messreihen";
const TABS: [Tab, string][] = [
  ["eigene", "processes.tabs.own"], ["standard", "processes.tabs.default_set"], ["betrieb", "processes.tabs.operations"],
  ["ausloeser", "processes.tabs.triggers"], ["messreihen", "processes.tabs.series"],
];
const TAB_KEYS = TABS.map(([k]) => k);

export default function Processes() {
  const { tab: tabParam } = useParams();
  const tab: Tab = (TAB_KEYS.includes(tabParam as Tab) ? tabParam : "eigene") as Tab;
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: () => api.get<User>("/auth/me") });
  usePageChrome(tr("nav.processes"), TABS.map(([key, label]) => ({
    key, label: tr(label), to: `/processes/${key}`,
    icon: { eigene: "✍️", standard: "🔀", betrieb: "📡", ausloeser: "⚡",
            messreihen: "📈" }[key],
  })), tab, "seite");
  return (
    <div>
      {/* Kein persönlicher Prozess-Satz mehr an dieser Stelle: er ist eine Vollkopie ALLER
          Slots und hilft genau dort nicht, wo man ihn nehmen wollte — ereignisgetriebene
          Abläufe liefen doppelt, weil `events.listeners` nach Triggern sucht, nicht nach
          Sätzen. Wer einen Ablauf anders haben will, legt einen eigenen an. */}
      {tab === "eigene" && <OwnWorkflowsPanel />}
      {tab === "messreihen" && <MessreihenPanel />}
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
    <Bereich hinweis={<>
      {tr("proc.standard_hinweis")}{" "}
      {tr(admin ? "processes.hinweis_admin" : "processes.hinweis_leser")}
    </>}>
      <Liste>
        {slots?.map((s) => (
          <SlotZeile
            key={s.slot} s={s} admin={admin}
            offen={offen === s.definition_id}
            onToggle={() => setOffen(offen === s.definition_id ? null : s.definition_id)}
            onEdit={() => s.definition_id && nav(`/workflows/${s.definition_id}`, { state: { from: "/processes/standard" } })}
          />
        ))}
        {slots?.length === 0 && <ListeLeer>{tr("proc.kein_standard_satz")}</ListeLeer>}
      </Liste>
    </Bereich>
  );
}

function SlotZeile({ s, admin, offen, onToggle, onEdit }: {
  s: ProcSlot; admin: boolean; offen: boolean; onToggle: () => void; onEdit: () => void;
}) {
  const nav = useNavigate();
  return (
    <ListenZeile>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-ink">{s.name}</span>
        {s.published
          ? <Etikett>v{s.version}</Etikett>
          : <Etikett farbe="gelb">{tr("proc.nicht_veroeffentlicht")}</Etikett>}
        {s.abweichungen.length > 0 && (
          <Etikett farbe="gelb" titel={tr("processes.diese_projekte_haben_eine_eigene_kopie_u")}>
            {s.abweichungen.length} Abweichung{s.abweichungen.length === 1 ? "" : "en"}
          </Etikett>
        )}
        <div className="flex-1" />
        <Zeilenknopf onClick={onToggle}>
          {offen ? "Versionen ausblenden" : "Versionen"}
        </Zeilenknopf>
        {s.definition_id && (
          <Zeilenknopf onClick={onEdit}>{admin ? "Bearbeiten" : "Ansehen"}</Zeilenknopf>
        )}
      </div>
      <div className="mt-1 text-xs text-muted">{s.description}</div>

      {s.abweichungen.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
          <span>{tr("processes.eigene_kopie")}</span>
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
    </ListenZeile>
  );
}

/** Version history with rolling back; the old version is published as a new one. */
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
                {tr(v.status === "published" ? "proc.veroeffentlicht" : "proc.entwurf")}
              </span>
              {aktuell && (
                <span className="rounded bg-brand/20 px-1.5 py-0.5 text-brand">aktuell</span>
              )}
              <span className="min-w-0 flex-1 truncate text-muted" title={v.notes || ""}>
                {v.notes || "—"}
              </span>
              <span className="shrink-0 text-muted">
                {formatDate(v.published_at)}
              </span>
              {darfSchreiben && !aktuell && v.status === "published" && (
                <button
                  onClick={() => rollback.mutate(v.id)}
                  disabled={rollback.isPending}
                  className="shrink-0 rounded border border-line px-1.5 py-0.5 hover:border-amber-400 disabled:opacity-50"
                  title={tr("processes.diese_fassung_wieder_in_kraft_setzen_als")}
                >
                  {tr("proc.zurueckrollen")}
                </button>
              )}
            </div>
          );
        })}
        {versionen?.length === 0 && <div className="text-xs text-muted">{tr("processes.noch_keine_version")}</div>}
      </div>
    </div>
  );
}

// ── Betrieb ──────────────────────────────────────────────────────────────────

type EtikettFarbe = "neutral" | "gruen" | "gelb" | "rot" | "blau" | "violett" | "brand";
const STATUS_FARBE: Record<ProcLauf["status"], EtikettFarbe> = {
  running: "blau", waiting: "neutral", failed: "rot", completed: "gruen", cancelled: "neutral",
};
const STATUS_TEXT: Record<ProcLauf["status"], string> = {
  running: "proc.status.laeuft", waiting: "proc.status.wartet", failed: "proc.status.gescheitert",
  completed: "fertig", cancelled: "abgebrochen",
};
const WARTET_AUF: Record<string, string> = {
  human_task: "proc.wartet.person", approval: "proc.wartet.freigabe", agent: "proc.wartet.agent",
  timer: "proc.wartet.zeitpunkt", event: "proc.wartet.ereignis", gate: "proc.wartet.fenster",
  subflow: "proc.wartet.unterprozess",
};

function Betrieb() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [nurHaengt, setNurHaengt] = useState(false);
  const [mitFertigen, setMitFertigen] = useState(false);
  const [err, setErr] = useState("");
  // Expanded run: graph plus log. A process that is stuck or has failed always raises the
  // same question: what came back, and why did it then continue there?
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
    <Bereich
      hinweis={tr("proc.betrieb_hinweis")}
      werkzeuge={<>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={nurHaengt} onChange={(e) => setNurHaengt(e.target.checked)} />
          {tr("proc.nur_auffaelliges")}{haengen > 0 && !nurHaengt ? ` (${haengen})` : ""}
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={mitFertigen} onChange={(e) => setMitFertigen(e.target.checked)} />
          Abgeschlossene mitzeigen
        </label>
        <div className="flex-1" />
        <span className="text-xs text-muted">
          {laeufe?.length ?? 0} {tr(laeufe?.length === 1 ? "proc.vorgang" : "proc.vorgaenge")}
        </span>
      </>}
    >
      <Fehlerzeile text={err} />

      <Liste>
        {laeufe?.map((l) => (
          <ListenZeile key={l.id}>
            <div className="flex flex-wrap items-center gap-2">
              <Etikett farbe={STATUS_FARBE[l.status]}>{tr(STATUS_TEXT[l.status])}</Etikett>
              <span className="font-medium text-ink">{l.definition_name}</span>
              {l.haengt && <Etikett farbe="gelb" titel="Steht ungewöhnlich lange">hängt</Etikett>}
              {l.project_key && <Etikett>{l.project_key}</Etikett>}
              {l.subject_ref && (
                <button
                  onClick={() => nav(l.subject_ref?.startsWith("HW-")
                    ? projektPfad(l.project_key!, "betrieb", "hardware")
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
              <Zeilenknopf
                onClick={() => setOffen(offen === l.id ? null : l.id)}
                titel={tr("processes.verlauf_des_vorgangs")}
              >
                {tr(offen === l.id ? "processes.verlauf_zu" : "processes.verlauf")}
              </Zeilenknopf>
              {(l.status === "running" || l.status === "waiting") && (
                <Zeilenknopf gefahr onClick={() => abbrechen.mutate(l.id)}
                  titel={tr("processes.vorgang_abbrechen")}>
                  {tr("processes.abbrechen")}
                </Zeilenknopf>
              )}
            </div>
            <div className="mt-1 text-xs text-muted">
              {tr("processes.steht_bei")} <span className="text-ink">{l.node_label || "—"}</span>
              {l.waiting_for && ` — ${tr("proc.wartet")} ${WARTET_AUF[l.waiting_for] ? tr(WARTET_AUF[l.waiting_for]) : l.waiting_for}`}
            </div>
            {l.error && <div className="mt-1 text-xs text-red-300">{l.error}</div>}
            {offen === l.id && (
              <div className="mt-3 border-t border-line pt-3">
                <WorkflowInstanceView iid={l.id} projectId={l.project_id ?? null}
                                     height="260px" compact />
              </div>
            )}
          </ListenZeile>
        ))}
        {laeufe?.length === 0 && (
          <ListeLeer>{nurHaengt ? tr("proc.nichts_auffaelliges") : tr("proc.kein_vorgang")}</ListeLeer>
        )}
      </Liste>
    </Bereich>
  );
}

// ── Triggers ─────────────────────────────────────────────────────────────────

const KIND: Record<ProcAusloeser["kind"], { label: string; farbe: EtikettFarbe }> = {
  event: { label: "processes.ausloeser_event", farbe: "violett" },
  webhook: { label: "processes.ausloeser_webhook", farbe: "blau" },
  job: { label: "processes.ausloeser_job", farbe: "gruen" },
  subflow: { label: "processes.ausloeser_subflow", farbe: "neutral" },
  manual: { label: "processes.ausloeser_manual", farbe: "neutral" },
};

function Ausloeser() {
  const nav = useNavigate();
  const { data: trigger } = useQuery({ queryKey: ["proc-triggers"], queryFn: processApi.triggers });
  const { data: events } = useQuery({ queryKey: ["proc-events"], queryFn: processApi.events });

  const ohneZuhoerer = events?.filter((e) => e.listeners === 0).length ?? 0;

  return (
    <div className="space-y-4">
      <Bereich hinweis={tr("proc.ausloeser_hinweis")}>
        <Liste>
          {trigger?.map((t, i) => {
            const k = KIND[t.kind];
            return (
              <ListenZeile key={`${t.definition_id}-${t.kind}-${i}`} gedimmt={!t.enabled}>
                <div className="flex flex-wrap items-center gap-2">
                  <Etikett farbe={k.farbe}>{tr(k.label)}</Etikett>
                  <span className="font-medium text-ink">{t.definition_name}</span>
                  {t.project_key && <Etikett>{t.project_key}</Etikett>}
                  {!t.enabled && <Etikett farbe="rot">abgeschaltet</Etikett>}
                  <div className="flex-1" />
                  <Zeilenknopf onClick={() => nav(t.project_key
                    ? `/projects/${t.project_key}/workflows/${t.definition_id}`
                    : `/workflows/${t.definition_id}`)}>
                    Ansehen
                  </Zeilenknopf>
                </div>
                <div className="mt-1 text-xs text-muted">
                  {t.label}
                  {t.only_project_id && ` ${tr("proc.nur_fuer_projekt")}`}
                </div>
              </ListenZeile>
            );
          })}
          {trigger?.length === 0 && <ListeLeer>{tr("processes.noch_keine_version")}</ListeLeer>}
        </Liste>
      </Bereich>

      <Bereich hinweis={<>
        <span className="font-medium text-ink">{tr("processes.ereignisse")}</span>{" — "}
        {tr("processes.ereignisse_hinweis")}
        {ohneZuhoerer === events?.length && events.length > 0 && (
          <> {tr("processes.ereignisse_niemand_hoert")}</>
        )}
      </>}>
        <Liste>
          {events?.map((e) => (
            <ListenZeile key={e.event}>
              <div className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate">{e.label}</span>
                <code className="hidden shrink-0 text-[11px] text-muted sm:block">{e.event}</code>
                <Etikett farbe={e.listeners ? "violett" : "neutral"}
                  titel={e.listeners ? "So viele Abläufe hören darauf" : "Niemand hört darauf"}>
                  {e.listeners || "—"}
                </Etikett>
              </div>
            </ListenZeile>
          ))}
        </Liste>
      </Bereich>
    </div>
  );
}
