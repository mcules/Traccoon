import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { tr } from "../../../i18n";
import { api, workflowApi } from "../../../api";
import type { NodeConfig } from "../types";

interface ProjectLite { id: number; key: string; name: string }

/**
 * Trigger of a flow.
 *
 * Two sources, both configurable here:
 *
 * **Event**: Traccoon reports something ("ticket created", "mail received"), and the flow
 * decides itself whether it listens for it. That way any number of flows hang off one event
 * without the trigger having to know them.
 *
 * **Webhook**: for everything that comes from outside and knows neither MCP nor Traccoon's
 * events: the flow gets an address of its own. That did exist before (Settings →
 * Webhooks, mode `workflow`), but at the other end: in the flow itself its source was
 * invisible. The example payload beside it is more than documentation: the context fields
 * the branches offer for selection come out of it.
 */
export default function StartConfig({
  config,
  onChange,
  defId,
  subjectKind,
}: {
  config: NodeConfig;
  onChange: (c: NodeConfig) => void;
  /** Definition this start node belongs to, for the incoming address. */
  defId?: number;
  /** Subject of the flow; determines whether an artifact has to be named. */
  subjectKind?: string;
}) {
  const qc = useQueryClient();
  const { data: hook } = useQuery({
    queryKey: ["workflow-webhook", defId],
    queryFn: () => workflowApi.webhookGet(defId as number),
    enabled: !!defId,
  });
  const addressCreate = useMutation({
    mutationFn: () => workflowApi.webhookCreate(defId as number),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workflow-webhook", defId] }),
  });
  const { data: events } = useQuery({
    queryKey: ["workflow-events"],
    queryFn: () => api.get<{ event: string; label: string }[]>("/workflow-events"),
    staleTime: 10 * 60_000,
  });
  const { data: projects } = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.get<ProjectLite[]>("/projects"),
    staleTime: 5 * 60_000,
  });

  const t: Record<string, any> = config.trigger || {};
  // Three ways to start a flow. They exclude each other: an incoming address on an event
  // trigger would be a second door nobody uses, and it would be in the way when one only
  // wants to see what the flow listens for.
  // The kind stands explicitly in the configuration instead of being guessed from the
  // content: otherwise "event" would fall back to "by hand" immediately on switching as
  // long as no event name is entered yet, so one chooses something and apparently nothing
  // happens. (`t.event` still counts as an event, which is the existing data.)
  const art: "manuell" | "ereignis" | "webhook" | "mail_action" =
    t.kind === "webhook" ? "webhook"
      : t.kind === "mail_action" ? "mail_action"
        : (t.kind === "ereignis" || t.event) ? "ereignis" : "manuell";
  const setArt = (fresh: typeof art) => {
    const remainder = { ...t };
    delete remainder.event; delete remainder.project_id; delete remainder.filter; delete remainder.kind;
    delete remainder.scope;
    if (fresh !== "manuell") remainder.kind = fresh;
    if (fresh === "mail_action") remainder.scope = "message";
    onChange({
      ...config,
      trigger: Object.keys(remainder).length ? (remainder as NodeConfig["trigger"]) : undefined,
    });
  };
  const setT = (next: Record<string, any>) => {
    const together: Record<string, any> = { ...t, ...next };
    // Do not carry empty entries along: a trigger without an event is none.
    for (const k of Object.keys(together)) {
      if (together[k] === "" || together[k] === undefined || together[k] === null) {
        delete together[k];
      }
    }
    // A trigger without an event is not an event trigger, but the example payload of a
    // webhook lives here as well and must not disappear with it.
    const empty = !together.event && !together.sample;
    onChange({
      ...config,
      trigger: empty ? undefined : (together as NodeConfig["trigger"]),
    });
  };
  const inp = "w-full rounded border border-line bg-surface px-2 py-1 text-sm text-ink";

  return (
    <div className="space-y-3">
      <label className="block text-xs font-medium text-muted">
        {tr("start_config.what_starts_flow")}
        <select value={art} onChange={(e) => setArt(e.target.value as typeof art)}
          className={`mt-1 ${inp}`}>
          <option value="manuell">{tr("start_config.hand_through_job")}</option>
          <option value="ereignis">{tr("start_config.event_inside_traccoon")}</option>
          <option value="webhook">{tr("start_config.call_from_outside_webhook")}</option>
          <option value="mail_action">Knopf an einer Mail</option>
        </select>
      </label>

      {art === "mail_action" && (
        <div className="space-y-2">
          <label className="block text-xs font-medium text-muted">
            Woran der Knopf haengt
            <select value={t.scope || "message"} onChange={(e) => setT({ scope: e.target.value })}
              className={`mt-1 ${inp}`}>
              <option value="message">an der Nachricht</option>
              <option value="attachment">{tr("start_config.on_every_attachment")}</option>
            </select>
          </label>
          {/* Without this line one would have to guess what stands in the context — and
              guessing here means writing placeholders that stay quietly empty. */}
          <p className="text-[11px] text-muted">
            {tr("start_config.mail_context_1")} <code>mail.account</code>, <code>mail.folder</code>,{" "}
            <code>mail.uid</code>, <code>mail.subject</code>, <code>mail.from</code>,{" "}
            <code>mail.text</code>, <code>mail.attachments</code> {tr("start_config.mail_context_2")}{" "}
            <code>attachment.index</code>, <code>attachment.filename</code>,{" "}
            <code>attachment.content_type</code>.
          </p>
        </div>
      )}

      {art === "ereignis" && (
      <label className="block text-xs font-medium text-muted">
        {tr("start_config.trigger")}
        <input
          list="ereignisse"
          value={t.event || ""}
          onChange={(e) => setT({ event: e.target.value.trim() })}
          placeholder={tr("start_config.no_trigger_manual_start_only")}
          className={`mt-1 ${inp}`}
        />
        <datalist id="ereignisse">
          {events?.map((e) => (
            <option key={e.event} value={e.event}>{e.label}</option>
          ))}
        </datalist>
        <span className="mt-1 block text-[11px] text-muted">{tr("start_config.own_names_allowed_webhook")}</span>
      </label>
      )}

      {/* ── Webhook als Quelle ─────────────────────────────────────────── */}
      {art !== "ereignis" && (
      <div className="rounded border border-line bg-surface p-2">
        <div className="mb-1 text-xs font-medium text-muted">{tr("start_config.incoming_address_webhook")}</div>
        {hook ? (
          <div className="space-y-1">
            <div className="break-all font-mono text-[11px] text-ink">{hook.url}</div>
            <div className="text-[11px] text-muted">
              {tr("start_config.signature_x_webhook_signature")}{" "}
              <code className="break-all rounded bg-card px-1">{hook.secret}</code>
            </div>
            <div className="text-[11px] text-muted">{tr("start_config.whole_payload_becomes_context")}</div>
          </div>
        ) : (
          <button
            onClick={() => addressCreate.mutate()}
            disabled={!defId || addressCreate.isPending}
            className="rounded border border-line px-2 py-1 text-xs text-muted hover:text-ink disabled:opacity-50"
          >
            {addressCreate.isPending ? "…" : tr("start_config.create_address")}
          </button>
        )}

        {subjectKind && subjectKind !== "standalone" && (
          <label className="mt-2 block text-[11px] text-muted">
            {tr("start_config.artifact_sits_field_payload")}
            <input
              value={t.subject_field || ""}
              onChange={(e) => setT({ subject_field: e.target.value.trim() })}
              placeholder={subjectKind === "issue" ? "vorgang.ticket" : "geraet.id"}
              className={`mt-1 font-mono ${inp}`}
            />
            <span className="mt-1 block">
              {tr(subjectKind === "issue"
                ? "start_config.ticket_key_tra_31" : "start_config.number_item")}{" "}
              {tr("start_config.without_flow_does_not")}
            </span>
          </label>
        )}
      </div>
      )}

      {art !== "ereignis" && (
      <label className="block text-xs font-medium text-muted">
        Beispiel-Nutzlast (JSON)
        <textarea
          rows={4}
          value={t.sample ? JSON.stringify(t.sample, null, 1) : ""}
          onChange={(e) => {
            const raw = e.target.value.trim();
            if (!raw) return setT({ sample: "" });
            try { setT({ sample: JSON.parse(raw) }); } catch { /* Tippen abwarten */ }
          }}
          placeholder={'{"case": {"id": 42, "title": "Fault"}, "source": "Zabbix"}'}
          className={`mt-1 ${inp} font-mono`}
        />
        <span className="mt-1 block text-[11px] text-muted">
          {tr("start_config.paste_once_what_other")}
        </span>
      </label>
      )}

      {t.event && (
        <>
          <label className="block text-xs font-medium text-muted">
            {tr("start_config.only_project")}
            <select
              value={t.project_id ?? ""}
              onChange={(e) => setT({ project_id: e.target.value ? Number(e.target.value) : "" })}
              className={`mt-1 ${inp}`}
            >
              <option value="">{tr("start_config.any_project")}</option>
              {projects?.map((p) => (
                <option key={p.id} value={p.id}>{p.key} · {p.name}</option>
              ))}
            </select>
            <span className="mt-1 block text-[11px] text-muted">
              {tr("start_config.flow_already_belongs_project")}
            </span>
          </label>

          <label className="block text-xs font-medium text-muted">
            {tr("start_config.only_when_condition_jsonlogic")}
            <textarea
              rows={3}
              value={t.filter ? JSON.stringify(t.filter, null, 1) : ""}
              onChange={(e) => {
                const raw = e.target.value.trim();
                if (!raw) return setT({ filter: "" });
                try { setT({ filter: JSON.parse(raw) }); } catch { /* Tippen abwarten */ }
              }}
              placeholder={'{"==": [{"var": "issue.priority"}, "highest"]}'}
              className={`mt-1 ${inp} font-mono`}
            />
            <span className="mt-1 block text-[11px] text-muted">
              {tr("start_config.empty_means_always_content")}
            </span>
          </label>
        </>
      )}
    </div>
  );
}
