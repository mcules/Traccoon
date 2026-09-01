import { useEffect, useState } from "react";
import { formatDate } from "../lib/formatTime";
import { tr } from "../i18n";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiError, api, processApi, workflowApi,
  type ProcTrigger, type ProcRun, type ProcSlot, type User,
} from "../api";
import { usePageChrome } from "../pageChrome";
import {
  Area, Tag, Errorrow, Listing, ListingEmpty, ListRow, Rowbutton,
} from "../components/ui";
import OwnWorkflowsPanel from "../components/workflow/OwnWorkflowsPanel";
import { projectPath } from "../projectTabs";
import StoresPanel from "../components/workflow/StoresPanel";
import LocationsPanel from "../components/workflow/PlacesPanel";
import WorkflowInstanceView from "../components/workflow/WorkflowInstanceView";
import VersionsDiff from "../components/workflow/VersionsDiff";
import { ConfirmDialog, ICON, IconButton, SortBar } from "../components/ui";
import { useListSort } from "../components/useListSort";

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
type Tab = "own" | "default" | "operations" | "triggers" | "documents" | "locations";
const TABS: [Tab, string][] = [
  ["own", "processes.own"], ["default", "processes.default_set"],
  ["operations", "processes.operations"], ["triggers", "processes.triggers"],
  ["documents", "processes.storage"],
  ["locations", "processes.locations"],
];
const TAB_KEYS = TABS.map(([k]) => k);

export default function Processes() {
  const { tab: tabParam } = useParams();
  const tab: Tab = (TAB_KEYS.includes(tabParam as Tab) ? tabParam : "own") as Tab;
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: () => api.get<User>("/auth/me") });
  usePageChrome(tr("nav.processes"), TABS.map(([key, label]) => ({
    key, label: tr(label), to: `/processes/${key}`,
    icon: { own: "✍️", default: "🔀", operations: "📡", triggers: "⚡",
            metrics: "📈", documents: "📄", locations: "📍" }[key],
  })), tab, "side");
  return (
    <div>
      {/* No personal process set at this place any more: it is a full copy of ALL slots and
          does not help exactly where one wanted to take it — event-driven flows ran twice,
          because `events.listeners` looks for triggers, not for
          sets. Whoever wants a flow differently creates one of their own. */}
      {tab === "own" && <OwnWorkflowsPanel />}
      {tab === "documents" && <StoresPanel />}
      {tab === "locations" && <LocationsPanel />}
      {tab === "default" && <StandardPreset />}
      {tab === "operations" && <Operation />}
      {tab === "triggers" && <Trigger />}
    </div>
  );
}

// ── Standard-Satz ────────────────────────────────────────────────────────────

function StandardPreset() {
  const nav = useNavigate();
  const { data: me } = useQuery({ queryKey: ["me"], queryFn: () => api.get<User>("/auth/me") });
  const admin = me?.global_role === "admin";
  const [open, setOpen] = useState<number | null>(null);

  const { data: slots } = useQuery({ queryKey: ["proc-slots"], queryFn: () => processApi.slots() });

  return (
    <Area hint={<>
      {tr("proc.shipped_default_applies_every")}{" "}
      {tr(admin ? "processes.running_instances_untouched_stay" : "processes.only_admin_can_change")}
    </>}>
      <Listing>
        {slots?.map((s) => (
          <SlotLine
            key={s.slot} s={s} admin={admin}
            open={open === s.definition_id}
            onToggle={() => setOpen(open === s.definition_id ? null : s.definition_id)}
            onEdit={() => s.definition_id && nav(`/workflows/${s.definition_id}`, { state: { from: "/processes/default" } })}
          />
        ))}
        {slots?.length === 0 && <ListingEmpty>{tr("proc.no_default_set_available")}</ListingEmpty>}
      </Listing>
    </Area>
  );
}

