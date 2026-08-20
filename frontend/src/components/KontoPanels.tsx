import { useEffect, useState } from "react";
import { tr } from "../i18n";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { KNOPF_KLEIN } from "./ui";

/**
 * The switches that hang off `/me/flags`, one panel per subject.
 *
 * They used to sit together in a single `PreferencesPanel` under the settings, while the
 * other half of the same subject (language, e-mail, notification channel, password) stood
 * on the profile page. Two pages for one person, and the Telegram chat id even in both, in
 * two fields writing to two endpoints. Split by subject here, assembled by the account
 * page, and the duplicate field is gone: the channel belongs to the notifications.
 */

// Keys instead of texts: the lists come into being while the module loads, and a tr() here
// would fix the language of the first call.
const DAYS = ["mo", "di", "mi", "do", "fr", "sa", "so"];

/** Per-user flags (a short description for the UI). */
const USER_FLAGS: string[] = ["shift_end", "sonnet_max", "show_token_prices", "ticket_notify"];
/** System wide, therefore not on the account page but in the administration. */
const GLOBAL_FLAGS: [string, string][] = [
  ["global_pause", "/runner/global-pause"],
  ["strict_success", "/strict-success"],
];

function useFlags() {
  const qc = useQueryClient();
  const { data: flags } = useQuery({ queryKey: ["flags"], queryFn: () => api.get<any>("/me/flags") });
  return { flags, inv: () => qc.invalidateQueries({ queryKey: ["flags"] }) };
}

/** Short green confirmation under a panel; disappears on its own. */
function useMeldung(): [string, (t: string) => void] {
  const [msg, setMsg] = useState("");
  return [msg, (t: string) => { setMsg(t); setTimeout(() => setMsg(""), 2500); }];
}

const KARTE = "rounded-lg border border-line bg-card p-4";
const FELD = "rounded border border-line bg-surface px-2 py-1";

export function AgentenBetriebPanel() {
  const { flags, inv } = useFlags();
  const [runners, setRunners] = useState(3);
  const [msg, flash] = useMeldung();
  useEffect(() => { if (flags) setRunners(flags.max_runners ?? 3); }, [flags]);

  return (
    <div className={KARTE}>
      <div className="mb-3 text-sm font-medium">{tr("preferences_panel.agenten_betrieb")}</div>
      <div className="flex flex-wrap items-center gap-2 text-sm sm:gap-3">
        <label className="text-muted">{tr("preferences_panel.gleichzeitige_laeufe_max_20")}</label>
        <input type="number" min={1} max={20} value={runners}
          onChange={(e) => setRunners(+e.target.value)} className={`w-20 ${FELD}`} />
        <button onClick={async () => {
          await api.put("/me/runner-limit", { value: runners }); inv(); flash(tr("konto.limit_gespeichert"));
        }} className={KNOPF_KLEIN.haupt}>{tr("preferences_panel.speichern")}</button>
      </div>
      {msg && <div className="mt-2 text-sm text-green-400">{msg}</div>}
    </div>
  );
}

export function AssistentMeldungenPanel() {
  const { flags, inv } = useFlags();
  const [notify, setNotify] = useState("needed");
  const [msg, flash] = useMeldung();
  useEffect(() => { if (flags) setNotify(flags.assistant_notify || "needed"); }, [flags]);

  return (
    <div className={KARTE}>
      <div className="mb-1 text-sm font-medium">{tr("preferences_panel.meldungen_des_assistenten")}</div>
      <p className="mb-3 text-xs text-muted">{tr("konto.assistent_meldungen_hinweis")}</p>
      <select value={notify} onChange={async (e) => {
        setNotify(e.target.value);
        await api.put("/me/assistant-notify", { value: e.target.value });
        inv(); flash(tr("konto.gespeichert"));
      }} className={`text-sm ${FELD}`}>
        <option value="needed">{tr("preferences_panel.nur_wenn_ich_etwas_wissen_muss_empfohlen")}</option>
        <option value="always">{tr("preferences_panel.jeder_erledigte_eingang")}</option>
        <option value="errors">{tr("preferences_panel.nur_pannen")}</option>
        <option value="never">{tr("preferences_panel.gar_nicht")}</option>
      </select>
      <p className="mt-2 text-[11px] text-muted">{tr("konto.assistent_fragen_hinweis")}</p>
      {msg && <div className="mt-2 text-sm text-green-400">{msg}</div>}
    </div>
  );
}

