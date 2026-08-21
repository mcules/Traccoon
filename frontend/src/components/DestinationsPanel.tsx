import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, destinationApi, type Destination, type DestinationScope } from "../api";
import { KeyValueEditor } from "./workflow/kv";
import {
  Actions, Area, Dialog, DialogFoot, INPUT_VALUE, Field, Errorrow, ICON, IconButton, Listing,
  ListingEmpty, ListenLine, DeleteDialog, BUTTON } from "./ui";
import { useAuth } from "../auth";

// Keys instead of texts: the list comes into being while the module loads, and a tr() here
// would fix the language of the first call.
const AUTH: [string, string][] = [
  ["none", "destinations_panel.auth_none"],
  ["basic", "destinations_panel.auth_basic"],
  ["bearer", "destinations_panel.auth_bearer"],
  ["api_key", "destinations_panel.auth_api_key"],
  ["hmac", "destinations_panel.auth_hmac"],
  ["oauth2_cc", "destinations_panel.auth_oauth2_cc"],
];

/** Which secret the respective method needs (the field name for the API plus the label). */
const SECRET_FIELD: Record<string, [string, string]> = {
  basic: ["password", "destinations_panel.geheimnis_passwort"],
  bearer: ["token", "destinations_panel.geheimnis_token"],
  api_key: ["api_key", "destinations_panel.geheimnis_api_key"],
  hmac: ["hmac_secret", "destinations_panel.geheimnis_hmac"],
  oauth2_cc: ["client_secret", "destinations_panel.geheimnis_client_secret"],
};

const EMPTY = {
  name: "", label: "", base_url: "", auth_type: "none", username: "",
  api_key_name: "X-API-Key", api_key_in: "header",
  hmac_header: "X-Webhook-Signature", hmac_algo: "sha256", hmac_prefix: "",
  oauth_token_url: "", oauth_client_id: "", oauth_scope: "", oauth_audience: "",
  timeout_sec: 30, verify_tls: true, allow_agents: false, max_response_chars: 4000, secret: "",
};

/**
 * Destinations are external counterparts with a stored login (like destinations in the BTP).
 * Processes, jobs and agents later name only the name; the base URL and the credentials
 * stand exactly here.
 *
 * The form used to stand permanently open under the list, and editing an entry filled it
 * from up there: one clicked a row at the top and the fields changed at the bottom, out of
 * sight. It is a dialog now, and the row carries only what it is: name, address, state and
 * three actions.
 */