function SlotLine({ s, admin, open: open, onToggle, onEdit }: {
  s: ProcSlot; admin: boolean; open: boolean; onToggle: () => void; onEdit: () => void;
}) {
  const nav = useNavigate();
  return (
    <ListRow>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium text-ink">{s.name}</span>
        {s.published
          ? <Tag>v{s.version}</Tag>
          : <Tag color="yellow">{tr("proc.not_published")}</Tag>}
        {s.deviations.length > 0 && (
          <Tag color="yellow" title={tr("processes.these_projects_have_a_copy_of_their_own_and_n")}>
            {s.deviations.length} Abweichung{s.deviations.length === 1 ? "" : "en"}
          </Tag>
        )}
        <div className="flex-1" />
        <Rowbutton onClick={onToggle}>
          {open ? tr("processes.hide_versions") : tr("processes.versions")}
        </Rowbutton>
        {s.definition_id && (
          <Rowbutton onClick={onEdit}>{admin ? tr("processes.edit") : "Ansehen"}</Rowbutton>
        )}
      </div>
      <div className="mt-1 text-xs text-muted">{s.description}</div>

      {s.deviations.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
          <span>{tr("processes.own_copy")}</span>
          {s.deviations.map((a) => (
            <button
              key={a.project_id}
              onClick={() => nav(`/projects/${a.project_key}/workflows/${a.definition_id}`, { state: { from: "/processes/default" } })}
              className="rounded bg-amber-500/10 px-1.5 py-0.5 text-amber-300 hover:bg-amber-500/20"
              title={`${a.project_name} — eigene Fassung ansehen`}
            >
              {a.project_key}
            </button>
          ))}
        </div>
      )}

      {open && s.definition_id && <Versions defId={s.definition_id} mayWrite={admin} />}
    </ListRow>
  );
}

