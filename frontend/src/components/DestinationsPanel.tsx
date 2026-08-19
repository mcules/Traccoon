import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, destinationApi, type Destination, type DestinationScope } from "../api";
import { KeyValueEditor } from "./workflow/kv";

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

const LEER = {
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
  const [f, setF] = useState<Record<string, any>>(LEER);
  const [editId, setEditId] = useState<number | null>(null);
  const [kopf, setKopf] = useState<Record<string, any>>({});
  const [err, setErr] = useState("");
  const [probe, setProbe] = useState<Record<number, string>>({});

  const schluessel = ["destinations", scope, projectId ?? null];
  const { data: alle } = useQuery({
    queryKey: schluessel,
    queryFn: () => destinationApi.list(projectId),
  });
  const ziele = alle?.filter((d) => d.scope === scope
    && (scope !== "project" || d.project_id === projectId)
    && (scope !== "user" || d.user_id === userId));

  const inv = () => qc.invalidateQueries({ queryKey: ["destinations"] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : "Fehler");
  const reset = () => { setF(LEER); setKopf({}); setEditId(null); setErr(""); };

  const speichern = useMutation({
    mutationFn: () => {
      const [feld] = SECRET_FIELD[f.auth_type] || [];
      const body: Record<string, any> = { ...f, default_headers: kopf };
      delete body.secret;
      if (feld && f.secret) body[feld] = f.secret;
      if (editId) {
        delete body.name;
        return destinationApi.update(editId, body);
      }
      return destinationApi.create({
        ...body,
        user_id: scope === "user" ? userId : null,
        project_id: scope === "project" ? projectId : null,
      });
    },
    onSuccess: () => { reset(); inv(); },
    onError: fail,
  });
  const loeschen = useMutation({
    mutationFn: (id: number) => destinationApi.del(id), onSuccess: inv, onError: fail,
  });
  const testen = useMutation({
    mutationFn: (id: number) => destinationApi.test(id, { method: "GET", path: "" }),
    onSuccess: (r, id) =>
      setProbe((p) => ({ ...p, [id]: `HTTP ${r.status_code}${r.ok ? " ✓" : " ✗"}` })),
    onError: (e, id) =>
      setProbe((p) => ({ ...p, [id]: e instanceof ApiError ? e.message : "Fehler" })),
  });

  const bearbeiten = (d: Destination) => {
    setEditId(d.id);
    setErr("");
    setKopf(d.default_headers || {});
    setF({ ...LEER, ...d, secret: "" });
  };

  const inp = "rounded border border-line bg-surface px-2 py-1.5 text-sm text-ink";
  const [secretField, secretLabel] = SECRET_FIELD[f.auth_type] || ["", ""];

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">
        {tr("destinations_panel.einleitung")}
        {scope === "user" && ` ${tr("destinations_panel.einleitung_user")}`}
        {scope === "project" && ` ${tr("destinations_panel.einleitung_projekt")}`}
      </p>

      {err && <div className="rounded border border-red-500/40 bg-red-500/10 p-2 text-sm text-red-300">{err}</div>}

      <div className="space-y-2">
        {ziele?.map((d) => (
          <div key={d.id} className={`rounded border border-line bg-card p-2 text-sm ${d.enabled ? "" : "opacity-50"}`}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs text-muted">{d.name}</span>
              <span className="font-medium">{d.label || d.base_url}</span>
              <span className="rounded bg-surface px-1.5 py-0.5 text-xs text-muted">
                {AUTH.find(([k]) => k === d.auth_type)?.[1] || d.auth_type}
              </span>
              {d.has_secret && <span className="text-xs text-green-400" title={tr("destinations_panel.geheimnis_hinterlegt")}>🔑</span>}
              {d.allow_agents && (
                <span className="rounded bg-purple-500/15 px-1.5 py-0.5 text-xs text-purple-300">
                  für Agenten frei
                </span>
              )}
              <div className="flex-1" />
              {probe[d.id] && <span className="text-xs text-muted">{probe[d.id]}</span>}
              <button onClick={() => testen.mutate(d.id)} title={tr("destinations_panel.probeaufruf_get_auf_die_basis_url")}
                className="rounded border border-line px-2 py-0.5 text-xs hover:border-brand">{tr("destinations_panel.testen")}</button>
              <button onClick={() => bearbeiten(d)}
                className="rounded border border-line px-2 py-0.5 text-xs hover:border-brand">{tr("destinations_panel.bearbeiten")}</button>
              <button onClick={() => confirm(`Ziel „${d.name}" löschen?`) && loeschen.mutate(d.id)}
                className="rounded border border-line px-2 py-0.5 text-xs hover:border-red-400">{tr("destinations_panel.loeschen")}</button>
            </div>
            <div className="mt-1 truncate text-xs text-muted">{d.base_url}</div>
          </div>
        ))}
        {ziele?.length === 0 && <div className="text-xs text-muted">{tr("destinations_panel.noch_keine_ziele")}</div>}
      </div>

      <div className="space-y-2 rounded-lg border border-line bg-card p-3">
        <div className="text-sm font-medium">{editId ? "Ziel bearbeiten" : "Neues Ziel"}</div>
        <div className="grid grid-cols-2 gap-2">
          <input value={f.name} disabled={!!editId} onChange={(e) => setF({ ...f, name: e.target.value })}
            placeholder={tr("destinations_panel.name_z_b_crm")} className={`${inp} font-mono disabled:opacity-50`} />
          <input value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })}
            placeholder={tr("destinations_panel.bezeichnung")} className={inp} />
          <input value={f.base_url} onChange={(e) => setF({ ...f, base_url: e.target.value })}
            placeholder={tr("destinations_panel.basis_url_https_api_example_com_v1")} className={`${inp} col-span-2`} />
          <select value={f.auth_type} onChange={(e) => setF({ ...f, auth_type: e.target.value })} className={inp}>
            {AUTH.map(([k, l]) => <option key={k} value={k}>{tr(l)}</option>)}
          </select>
          <input type="number" min={1} max={600} value={f.timeout_sec}
            onChange={(e) => setF({ ...f, timeout_sec: Number(e.target.value) })}
            placeholder={tr("destinations_panel.zeitlimit_s")} className={inp} />
          <input type="number" min={500} max={60000} step={500} value={f.max_response_chars}
            onChange={(e) => setF({ ...f, max_response_chars: Number(e.target.value) })}
            title={tr("destinations_panel.wie_viel_der_antwort_der_aufrufer_hoechs")}
            placeholder={tr("destinations_panel.antwort_max_zeichen")} className={inp} />

          {f.auth_type === "basic" && (
            <input value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })}
              placeholder={tr("destinations_panel.benutzername")} className={inp} />
          )}
          {f.auth_type === "api_key" && (
            <>
              <input value={f.api_key_name} onChange={(e) => setF({ ...f, api_key_name: e.target.value })}
                placeholder={tr("destinations_panel.name_des_schluessels")} className={inp} />
              <select value={f.api_key_in} onChange={(e) => setF({ ...f, api_key_in: e.target.value })} className={inp}>
                <option value="header">{tr("destinations_panel.im_kopf")}</option>
                <option value="query">{tr("destinations_panel.in_der_url")}</option>
              </select>
            </>
          )}
          {f.auth_type === "hmac" && (
            <>
              <input value={f.hmac_header} onChange={(e) => setF({ ...f, hmac_header: e.target.value })}
                placeholder={tr("destinations_panel.signatur_kopfzeile")} className={inp} />
              <input value={f.hmac_prefix} onChange={(e) => setF({ ...f, hmac_prefix: e.target.value })}
                placeholder={tr("destinations_panel.praefix_leer_lassen_z_b_hermes")} className={inp} />
            </>
          )}
          {f.auth_type === "oauth2_cc" && (
            <>
              <input value={f.oauth_token_url} onChange={(e) => setF({ ...f, oauth_token_url: e.target.value })}
                placeholder={tr("destinations_panel.token_url")} className={`${inp} col-span-2`} />
              <input value={f.oauth_client_id} onChange={(e) => setF({ ...f, oauth_client_id: e.target.value })}
                placeholder={tr("destinations_panel.client_id")} className={inp} />
              <input value={f.oauth_scope} onChange={(e) => setF({ ...f, oauth_scope: e.target.value })}
                placeholder={tr("destinations_panel.scope_optional")} className={inp} />
            </>
          )}
          {secretField && (
            <input type="password" value={f.secret} onChange={(e) => setF({ ...f, secret: e.target.value })}
              placeholder={editId ? tr("destinations_panel.geheimnis_unveraendert", { feld: tr(secretLabel) }) : tr(secretLabel)}
              className={`${inp} col-span-2`} />
          )}
        </div>

        <div>
          <div className="mb-1 text-xs font-medium text-muted">{tr("destinations_panel.feste_kopfzeilen_bei_jedem_aufruf")}</div>
          <KeyValueEditor value={kopf} onChange={setKopf} />
        </div>

        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={!!f.verify_tls}
            onChange={(e) => setF({ ...f, verify_tls: e.target.checked })} />
          TLS-Zertifikat prüfen
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={!!f.allow_agents}
            onChange={(e) => setF({ ...f, allow_agents: e.target.checked })} />
          Für KI-Agenten freigeben
          <span className="text-xs text-muted">{tr("destinations_panel.sonst_nur_prozesse_und_jobs")}</span>
        </label>

        <div className="flex gap-2">
          <button onClick={() => f.name.trim() && f.base_url.trim() && speichern.mutate()}
            className="rounded bg-brand px-3 py-1.5 text-sm text-white">
            {editId ? "Speichern" : "Anlegen"}
          </button>
          {editId && (
            <button onClick={reset} className="rounded border border-line px-3 py-1.5 text-sm">{tr("destinations_panel.abbrechen")}</button>
          )}
        </div>
      </div>
    </div>
  );
}
