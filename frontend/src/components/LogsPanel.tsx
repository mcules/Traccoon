import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { tr } from "../i18n";
import { Area, BUTTON_SMALL, Listing, ListingEmpty, ListRow, Tag } from "./ui";

/**
 * Logs and retention, for admins.
 *
 * Two questions live on this page, and they are the same question from two ends: what
 * happened, and how long we still know about it. Until now neither could be answered
 * from the surface — the protocol tables were visible only through the project views
 * (bound to project membership, and runs without a ticket never appeared at all), and
 * the application log only over `docker logs` on the host.
 *
 * The list is deliberately one list. Splitting runs, flows, jobs and deliveries into
 * four tabs looks tidier and is useless in the moment one needs it, because a failure
 * walks across them: the job starts the flow, the flow starts the run, the run falls
 * over. Sorted by time, they read as one sequence.
 */

type Entry = {
  ts: string | null;
  source: string;
  level: "error" | "warn" | "info";
  title: string;
  detail: string;
  ref: Record<string, unknown>;
};

type Rule = {
  key: string; label: string; days: number;
  default_days: number; rows: number; pending: number;
};

const LEVEL_TAG: Record<Entry["level"], "red" | "yellow" | "neutral"> = {
  error: "red", warn: "yellow", info: "neutral",
};

/** Sources in the order in which a failure usually walks through them. */
const SOURCES = ["jobs", "workflows", "runs", "inbound"] as const;

export default function LogsPanel() {
  return (
    <div className="space-y-4">
      <LogList />
      <ContainerLog />
      <Retention />
    </div>
  );
}

function LogList() {
  const [hours, setHours] = useState(24);
  const [errors, setErrors] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [q, setQ] = useState("");
  // The typed text only becomes a query on submit: filtering per keystroke means one
  // request per letter over four tables.
  const [needle, setNeedle] = useState("");
  const [open, setOpen] = useState<number | null>(null);

  const { data, isFetching, refetch } = useQuery({
    queryKey: ["admin-logs", hours, errors, picked.join(","), needle],
    queryFn: () => api.get<{ entries: Entry[]; sources: string[] }>(
      `/admin/logs?hours=${hours}&errors=${errors}&limit=200`
      + (picked.length ? `&sources=${picked.join(",")}` : "")
      + (needle ? `&q=${encodeURIComponent(needle)}` : "")),
  });
  const entries = data?.entries ?? [];

  const toggle = (s: string) =>
    setPicked((old) => (old.includes(s) ? old.filter((x) => x !== s) : [...old, s]));

  return (
    <Area title={tr("logs.title")} hint={tr("logs.hint")}>
      <div className="flex flex-wrap items-center gap-2">
        {SOURCES.map((s) => (
          <button key={s} onClick={() => toggle(s)}
            className={`rounded border px-2 py-1 text-xs ${picked.includes(s)
              ? "border-accent text-accent" : "border-line text-muted"}`}>
            {tr(`logs.source.${s}`)}
          </button>
        ))}
        <span className="mx-1 h-4 w-px bg-line" />
        <select value={hours} onChange={(e) => setHours(Number(e.target.value))}
          className="rounded border border-line bg-surface px-2 py-1 text-xs text-ink">
          <option value={6}>{tr("logs.range.6h")}</option>
          <option value={24}>{tr("logs.range.24h")}</option>
          <option value={168}>{tr("logs.range.7d")}</option>
          <option value={720}>{tr("logs.range.30d")}</option>
        </select>
        <label className="flex items-center gap-1 text-xs text-muted">
          <input type="checkbox" checked={errors} onChange={(e) => setErrors(e.target.checked)} />
          {tr("logs.only_errors")}
        </label>
        <form className="flex items-center gap-1"
          onSubmit={(e) => { e.preventDefault(); setNeedle(q.trim()); }}>
          <input value={q} onChange={(e) => setQ(e.target.value)}
            placeholder={tr("logs.search")}
            className="w-48 rounded border border-line bg-surface px-2 py-1 text-xs text-ink" />
          <button type="submit" className={BUTTON_SMALL.secondary}>{tr("logs.apply")}</button>
        </form>
        <button onClick={() => refetch()} className={BUTTON_SMALL.secondary}>
          {isFetching ? tr("logs.loading") : tr("logs.refresh")}
        </button>
      </div>

      <div>
        <Listing>
          {entries.length === 0 && <ListingEmpty>{tr("logs.empty")}</ListingEmpty>}
          {entries.map((e, i) => (
            <ListRow key={i} onClick={() => setOpen(open === i ? null : i)}>
              <div className="flex w-full items-start gap-3">
                <span className="w-36 shrink-0 font-mono text-xs text-muted">
                  {e.ts ? e.ts.slice(0, 19).replace("T", " ") : "—"}
                </span>
                <Tag color={LEVEL_TAG[e.level]}>{tr(`logs.source.${e.source}`)}</Tag>
                <span className="min-w-0 flex-1 truncate text-sm text-ink">{e.title}</span>
                {e.detail && <span className="shrink-0 text-xs text-muted">
                  {open === i ? "▾" : "▸"}</span>}
              </div>
              {open === i && e.detail && (
                <pre className="mt-2 max-h-80 w-full overflow-auto whitespace-pre-wrap
                  rounded bg-surface p-2 font-mono text-xs text-muted">{e.detail}</pre>
              )}
            </ListRow>
          ))}
        </Listing>
      </div>
    </Area>
  );
}