/** Version history with rolling back; the old version is published as a new one. */
function Versions({ defId, mayWrite: mayWrite }: { defId: number; mayWrite: boolean }) {
  const qc = useQueryClient();
  const [err, setErr] = useState("");
  const [diffFrom, setDiffFrom] = useState<{ id: number; version: number } | null>(null);
  const [back, setBack] = useState<{ id: number; version: number } | null>(null);
  const { data: versions } = useQuery({
    queryKey: ["wf-versions", defId], queryFn: () => workflowApi.versions(defId),
  });
  const { data: def } = useQuery({
    queryKey: ["wf-def", defId], queryFn: () => workflowApi.get(defId),
  });

  const rollback = useMutation({
    mutationFn: (vid: number) => workflowApi.rollback(defId, vid),
    onSuccess: () => {
      setErr("");
      setBack(null);
      qc.invalidateQueries({ queryKey: ["wf-versions", defId] });
      qc.invalidateQueries({ queryKey: ["wf-def", defId] });
      qc.invalidateQueries({ queryKey: ["proc-slots"] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : tr("common.error")),
  });

  return (
    <div className="mt-3 border-t border-line pt-2">
      {err && <div className="mb-2 rounded border border-red-500/40 bg-red-500/10 p-2 text-xs text-red-300">{err}</div>}
      <div className="space-y-1">
        {versions?.map((v) => {
          const current = def?.current_version_id === v.id;
          return (
            <div key={v.id} className="flex flex-wrap items-center gap-2 text-xs">
              <span className={`w-10 shrink-0 ${current ? "font-medium text-ink" : "text-muted"}`}>
                v{v.version}
              </span>
              <span className={`rounded px-1.5 py-0.5 ${
                v.status === "published" ? "bg-green-500/15 text-green-300" : "bg-surface text-muted"
              }`}>
                {tr(v.status === "published" ? "proc.published" : "proc.draft")}
              </span>
              {current && (
                <span className="rounded bg-brand/20 px-1.5 py-0.5 text-brand">aktuell</span>
              )}
              <span className="min-w-0 flex-1 truncate text-muted" title={v.notes || ""}>
                {v.notes || "—"}
              </span>
              <span className="shrink-0 text-muted">
                {formatDate(v.published_at)}
              </span>
              <IconButton icon="⇄" title={tr("proc.differences_previous_version")}
                onClick={() => setDiffFrom({ id: v.id, version: v.version })} />
              {mayWrite && !current && v.status === "published" && (
                <IconButton icon={ICON.back} title={tr("processes.put_this_version_back_in_force_as_a_new_versi")}
                  onClick={() => setBack({ id: v.id, version: v.version })} />
              )}
            </div>
          );
        })}
        {versions?.length === 0 && <div className="text-xs text-muted">{tr("processes.no_version_yet")}</div>}
      </div>

      {diffFrom && (
        <VersionsDiff defId={defId} versionId={diffFrom.id}
          title={tr("proc.what_v_version_changed", { version: diffFrom.version })}
          onClose={() => setDiffFrom(null)} />
      )}
      {back && (
        <ConfirmDialog
          title={tr("proc.roll_back")}
          text={tr("proc.put_version_v_version", { version: back.version })}
          hint={tr("proc.published_new_version_old")}
          confirmText={tr("proc.roll_back")} danger={false}
          runs={rollback.isPending}
          onClose={() => setBack(null)}
          onConfirm={() => rollback.mutate(back.id)} />
      )}
    </div>
  );
}

// ── Betrieb ──────────────────────────────────────────────────────────────────

type TagColor = "neutral" | "green" | "yellow" | "red" | "blue" | "violet" | "brand";
const STATUS_COLOR: Record<ProcRun["status"], TagColor> = {
  running: "blue", waiting: "neutral", failed: "red", completed: "green", cancelled: "neutral",
};
const STATUS_TEXT: Record<ProcRun["status"], string> = {
  running: "proc.running", waiting: "proc.waiting", failed: "proc.failed",
  completed: "fertig", cancelled: "abgebrochen",
};
const WAITS_ON: Record<string, string> = {
  human_task: "proc.person", approval: "proc.approval", agent: "proc.agent",
  timer: "proc.point_time", event: "proc.event", gate: "proc.free_time_window",
  subflow: "proc.subprocess",
};

/** What a run answers by. `state` groups what one looks for: stuck first, then running. */
const RUN_ORDER: Record<ProcRun["status"], number> = {
  failed: 0, waiting: 1, running: 2, completed: 3, cancelled: 4 };
const RUN_SORTABLE = {
  flow: (l: ProcRun) => l.definition_name,
  state: (l: ProcRun) => (l.hangs ? -1 : RUN_ORDER[l.status] ?? 9),
  age: (l: ProcRun) => l.hours,
};

function Operation() {
  const qc = useQueryClient();
  const nav = useNavigate();
  const [onlyHangs, setOnlyHangs] = useState(false);
  const [withFinish, setWithFinish] = useState(false);
  const [err, setErr] = useState("");
  // Expanded run: graph plus log. A process that is stuck or has failed always raises the
  // same question: what came back, and why did it then continue there?
  //
  // `?run=31` opens exactly that one right away: the start page points here from a standing
  // flow, and whoever arrives from there was pointing at a run a moment ago — having to look
  // for it again in a list of thirty is the opposite of a way there.
  const [params] = useSearchParams();
  const pointed = Number(params.get("run")) || null;
  const [open, setOpen] = useState<number | null>(pointed);

  const sort = useListSort<ProcRun>("processes.operations", { by: "age", dir: "desc" },
                                    RUN_SORTABLE);

  const { data: raw } = useQuery({
    queryKey: ["proc-running", onlyHangs, withFinish],
    queryFn: () => processApi.running({ onlyStuck: onlyHangs, includeDone: withFinish }),
    refetchInterval: 20000,
  });

  const oops = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.error"));
  const done = () => {
    setErr("");
    qc.invalidateQueries({ queryKey: ["proc-running"] });
    qc.invalidateQueries({ queryKey: ["proc-stuck"] });
  };
  const cancel = useMutation({
    mutationFn: (iid: number) => workflowApi.cancel(iid), onSuccess: done, onError: oops,
  });
  const again = useMutation({
    mutationFn: (iid: number) => workflowApi.restart(iid), onSuccess: done, onError: oops,
  });
  const drop = useMutation({
    mutationFn: (iid: number) => workflowApi.remove(iid),
    onSuccess: () => { done(); setOpen(null); }, onError: oops,
  });

  const runs = sort.sorted(raw);
  // A run that is pointed at but has already been dealt with (finished, cancelled) is not in
  // the open list — then the list shows the finished ones too instead of staying empty on the
  // very thing one came for.
  useEffect(() => {
    if (pointed && raw && !raw.some((l) => l.id === pointed) && !withFinish) setWithFinish(true);
  }, [pointed, raw, withFinish]);
  const hang = runs.filter((l) => l.hangs).length;

  return (
    <Area
      hint={tr("proc.all_open_runs_across")}
      tools={<>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={onlyHangs} onChange={(e) => setOnlyHangs(e.target.checked)} />
          {tr("proc.only_what_stands")}{hang > 0 && !onlyHangs ? ` (${hang})` : ""}
        </label>
        <label className="flex items-center gap-1.5">
          <input type="checkbox" checked={withFinish} onChange={(e) => setWithFinish(e.target.checked)} />
          Abgeschlossene mitzeigen
        </label>
        <div className="flex-1" />
        <SortBar by={sort.by} dir={sort.dir} onSort={sort.toggle}
          fields={[{ key: "age", label: tr("sort.age") }, { key: "state", label: tr("sort.state") },
                   { key: "flow", label: tr("sort.flow") }]} />
        <span className="text-xs text-muted">
          {runs.length} {tr(runs.length === 1 ? "proc.run" : "proc.runs")}
        </span>
      </>}
    >
      <Errorrow text={err} />

      <Listing>
        {runs.map((l) => (
          <ListRow key={l.id}>
            <div className="flex flex-wrap items-center gap-2">
              <Tag color={STATUS_COLOR[l.status]}>{tr(STATUS_TEXT[l.status])}</Tag>
              <span className="font-medium text-ink">{l.definition_name}</span>
              {l.hangs && <Tag color="yellow" title={tr("processes.standing_unusually_long")}>{tr("processes.hangs")}</Tag>}
              {l.project_key && <Tag>{l.project_key}</Tag>}
              {l.subject_ref && (
                <button
                  onClick={() => nav(l.subject_ref?.startsWith("HW-")
                    ? projectPath(l.project_key!, "operations", "hardware")
                    : `/projects/${l.project_key}/tickets/${l.subject_ref}`)}
                  className="rounded bg-surface px-1.5 py-0.5 text-xs text-ink hover:text-brand"
                >
                  {l.subject_ref}
                </button>
              )}
              <div className="flex-1" />
              {l.hours != null && (
                <span className={`text-xs ${l.hangs ? "text-amber-300" : "text-muted"}`}>
                  {l.hours < 48 ? `${l.hours} h` : `${Math.round(l.hours / 24)} Tage`}
                </span>
              )}
              <Rowbutton
                onClick={() => setOpen(open === l.id ? null : l.id)}
                title={tr("processes.history_run")}
              >
                {tr(open === l.id ? "processes.close_history" : "processes.history")}
              </Rowbutton>
              {(l.status === "running" || l.status === "waiting") && (
                <Rowbutton danger onClick={() => cancel.mutate(l.id)}
                  title={tr("processes.cancel_run")}>
                  {tr("processes.cancel")}
                </Rowbutton>
              )}
              {/* A run that has ended has two handles the same as on the start page. Until
                  now the only thing one could do with a failure was to cancel it — which it
                  already was — so six identical ones stood in this list and stayed. */}
              {(l.status === "failed" || l.status === "cancelled") && (
                <>
                  <Rowbutton onClick={() => again.mutate(l.id)} title={tr("ops.restart")}>
                    {tr("ops.restart")}
                  </Rowbutton>
                  <Rowbutton danger onClick={() => drop.mutate(l.id)} title={tr("ops.delete_run")}>
                    {tr("common.delete")}
                  </Rowbutton>
                </>
              )}
            </div>
            <div className="mt-1 text-xs text-muted">
              {tr("processes.stands")} <span className="text-ink">{l.node_label || "—"}</span>
              {l.waiting_for && ` — ${tr("proc.waiting_2")} ${WAITS_ON[l.waiting_for] ? tr(WAITS_ON[l.waiting_for]) : l.waiting_for}`}
            </div>
            {l.error && <div className="mt-1 text-xs text-red-300">{l.error}</div>}
            {open === l.id && (
              <div className="mt-3 border-t border-line pt-3">
                <WorkflowInstanceView iid={l.id} projectId={l.project_id ?? null}
                                     height="260px" compact />
              </div>
            )}
          </ListRow>
        ))}
        {runs.length === 0 && (
          <ListingEmpty>{onlyHangs ? tr("proc.nothing_unusual_no_run") : tr("proc.no_run_going_right")}</ListingEmpty>
        )}
      </Listing>
    </Area>
  );
}

// ── Triggers ─────────────────────────────────────────────────────────────────

const KIND: Record<ProcTrigger["kind"], { label: string; color: TagColor }> = {
  event: { label: "processes.event", color: "violet" },
  webhook: { label: "processes.webhook", color: "blue" },
  job: { label: "processes.schedule", color: "green" },
  subflow: { label: "processes.subprocess", color: "neutral" },
  manual: { label: "processes.program", color: "neutral" },
};

/** What a trigger answers by. `state` puts what is switched off at the end. */
const TRIGGER_SORTABLE = {
  flow: (t: ProcTrigger) => t.definition_name,
  kind: (t: ProcTrigger) => t.kind,
  state: (t: ProcTrigger) => (t.enabled ? 0 : 1),
};

function Trigger() {
  const nav = useNavigate();
  const sort = useListSort<ProcTrigger>("processes.triggers", { by: "flow", dir: "asc" },
                                        TRIGGER_SORTABLE);
  const { data: raw } = useQuery({ queryKey: ["proc-triggers"], queryFn: processApi.triggers });
  const trigger = sort.sorted(raw);
  const { data: events } = useQuery({ queryKey: ["proc-events"], queryFn: processApi.events });

  const withoutListener = events?.filter((e) => e.listeners === 0).length ?? 0;

  return (
    <div className="space-y-4">
      <Area hint={tr("proc.what_sets_flow_going")}
        tools={trigger.length > 1 ? <>
          <div className="flex-1" />
          <SortBar by={sort.by} dir={sort.dir} onSort={sort.toggle}
            fields={[{ key: "flow", label: tr("sort.flow") }, { key: "kind", label: tr("sort.kind") },
                     { key: "state", label: tr("sort.state") }]} />
        </> : undefined}>
        <Listing>
          {trigger.map((t, i) => {
            const k = KIND[t.kind];
            return (
              <ListRow key={`${t.definition_id}-${t.kind}-${i}`} dimmed={!t.enabled}>
                <div className="flex flex-wrap items-center gap-2">
                  <Tag color={k.color}>{tr(k.label)}</Tag>
                  <span className="font-medium text-ink">{t.definition_name}</span>
                  {t.project_key && <Tag>{t.project_key}</Tag>}
                  {!t.enabled && <Tag color="red">abgeschaltet</Tag>}
                  <div className="flex-1" />
                  <Rowbutton onClick={() => nav(t.project_key
                    ? `/projects/${t.project_key}/workflows/${t.definition_id}`
                    : `/workflows/${t.definition_id}`)}>
                    Ansehen
                  </Rowbutton>
                </div>
                <div className="mt-1 text-xs text-muted">
                  {t.label}
                  {t.only_project_id && ` ${tr("proc.one_specific_project_only")}`}
                </div>
              </ListRow>
            );
          })}
          {trigger.length === 0 && <ListingEmpty>{tr("processes.no_version_yet")}</ListingEmpty>}
        </Listing>
      </Area>

      <Area hint={<>
        <span className="font-medium text-ink">{tr("processes.events")}</span>{" — "}
        {tr("processes.these_events_happen_during")}
        {withoutListener === events?.length && events.length > 0 && (
          <> {tr("processes.right_now_no_flow")}</>
        )}
      </>}>
        <Listing>
          {events?.map((e) => (
            <ListRow key={e.event}>
              <div className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate">{e.label}</span>
                <code className="hidden shrink-0 text-[11px] text-muted sm:block">{e.event}</code>
                <Tag color={e.listeners ? "violet" : "neutral"}
                  title={e.listeners ? tr("processes.this_many_listen") : tr("processes.nobody_listens")}>
                  {e.listeners || "—"}
                </Tag>
              </div>
            </ListRow>
          ))}
        </Listing>
      </Area>
    </div>
  );
}