export default function DestinationsPanel({
  scope,
  projectId,
  userId,
}: {
  scope: DestinationScope;
  projectId?: number;
  userId?: number;
}) {
  const qc = useQueryClient();
  const [dialog, setDialog] = useState<Destination | {} | null>(null);   // {} = neues Ziel
  const [deleteTarget, setDeleteTarget] = useState<Destination | null>(null);
  const [err, setErr] = useState("");
  const [probe, setProbe] = useState<Record<number, string>>({});

  const key = ["destinations", scope, projectId ?? null];
  const { data: all } = useQuery({
    queryKey: key,
    queryFn: () => destinationApi.list(projectId),
  });
  const targets = all?.filter((d) => d.scope === scope
    && (scope !== "project" || d.project_id === projectId)
    && (scope !== "user" || d.user_id === userId));

  const inv = () => qc.invalidateQueries({ queryKey: ["destinations"] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.fehler"));

  const save = useMutation({
    mutationFn: ({ id, body }: { id: number | null; body: Record<string, any> }) =>
      id ? destinationApi.update(id, body)
         : destinationApi.create({
             ...body,
             user_id: scope === "user" ? userId : null,
             project_id: scope === "project" ? projectId : null,
           }),
    onSuccess: () => { setDialog(null); setErr(""); inv(); },
    onError: fail,
  });
  const remove = useMutation({
    mutationFn: (id: number) => destinationApi.del(id),
    onSuccess: () => { setDeleteTarget(null); inv(); }, onError: fail,
  });
  const testing = useMutation({
    mutationFn: (id: number) => destinationApi.test(id, { method: "GET", path: "" }),
    onSuccess: (r, id) =>
      setProbe((p) => ({ ...p, [id]: `HTTP ${r.status_code}${r.ok ? " ✓" : " ✗"}` })),
    onError: (e, id) =>
      setProbe((p) => ({ ...p, [id]: e instanceof ApiError ? e.message : tr("common.fehler") })),
  });

  return (
    <Area hint={<>
      {tr("destinations_panel.einleitung")}
      {scope === "user" && ` ${tr("destinations_panel.einleitung_user")}`}
      {scope === "project" && ` ${tr("destinations_panel.einleitung_projekt")}`}
    </>}>
      <Errorrow text={err} />

      <Listing>
        {targets?.map((d) => (
          <ListenLine key={d.id} dimmed={!d.enabled}>
            <div className="flex items-center gap-2">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-xs text-muted">{d.name}</span>
                  <span className="font-medium">{d.label || d.base_url}</span>
                  <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted">
                    {AUTH.find(([k]) => k === d.auth_type)?.[1] ? tr(AUTH.find(([k]) => k === d.auth_type)![1]) : d.auth_type}
                  </span>
                  {d.has_secret && <span className="text-xs text-green-400" title={tr("destinations_panel.geheimnis_hinterlegt")}>🔑</span>}
                  {d.allow_agents && (
                    <span className="rounded bg-purple-500/15 px-1.5 py-0.5 text-xs text-purple-300">
                      {tr("destinations_panel.fuer_agenten_frei")}
                    </span>
                  )}
                  {probe[d.id] && <span className="text-xs text-muted">{probe[d.id]}</span>}
                </div>
                <div className="mt-0.5 truncate text-xs text-muted">{d.base_url}</div>
              </div>
              <Actions>
                <IconButton icon={ICON.testing} title={tr("destinations_panel.probeaufruf_get_auf_die_basis_url")}
                  onClick={() => testing.mutate(d.id)} disabled={testing.isPending} />
                <IconButton icon={ICON.edit} title={tr("common.bearbeiten")} onClick={() => setDialog(d)} />
                <IconButton icon={ICON.remove} title={tr("common.loeschen")} danger onClick={() => setDeleteTarget(d)} />
              </Actions>
            </div>
          </ListenLine>
        ))}
        {targets?.length === 0 && <ListingEmpty>{tr("destinations_panel.noch_keine_ziele")}</ListingEmpty>}
      </Listing>

      <button onClick={() => { setErr(""); setDialog({}); }}
        className={BUTTON.primary}>
        {ICON.fresh} {tr("destinations_panel.neues_ziel")}
      </button>

      {dialog && (
        <TargetDialog target={"id" in dialog ? (dialog as Destination) : null}
          error={err}
          runs={save.isPending}
          onClose={() => { setDialog(null); setErr(""); }}
          onSave={(body, id) => save.mutate({ id, body })} />
      )}
      {deleteTarget && (
        <DeleteDialog was={deleteTarget.name} hint={tr("destinations_panel.loeschen_hinweis")}
          runs={remove.isPending}
          onClose={() => setDeleteTarget(null)} onDelete={() => remove.mutate(deleteTarget.id)} />
      )}
    </Area>
  );
}

/**
 * Create and edit in one form.
 *
 * The name stays fixed once it exists: flows, jobs and agents address the destination by
 * exactly that name, and renaming it here would break them silently.
 */
function TargetDialog({ target: target, error: error, runs: running, onClose, onSave }: {
  target: Destination | null;
  error: string;
  runs: boolean;
  onClose: () => void;
  onSave: (body: Record<string, any>, id: number | null) => void;
}) {
  const [f, setF] = useState<Record<string, any>>(target ? { ...EMPTY, ...target, secret: "" } : EMPTY);
  const [header, setHeader] = useState<Record<string, any>>(target?.default_headers || {});
  const [secretField, secretLabel] = SECRET_FIELD[f.auth_type] || ["", ""];
  const can = !!f.name.trim() && !!f.base_url.trim();

  const save = () => {
    const body: Record<string, any> = { ...f, default_headers: header };
    delete body.secret;
    if (secretField && f.secret) body[secretField] = f.secret;
    // The name belongs to the entry, not to the change: an update must not carry it.
    if (target) delete body.name;
    onSave(body, target ? target.id : null);
  };

  return (
    <Dialog wide title={target ? tr("destinations_panel.ziel_bearbeiten") : tr("destinations_panel.neues_ziel")}
      onClose={onClose}
      foot={<DialogFoot onCancel={onClose} onSave={save} disabled={!can} runs={running}
        saveText={target ? undefined : tr("common.anlegen")} />}>
      <Errorrow text={error} />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label={tr("destinations_panel.name_z_b_crm")} hint={target ? tr("destinations_panel.name_fest") : undefined}>
          <input value={f.name} disabled={!!target} autoFocus={!target}
            onChange={(e) => setF({ ...f, name: e.target.value })}
            className={`${INPUT_VALUE} font-mono disabled:opacity-60`} />
        </Field>
        <Field label={tr("destinations_panel.bezeichnung")}>
          <input value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} className={INPUT_VALUE} />
        </Field>
        <div className="sm:col-span-2">
          <Field label={tr("destinations_panel.basis_url_https_api_example_com_v1")}>
            <input value={f.base_url} autoFocus={!!target}
              onChange={(e) => setF({ ...f, base_url: e.target.value })} className={INPUT_VALUE} />
          </Field>
        </div>
        <Field label={tr("destinations_panel.anmeldung")}>
          <select value={f.auth_type} onChange={(e) => setF({ ...f, auth_type: e.target.value })} className={INPUT_VALUE}>
            {AUTH.map(([k, l]) => <option key={k} value={k}>{tr(l)}</option>)}
          </select>
        </Field>
        <Field label={tr("destinations_panel.zeitlimit_s")}>
          <input type="number" min={1} max={600} value={f.timeout_sec}
            onChange={(e) => setF({ ...f, timeout_sec: Number(e.target.value) })} className={INPUT_VALUE} />
        </Field>
        <Field label={tr("destinations_panel.antwort_max_zeichen")}
          hint={tr("destinations_panel.wie_viel_der_antwort_der_aufrufer_hoechs")}>
          <input type="number" min={500} max={60000} step={500} value={f.max_response_chars}
            onChange={(e) => setF({ ...f, max_response_chars: Number(e.target.value) })} className={INPUT_VALUE} />
        </Field>

        {f.auth_type === "basic" && (
          <Field label={tr("destinations_panel.benutzername")}>
            <input value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} className={INPUT_VALUE} />
          </Field>
        )}
        {f.auth_type === "api_key" && (
          <>
            <Field label={tr("destinations_panel.name_des_schluessels")}>
              <input value={f.api_key_name} onChange={(e) => setF({ ...f, api_key_name: e.target.value })} className={INPUT_VALUE} />
            </Field>
            <Field label={tr("destinations_panel.wohin")}>
              <select value={f.api_key_in} onChange={(e) => setF({ ...f, api_key_in: e.target.value })} className={INPUT_VALUE}>
                <option value="header">{tr("destinations_panel.im_kopf")}</option>
                <option value="query">{tr("destinations_panel.in_der_url")}</option>
              </select>
            </Field>
          </>
        )}
        {f.auth_type === "hmac" && (
          <>
            <Field label={tr("destinations_panel.signatur_kopfzeile")}>
              <input value={f.hmac_header} onChange={(e) => setF({ ...f, hmac_header: e.target.value })} className={INPUT_VALUE} />
            </Field>
            <Field label={tr("destinations_panel.praefix_leer_lassen_z_b_hermes")}>
              <input value={f.hmac_prefix} onChange={(e) => setF({ ...f, hmac_prefix: e.target.value })} className={INPUT_VALUE} />
            </Field>
          </>
        )}
        {f.auth_type === "oauth2_cc" && (
          <>
            <div className="sm:col-span-2">
              <Field label={tr("destinations_panel.token_url")}>
                <input value={f.oauth_token_url} onChange={(e) => setF({ ...f, oauth_token_url: e.target.value })} className={INPUT_VALUE} />
              </Field>
            </div>
            <Field label={tr("destinations_panel.client_id")}>
              <input value={f.oauth_client_id} onChange={(e) => setF({ ...f, oauth_client_id: e.target.value })} className={INPUT_VALUE} />
            </Field>
            <Field label={tr("destinations_panel.scope_optional")}>
              <input value={f.oauth_scope} onChange={(e) => setF({ ...f, oauth_scope: e.target.value })} className={INPUT_VALUE} />
            </Field>
          </>
        )}
        {secretField && (
          <div className="sm:col-span-2">
            <Field label={tr(secretLabel)}
              hint={target ? tr("destinations_panel.geheimnis_unveraendert", { field: tr(secretLabel) }) : undefined}>
              <input type="password" value={f.secret}
                onChange={(e) => setF({ ...f, secret: e.target.value })} className={INPUT_VALUE} />
            </Field>
          </div>
        )}

        <div className="sm:col-span-2">
          <div className="mb-1 text-xs font-medium text-muted">{tr("destinations_panel.feste_kopfzeilen_bei_jedem_aufruf")}</div>
          <KeyValueEditor value={header} onChange={setHeader} />
        </div>

        <label className="flex items-center gap-2 text-sm text-ink">
          <input type="checkbox" checked={!!f.verify_tls}
            onChange={(e) => setF({ ...f, verify_tls: e.target.checked })} />
          {tr("destinations_panel.tls_pruefen")}
        </label>
        <label className="flex items-center gap-2 text-sm text-ink">
          <input type="checkbox" checked={!!f.allow_agents}
            onChange={(e) => setF({ ...f, allow_agents: e.target.checked })} />
          {tr("destinations_panel.fuer_agenten_freigeben")}
          <span className="text-xs text-muted">{tr("destinations_panel.sonst_nur_prozesse_und_jobs")}</span>
        </label>
      </div>
    </Dialog>
  );
}

