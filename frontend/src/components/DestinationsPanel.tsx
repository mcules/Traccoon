import { useState } from "react";
import { tr } from "../i18n";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, destinationApi, type Destination, type DestinationScope } from "../api";
import { KeyValueEditor } from "./workflow/kv";
import {
  Aktionen, Bereich, Dialog, DialogFuss, EINGABE, Feld, Fehlerzeile, ICON, IconKnopf, Liste,
  ListeLeer, ListenZeile, LoeschDialog, KNOPF } from "./ui";
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
  const [loeschZiel, setLoeschZiel] = useState<Destination | null>(null);
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
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.fehler"));

  const speichern = useMutation({
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
  const loeschen = useMutation({
    mutationFn: (id: number) => destinationApi.del(id),
    onSuccess: () => { setLoeschZiel(null); inv(); }, onError: fail,
  });
  const testen = useMutation({
    mutationFn: (id: number) => destinationApi.test(id, { method: "GET", path: "" }),
    onSuccess: (r, id) =>
      setProbe((p) => ({ ...p, [id]: `HTTP ${r.status_code}${r.ok ? " ✓" : " ✗"}` })),
    onError: (e, id) =>
      setProbe((p) => ({ ...p, [id]: e instanceof ApiError ? e.message : tr("common.fehler") })),
  });

  return (
    <Bereich hinweis={<>
      {tr("destinations_panel.einleitung")}
      {scope === "user" && ` ${tr("destinations_panel.einleitung_user")}`}
      {scope === "project" && ` ${tr("destinations_panel.einleitung_projekt")}`}
    </>}>
      <Fehlerzeile text={err} />

      <Liste>
        {ziele?.map((d) => (
          <ListenZeile key={d.id} gedimmt={!d.enabled}>
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
              <Aktionen>
                <IconKnopf icon={ICON.testen} titel={tr("destinations_panel.probeaufruf_get_auf_die_basis_url")}
                  onClick={() => testen.mutate(d.id)} disabled={testen.isPending} />
                <IconKnopf icon={ICON.bearbeiten} titel={tr("common.bearbeiten")} onClick={() => setDialog(d)} />
                <IconKnopf icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr onClick={() => setLoeschZiel(d)} />
              </Aktionen>
            </div>
          </ListenZeile>
        ))}
        {ziele?.length === 0 && <ListeLeer>{tr("destinations_panel.noch_keine_ziele")}</ListeLeer>}
      </Liste>

      <button onClick={() => { setErr(""); setDialog({}); }}
        className={KNOPF.haupt}>
        {ICON.neu} {tr("destinations_panel.neues_ziel")}
      </button>

      {dialog && (
        <ZielDialog ziel={"id" in dialog ? (dialog as Destination) : null}
          fehler={err}
          laeuft={speichern.isPending}
          onClose={() => { setDialog(null); setErr(""); }}
          onSpeichern={(body, id) => speichern.mutate({ id, body })} />
      )}
      {loeschZiel && (
        <LoeschDialog was={loeschZiel.name} hinweis={tr("destinations_panel.loeschen_hinweis")}
          laeuft={loeschen.isPending}
          onClose={() => setLoeschZiel(null)} onLoeschen={() => loeschen.mutate(loeschZiel.id)} />
      )}
    </Bereich>
  );
}

/**
 * Create and edit in one form.
 *
 * The name stays fixed once it exists: flows, jobs and agents address the destination by
 * exactly that name, and renaming it here would break them silently.
 */
function ZielDialog({ ziel, fehler, laeuft, onClose, onSpeichern }: {
  ziel: Destination | null;
  fehler: string;
  laeuft: boolean;
  onClose: () => void;
  onSpeichern: (body: Record<string, any>, id: number | null) => void;
}) {
  const [f, setF] = useState<Record<string, any>>(ziel ? { ...LEER, ...ziel, secret: "" } : LEER);
  const [kopf, setKopf] = useState<Record<string, any>>(ziel?.default_headers || {});
  const [secretField, secretLabel] = SECRET_FIELD[f.auth_type] || ["", ""];
  const kann = !!f.name.trim() && !!f.base_url.trim();

  const speichern = () => {
    const body: Record<string, any> = { ...f, default_headers: kopf };
    delete body.secret;
    if (secretField && f.secret) body[secretField] = f.secret;
    // The name belongs to the entry, not to the change: an update must not carry it.
    if (ziel) delete body.name;
    onSpeichern(body, ziel ? ziel.id : null);
  };

  return (
    <Dialog breit titel={ziel ? tr("destinations_panel.ziel_bearbeiten") : tr("destinations_panel.neues_ziel")}
      onClose={onClose}
      fuss={<DialogFuss onAbbrechen={onClose} onSpeichern={speichern} deaktiviert={!kann} laeuft={laeuft}
        speichernText={ziel ? undefined : tr("common.anlegen")} />}>
      <Fehlerzeile text={fehler} />
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Feld label={tr("destinations_panel.name_z_b_crm")} hinweis={ziel ? tr("destinations_panel.name_fest") : undefined}>
          <input value={f.name} disabled={!!ziel} autoFocus={!ziel}
            onChange={(e) => setF({ ...f, name: e.target.value })}
            className={`${EINGABE} font-mono disabled:opacity-60`} />
        </Feld>
        <Feld label={tr("destinations_panel.bezeichnung")}>
          <input value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} className={EINGABE} />
        </Feld>
        <div className="sm:col-span-2">
          <Feld label={tr("destinations_panel.basis_url_https_api_example_com_v1")}>
            <input value={f.base_url} autoFocus={!!ziel}
              onChange={(e) => setF({ ...f, base_url: e.target.value })} className={EINGABE} />
          </Feld>
        </div>
        <Feld label={tr("destinations_panel.anmeldung")}>
          <select value={f.auth_type} onChange={(e) => setF({ ...f, auth_type: e.target.value })} className={EINGABE}>
            {AUTH.map(([k, l]) => <option key={k} value={k}>{tr(l)}</option>)}
          </select>
        </Feld>
        <Feld label={tr("destinations_panel.zeitlimit_s")}>
          <input type="number" min={1} max={600} value={f.timeout_sec}
            onChange={(e) => setF({ ...f, timeout_sec: Number(e.target.value) })} className={EINGABE} />
        </Feld>
        <Feld label={tr("destinations_panel.antwort_max_zeichen")}
          hinweis={tr("destinations_panel.wie_viel_der_antwort_der_aufrufer_hoechs")}>
          <input type="number" min={500} max={60000} step={500} value={f.max_response_chars}
            onChange={(e) => setF({ ...f, max_response_chars: Number(e.target.value) })} className={EINGABE} />
        </Feld>

        {f.auth_type === "basic" && (
          <Feld label={tr("destinations_panel.benutzername")}>
            <input value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} className={EINGABE} />
          </Feld>
        )}
        {f.auth_type === "api_key" && (
          <>
            <Feld label={tr("destinations_panel.name_des_schluessels")}>
              <input value={f.api_key_name} onChange={(e) => setF({ ...f, api_key_name: e.target.value })} className={EINGABE} />
            </Feld>
            <Feld label={tr("destinations_panel.wohin")}>
              <select value={f.api_key_in} onChange={(e) => setF({ ...f, api_key_in: e.target.value })} className={EINGABE}>
                <option value="header">{tr("destinations_panel.im_kopf")}</option>
                <option value="query">{tr("destinations_panel.in_der_url")}</option>
              </select>
            </Feld>
          </>
        )}
        {f.auth_type === "hmac" && (
          <>
            <Feld label={tr("destinations_panel.signatur_kopfzeile")}>
              <input value={f.hmac_header} onChange={(e) => setF({ ...f, hmac_header: e.target.value })} className={EINGABE} />
            </Feld>
            <Feld label={tr("destinations_panel.praefix_leer_lassen_z_b_hermes")}>
              <input value={f.hmac_prefix} onChange={(e) => setF({ ...f, hmac_prefix: e.target.value })} className={EINGABE} />
            </Feld>
          </>
        )}
        {f.auth_type === "oauth2_cc" && (
          <>
            <div className="sm:col-span-2">
              <Feld label={tr("destinations_panel.token_url")}>
                <input value={f.oauth_token_url} onChange={(e) => setF({ ...f, oauth_token_url: e.target.value })} className={EINGABE} />
              </Feld>
            </div>
            <Feld label={tr("destinations_panel.client_id")}>
              <input value={f.oauth_client_id} onChange={(e) => setF({ ...f, oauth_client_id: e.target.value })} className={EINGABE} />
            </Feld>
            <Feld label={tr("destinations_panel.scope_optional")}>
              <input value={f.oauth_scope} onChange={(e) => setF({ ...f, oauth_scope: e.target.value })} className={EINGABE} />
            </Feld>
          </>
        )}
        {secretField && (
          <div className="sm:col-span-2">
            <Feld label={tr(secretLabel)}
              hinweis={ziel ? tr("destinations_panel.geheimnis_unveraendert", { feld: tr(secretLabel) }) : undefined}>
              <input type="password" value={f.secret}
                onChange={(e) => setF({ ...f, secret: e.target.value })} className={EINGABE} />
            </Feld>
          </div>
        )}

        <div className="sm:col-span-2">
          <div className="mb-1 text-xs font-medium text-muted">{tr("destinations_panel.feste_kopfzeilen_bei_jedem_aufruf")}</div>
          <KeyValueEditor value={kopf} onChange={setKopf} />
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
export function DestinationsBereich({ projectId }: { projectId?: number }) {
  const { user } = useAuth();
  const istAdmin = user?.global_role === "admin";
  const bereiche: [DestinationScope, string][] = [
    ...(projectId ? ([["project", tr("destinations_panel.bereich_projekt")]] as [DestinationScope, string][]) : []),
    ["user", tr("destinations_panel.bereich_ich")],
    ...(istAdmin ? ([["global", tr("destinations_panel.bereich_global")]] as [DestinationScope, string][]) : []),
  ];
  const [scope, setScope] = useState<DestinationScope>(bereiche[0][0]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5">
        {bereiche.map(([k, label]) => (
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