/**
 * Zeitzone dieser Person.
 *
 * Sie beantwortet die Frage, was „8 Uhr" heißt — und zwar an drei Stellen zugleich: in den
 * Uhrzeiten dieser Oberfläche, im Nachtfenster der Agenten und im Zeitplan der eigenen Jobs.
 * Vorher rechnete die Oberfläche mit der Zone des Browsers und der Server in UTC; ein
 * Cron-Job „0 8 * * *" lief damit im Sommer um zehn.
 */
export function ZeitzonePanel() {
  const { flags, inv } = useFlags();
  const [zone, setZone] = useState("");
  const [msg, flash] = useMeldung();
  const { data: zonen } = useQuery({
    queryKey: ["timezones"], queryFn: () => api.get<string[]>("/timezones"),
    staleTime: 24 * 60 * 60_000,
  });
  useEffect(() => { if (flags) setZone(flags.timezone || ""); }, [flags]);

  const jetzt = zone
    ? new Date().toLocaleString("de-DE", { timeZone: zone, hour: "2-digit", minute: "2-digit" })
    : "";

  return (
    <div className={KARTE}>
      <div className="mb-1 text-sm font-medium">Zeitzone</div>
      <p className="mb-3 text-xs text-muted">
        Gilt für die Uhrzeiten hier, für das Nachtfenster und für den Zeitplan deiner Jobs:
        „0 8 * * *" heißt acht Uhr in dieser Zone.
      </p>
      <div className="flex flex-wrap items-center gap-2 text-sm sm:gap-3">
        <select value={zone} onChange={async (e) => {
          setZone(e.target.value);
          await api.put("/me/timezone", { value: e.target.value });
          inv(); flash(tr("konto.gespeichert"));
        }} className={`${FELD} max-w-xs`}>
          {!zonen && <option value={zone}>{zone || "…"}</option>}
          {zonen?.map((z) => <option key={z} value={z}>{z}</option>)}
        </select>
        {jetzt && <span className="text-xs text-muted">dort ist es gerade {jetzt} Uhr</span>}
      </div>
      {msg && <div className="mt-2 text-sm text-green-400">{msg}</div>}
    </div>
  );
}

export function GedaechtnisPanel() {
  const { flags, inv } = useFlags();
  const [pfad, setPfad] = useState("");
  const [msg, flash] = useMeldung();
  useEffect(() => { if (flags) setPfad(flags.vault_memory_path || ""); }, [flags]);

  return (
    <div className={KARTE}>
      <div className="mb-1 text-sm font-medium">{tr("preferences_panel.gedaechtnis_der_agenten")}</div>
      <p className="mb-3 text-xs text-muted">
        {tr("konto.gedaechtnis_erklaerung")} <code>{tr("preferences_panel.mensch_md")}</code>,{" "}
        <code>{tr("preferences_panel.agent_lt_rolle_gt_md")}</code>{" "}
        {tr("common.und")} <code>{tr("preferences_panel.projekt_lt_key_gt_md")}</code>.
      </p>
      <div className="flex flex-wrap items-center gap-2 text-sm sm:gap-3">
        <input value={pfad} onChange={(e) => setPfad(e.target.value)}
          placeholder={tr("preferences_panel.z_b_04_traccoon_gedaechtnis")} className={`w-72 ${FELD}`} />
        <button onClick={async () => {
          await api.put("/me/vault-memory-path", { value: pfad });
          inv(); flash(tr(pfad ? "preferences_panel.gedaechtnis_gespeichert" : "preferences_panel.gedaechtnis_aus"));
        }} className={KNOPF_KLEIN.haupt}>{tr("preferences_panel.speichern")}</button>
      </div>
      <p className="mt-2 text-[11px] text-muted">{tr("preferences_panel.gedaechtnis_hinweis")}</p>
      {msg && <div className="mt-2 text-sm text-green-400">{msg}</div>}
    </div>
  );
}