/**
 * The same destinations, switched by scope instead of by menu entry.
 *
 * Destinations exist three times over (global, personal, per project) and therefore stood
 * at three places in the menu: under the settings, in the administration and in the project
 * settings, each time the same panel. One entry now, and the scope is a switch above the
 * list, showing only what this person may see.
 */
export function DestinationsArea({ projectId }: { projectId?: number }) {
  const { user } = useAuth();
  const isAdmin = user?.global_role === "admin";
  const areas: [DestinationScope, string][] = [
    ...(projectId ? ([["project", tr("destinations_panel.bereich_projekt")]] as [DestinationScope, string][]) : []),
    ["user", tr("destinations_panel.bereich_ich")],
    ...(isAdmin ? ([["global", tr("destinations_panel.bereich_global")]] as [DestinationScope, string][]) : []),
  ];
  const [scope, setScope] = useState<DestinationScope>(areas[0][0]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5">
        {areas.map(([k, label]) => (
          <button key={k} onClick={() => setScope(k)}
            className={`rounded-md border px-3 py-1 text-sm ${
              scope === k ? "border-brand bg-brand text-white"
                          : "border-line text-muted hover:bg-surface hover:text-ink"}`}>
            {label}
          </button>
        ))}
      </div>
      <DestinationsPanel scope={scope} projectId={projectId} userId={user?.id} />
    </div>
  );
}