/**
 * The container log. It comes from the deployer, not from the backend: the backend has
 * no docker socket and is not meant to have one, because it faces the web and the socket
 * is root on the host.
 */
function ContainerLog() {
  const [service, setService] = useState("");
  const [tail, setTail] = useState(300);
  const { data: svc, error: svcErr } = useQuery({
    queryKey: ["admin-log-containers"],
    queryFn: () => api.get<{ services: { service: string; state: string; status: string }[] }>(
      "/admin/logs/containers"),
  });
  const { data, isFetching, refetch } = useQuery({
    queryKey: ["admin-container-log", service, tail],
    queryFn: () => api.get<{ log: string; error?: string }>(
      `/admin/logs/container/${service}?tail=${tail}`),
    enabled: !!service,
  });

  return (
    <Area title={tr("logs.containers")} hint={tr("logs.containers_hint")}>
      {svcErr && <p className="text-xs text-red-400">{tr("logs.deployer_away")}</p>}

      <div className="flex flex-wrap items-center gap-2">
        <select value={service} onChange={(e) => setService(e.target.value)}
          className="rounded border border-line bg-surface px-2 py-1 text-xs text-ink">
          <option value="">{tr("logs.pick_container")}</option>
          {(svc?.services ?? []).map((s) => (
            <option key={s.service} value={s.service}>
              {s.service}{s.state === "running" ? "" : ` (${s.state})`}
            </option>
          ))}
        </select>
        <select value={tail} onChange={(e) => setTail(Number(e.target.value))}
          className="rounded border border-line bg-surface px-2 py-1 text-xs text-ink">
          {[100, 300, 1000, 5000].map((n) => (
            <option key={n} value={n}>{tr("logs.lines", { n: String(n) })}</option>
          ))}
        </select>
        <button onClick={() => refetch()} disabled={!service} className={BUTTON_SMALL.secondary}>
          {isFetching ? tr("logs.loading") : tr("logs.refresh")}
        </button>
      </div>

      {service && (
        <pre className="mt-3 max-h-[32rem] overflow-auto whitespace-pre-wrap rounded
          bg-surface p-2 font-mono text-xs text-muted">
          {data?.error ? data.error : (data?.log || tr("logs.empty"))}
        </pre>
      )}
    </Area>
  );
}

/**
 * Retention. Every protocol source has its own period, because they weigh differently:
 * inbound deliveries carry the raw body of every webhook and are kept shortest,
 * notifications longest, because an unread one is the last trace of something nobody
 * has looked at yet.
 */
function Retention() {
  const qc = useQueryClient();
  const [edited, setEdited] = useState<Record<string, number>>({});
  const [msg, setMsg] = useState("");
  const { data } = useQuery({
    queryKey: ["admin-retention"],
    queryFn: () => api.get<{ rules: Rule[] }>("/admin/retention"),
  });
  const save = useMutation({
    mutationFn: (v: { key: string; days: number }) => api.put("/admin/retention", v),
    onSuccess: () => {
      setMsg(tr("common.saved")); setTimeout(() => setMsg(""), 2000); setEdited({});
      qc.invalidateQueries({ queryKey: ["admin-retention"] });
    },
  });
  const sweep = useMutation({
    mutationFn: () => api.post("/admin/retention/sweep", {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-retention"] }),
  });

  return (
    <Area title={tr("logs.retention")}
      hint={<>{tr("logs.retention_hint")} <b>{tr("admin.0_never_delete")}</b></>}>
      <div className="space-y-2">
        {(data?.rules ?? []).map((r) => {
          const value = edited[r.key] ?? r.days;
          return (
            <div key={r.key} className="flex flex-wrap items-center gap-2">
              <span className="w-44 text-sm text-ink">{tr(`logs.rule.${r.key}`)}</span>
              <input type="number" min={0} value={value}
                onChange={(e) => setEdited({ ...edited, [r.key]: Number(e.target.value) })}
                className="w-20 rounded border border-line bg-surface px-2 py-1 text-sm text-ink" />
              <span className="text-xs text-muted">{tr("admin.days")}</span>
              <span className="text-xs text-muted">
                {tr("logs.rows", { n: String(r.rows) })}
                {r.pending > 0 && ` · ${tr("logs.pending", { n: String(r.pending) })}`}
              </span>
              {value !== r.days && (
                <button className={BUTTON_SMALL.secondary}
                  onClick={() => save.mutate({ key: r.key, days: value })}>
                  {tr("admin.save")}
                </button>
              )}
            </div>
          );
        })}
      </div>

      <div className="flex items-center gap-2">
        <button className={BUTTON_SMALL.secondary} onClick={() => sweep.mutate()}>
          {sweep.isPending ? tr("logs.sweeping") : tr("logs.sweep_now")}
        </button>
        <span className="text-xs text-muted">{tr("logs.sweep_hint")}</span>
        {msg && <span className="text-sm text-green-400">{msg}</span>}
      </div>
    </Area>
  );
}