export function NachtFensterPanel() {
  const { flags, inv } = useFlags();
  const [start, setStart] = useState(22);
  const [ende, setEnde] = useState(6);
  const [msg, flash] = useMeldung();
  useEffect(() => {
    if (!flags) return;
    setStart(flags.night_start_hour ?? 22);
    setEnde(flags.night_end_hour ?? 6);
  }, [flags]);

  const tage: number[] = flags?.night_days || [0, 1, 2, 3, 4, 5, 6];
  const tagUm = async (idx: number) => {
    const next = tage.includes(idx) ? tage.filter((d) => d !== idx) : [...tage, idx].sort();
    await api.put("/me/night-window", { start_hour: start, end_hour: ende, days: next });
    inv();
  };

  return (
    <div className={KARTE}>
      <div className="mb-1 text-sm font-medium">{tr("preferences_panel.nacht_fenster")}</div>
      <p className="mb-3 text-xs text-muted">{tr("preferences_panel.tickets_mit_der_markierung_nachtarbeit_s")}</p>
      <div className="mb-3 flex items-center gap-2 text-sm">
        <input type="number" min={0} max={23} value={start} onChange={(e) => setStart(+e.target.value)}
          className={`w-16 ${FELD}`} />
        <span className="text-muted">{tr("common.bis")}</span>
        <input type="number" min={0} max={23} value={ende} onChange={(e) => setEnde(+e.target.value)}
          className={`w-16 ${FELD}`} />
        <span className="text-muted">{tr("preferences_panel.uhr")}</span>
        <button onClick={async () => {
          await api.put("/me/night-window", { start_hour: start, end_hour: ende, days: tage });
          inv(); flash(tr("preferences_panel.fenster_gespeichert"));
        }} className={KNOPF_KLEIN.haupt}>{tr("preferences_panel.speichern")}</button>
      </div>
      <div className="mb-3 flex gap-1">
        {DAYS.map((d, i) => (
          <button key={d} onClick={() => tagUm(i)}
            className={`rounded px-2 py-1 text-xs ${tage.includes(i)
              ? "bg-brand text-white" : "border border-line text-muted"}`}>{tr(`common.tag_${d}`)}</button>
        ))}
      </div>
      <Toggle label={tr("preferences_panel.fenster_ignorieren")} hint={tr("preferences_panel.nacht_tickets_jederzeit")}
        on={!!flags?.night_override}
        onChange={async (v) => { await api.put("/me/night-override", { active: v }); inv(); }} />
      {msg && <div className="mt-2 text-sm text-green-400">{msg}</div>}
    </div>
  );
}

export function MeineSchalterPanel() {
  const { flags, inv } = useFlags();
  const um = async (path: string, on: boolean) => {
    on ? await api.post(path) : await api.del(path);
    inv();
  };
  return (
    <div className={KARTE}>
      <div className="mb-3 text-sm font-medium">{tr("preferences_panel.meine_schalter")}</div>
      <div className="space-y-2">
        {USER_FLAGS.map((key) => (
          <Toggle key={key} label={tr(`preferences_panel.flag_${key}`)}
            hint={tr(`preferences_panel.flag_${key}_hinweis`)} on={!!flags?.[key]}
            onChange={(v) => um(`/me/${key.replace(/_/g, "-")}`, v)} />
        ))}
      </div>
    </div>
  );
}

/** System wide switches. They belong to the installation, not to the person, and therefore
 *  stand in the administration (maintenance) and no longer between somebody's own settings. */
export function SystemSchalterPanel() {
  const { flags, inv } = useFlags();
  const um = async (path: string, on: boolean) => {
    on ? await api.post(path) : await api.del(path);
    inv();
  };
  return (
    <div className="rounded-lg border border-yellow-500/40 bg-card p-4">
      <div className="mb-3 text-sm font-medium text-yellow-400">{tr("preferences_panel.systemweit_administration")}</div>
      <div className="space-y-2">
        {GLOBAL_FLAGS.map(([key, path]) => (
          <Toggle key={key} label={tr(`preferences_panel.flag_${key}`)}
            hint={tr(`preferences_panel.flag_${key}_hinweis`)} on={!!flags?.[key]}
            onChange={(v) => um(path, v)} />
        ))}
      </div>
    </div>
  );
}

export function Toggle({ label, hint, on, onChange }: {
  label: string; hint: string; on: boolean; onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-start gap-3 text-sm">
      <input type="checkbox" checked={on} onChange={(e) => onChange(e.target.checked)} className="mt-1" />
      <span>
        <span className="text-ink">{label}</span>
        <span className="block text-xs text-muted">{hint}</span>
      </span>
    </label>
  );
}
