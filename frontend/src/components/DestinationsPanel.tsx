import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, destinationApi, type Destination, type DestinationScope } from "../api";
import { KeyValueEditor } from "./workflow/kv";

const AUTH: [string, string][] = [
  ["none", "Keine"],
  ["basic", "Basic (Benutzer/Passwort)"],
  ["bearer", "Bearer-Token"],
  ["api_key", "API-Key"],
  ["hmac", "HMAC-Signatur"],
  ["oauth2_cc", "OAuth2 Client Credentials"],
];

/** Welches Geheimnis das jeweilige Verfahren braucht (Feldname für die API + Beschriftung). */
const SECRET_FIELD: Record<string, [string, string]> = {
  basic: ["password", "Passwort"],
  bearer: ["token", "Token"],
  api_key: ["api_key", "API-Key"],
  hmac: ["hmac_secret", "Signatur-Geheimnis"],
  oauth2_cc: ["client_secret", "Client-Secret"],
};

const LEER = {
  name: "", label: "", base_url: "", auth_type: "none", username: "",
  api_key_name: "X-API-Key", api_key_in: "header",
  hmac_header: "X-Webhook-Signature", hmac_algo: "sha256", hmac_prefix: "",
  oauth_token_url: "", oauth_client_id: "", oauth_scope: "", oauth_audience: "",
  timeout_sec: 30, verify_tls: true, allow_agents: false, max_response_chars: 4000, secret: "",
};

/**
 * Ziele = externe Gegenstellen mit hinterlegter Anmeldung (wie Destinations in der BTP).
 * Prozesse, Jobs und Agenten nennen später nur den Namen — Basis-URL und Zugangsdaten
 * stehen genau hier.
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
        Ein <b>Ziel</b> bündelt Basis-URL und Anmeldung einer externen Gegenstelle. Prozesse,
        Jobs und (falls freigegeben) KI-Agenten rufen es später nur über seinen <b>Namen</b> auf
        und ergänzen Methode, Pfad, Query, Kopfzeilen und Body. Zugangsdaten werden verschlüsselt
        gespeichert und nie wieder angezeigt.
        {scope === "user" && " Persönliche Ziele gelten in allen deinen Projekten."}
        {scope === "project" && " Ein Projekt-Ziel überschreibt ein gleichnamiges persönliches oder systemweites."}
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
              {d.has_secret && <span className="text-xs text-green-400" title="Geheimnis hinterlegt">🔑</span>}
              {d.allow_agents && (
                <span className="rounded bg-purple-500/15 px-1.5 py-0.5 text-xs text-purple-300">
                  für Agenten frei
                </span>
              )}
              <div className="flex-1" />
              {probe[d.id] && <span className="text-xs text-muted">{probe[d.id]}</span>}
              <button onClick={() => testen.mutate(d.id)} title="Probeaufruf (GET auf die Basis-URL)"
                className="rounded border border-line px-2 py-0.5 text-xs hover:border-brand">Testen</button>
              <button onClick={() => bearbeiten(d)}
                className="rounded border border-line px-2 py-0.5 text-xs hover:border-brand">Bearbeiten</button>
              <button onClick={() => confirm(`Ziel „${d.name}" löschen?`) && loeschen.mutate(d.id)}
                className="rounded border border-line px-2 py-0.5 text-xs hover:border-red-400">Löschen</button>
            </div>
            <div className="mt-1 truncate text-xs text-muted">{d.base_url}</div>
          </div>
        ))}
        {ziele?.length === 0 && <div className="text-xs text-muted">Noch keine Ziele.</div>}
      </div>

      <div className="space-y-2 rounded-lg border border-line bg-card p-3">
        <div className="text-sm font-medium">{editId ? "Ziel bearbeiten" : "Neues Ziel"}</div>
        <div className="grid grid-cols-2 gap-2">
          <input value={f.name} disabled={!!editId} onChange={(e) => setF({ ...f, name: e.target.value })}
            placeholder="Name (z. B. crm)" className={`${inp} font-mono disabled:opacity-50`} />
          <input value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })}
            placeholder="Bezeichnung" className={inp} />
          <input value={f.base_url} onChange={(e) => setF({ ...f, base_url: e.target.value })}
            placeholder="Basis-URL (https://api.example.com/v1)" className={`${inp} col-span-2`} />
          <select value={f.auth_type} onChange={(e) => setF({ ...f, auth_type: e.target.value })} className={inp}>
            {AUTH.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          <input type="number" min={1} max={600} value={f.timeout_sec}
            onChange={(e) => setF({ ...f, timeout_sec: Number(e.target.value) })}
            placeholder="Zeitlimit (s)" className={inp} />
          <input type="number" min={500} max={60000} step={500} value={f.max_response_chars}
            onChange={(e) => setF({ ...f, max_response_chars: Number(e.target.value) })}
            title="Wie viel der Antwort der Aufrufer höchstens sieht. Nur anheben, wenn die Gegenstelle ihre Lage bewusst in einem Abruf liefert."
            placeholder="Antwort max. (Zeichen)" className={inp} />

          {f.auth_type === "basic" && (
            <input value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })}
              placeholder="Benutzername" className={inp} />
          )}
          {f.auth_type === "api_key" && (
            <>
              <input value={f.api_key_name} onChange={(e) => setF({ ...f, api_key_name: e.target.value })}
                placeholder="Name des Schlüssels" className={inp} />
              <select value={f.api_key_in} onChange={(e) => setF({ ...f, api_key_in: e.target.value })} className={inp}>
                <option value="header">im Kopf (Header)</option>
                <option value="query">in der URL (Query)</option>
              </select>
            </>
          )}
          {f.auth_type === "hmac" && (
            <>
              <input value={f.hmac_header} onChange={(e) => setF({ ...f, hmac_header: e.target.value })}
                placeholder="Signatur-Kopfzeile" className={inp} />
              <input value={f.hmac_prefix} onChange={(e) => setF({ ...f, hmac_prefix: e.target.value })}
                placeholder="Präfix (leer lassen, z. B. Hermes)" className={inp} />
            </>
          )}
          {f.auth_type === "oauth2_cc" && (
            <>
              <input value={f.oauth_token_url} onChange={(e) => setF({ ...f, oauth_token_url: e.target.value })}
                placeholder="Token-URL" className={`${inp} col-span-2`} />
              <input value={f.oauth_client_id} onChange={(e) => setF({ ...f, oauth_client_id: e.target.value })}
                placeholder="Client-ID" className={inp} />
              <input value={f.oauth_scope} onChange={(e) => setF({ ...f, oauth_scope: e.target.value })}
                placeholder="Scope (optional)" className={inp} />
            </>
          )}
          {secretField && (
            <input type="password" value={f.secret} onChange={(e) => setF({ ...f, secret: e.target.value })}
              placeholder={editId ? `${secretLabel} (leer = unverändert)` : secretLabel}
              className={`${inp} col-span-2`} />
          )}
        </div>

        <div>
          <div className="mb-1 text-xs font-medium text-muted">Feste Kopfzeilen (bei jedem Aufruf)</div>
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
          <span className="text-xs text-muted">(sonst nur Prozesse und Jobs)</span>
        </label>

        <div className="flex gap-2">
          <button onClick={() => f.name.trim() && f.base_url.trim() && speichern.mutate()}
            className="rounded bg-brand px-3 py-1.5 text-sm text-white">
            {editId ? "Speichern" : "Anlegen"}
          </button>
          {editId && (
            <button onClick={reset} className="rounded border border-line px-3 py-1.5 text-sm">Abbrechen</button>
          )}
        </div>
      </div>
    </div>
  );
}
