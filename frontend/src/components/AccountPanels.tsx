import { useEffect, useState } from "react";
import { tr } from "../i18n";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { BUTTON_SMALL } from "./ui";

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
function useNotice(): [string, (t: string) => void] {
  const [msg, setMsg] = useState("");
  return [msg, (t: string) => { setMsg(t); setTimeout(() => setMsg(""), 2500); }];
}

const KARTE = "rounded-lg border border-line bg-card p-4";
const FIELD = "rounded border border-line bg-surface px-2 py-1";

export function AgentsOperationPanel() {
  const { flags, inv } = useFlags();
  const [runners, setRunners] = useState(3);
  const [msg, flash] = useNotice();
  useEffect(() => { if (flags) setRunners(flags.max_runners ?? 3); }, [flags]);

  return (
    <div className={KARTE}>
      <div className="mb-3 text-sm font-medium">{tr("preferences_panel.agent_operation")}</div>
      <div className="flex flex-wrap items-center gap-2 text-sm sm:gap-3">
        <label className="text-muted">{tr("preferences_panel.concurrent_runs_max_20")}</label>
        <input type="number" min={1} max={20} value={runners}
          onChange={(e) => setRunners(+e.target.value)} className={`w-20 ${FIELD}`} />
        <button onClick={async () => {
          await api.put("/me/runner-limit", { value: runners }); inv(); flash(tr("account.limit_saved"));
        }} className={BUTTON_SMALL.primary}>{tr("preferences_panel.save")}</button>
      </div>
      {msg && <div className="mt-2 text-sm text-green-400">{msg}</div>}
    </div>
  );
}

export function AssistantNoticesPanel() {
  const { flags, inv } = useFlags();
  const [notify, setNotify] = useState("needed");
  const [msg, flash] = useNotice();
  useEffect(() => { if (flags) setNotify(flags.assistant_notify || "needed"); }, [flags]);

  return (
    <div className={KARTE}>
      <div className="mb-1 text-sm font-medium">{tr("preferences_panel.messages_assistant")}</div>
      <p className="mb-3 text-xs text-muted">{tr("account.personal_assistant_works_through")}</p>
      <select value={notify} onChange={async (e) => {
        setNotify(e.target.value);
        await api.put("/me/assistant-notify", { value: e.target.value });
        inv(); flash(tr("account.saved"));
      }} className={`text-sm ${FIELD}`}>
        <option value="needed">{tr("preferences_panel.only_when_i_need_to_know_recommended")}</option>
        <option value="always">{tr("preferences_panel.every_finished_item")}</option>
        <option value="errors">{tr("preferences_panel.failures_only")}</option>
        <option value="never">{tr("preferences_panel.never")}</option>
      </select>
      <p className="mt-2 text-[11px] text-muted">{tr("account.questions_ask_chat_always")}</p>
      {msg && <div className="mt-2 text-sm text-green-400">{msg}</div>}
    </div>
  );
}

/**
 * Zeitzone dieser Person.
 *
 * It answers the question what "8 o'clock" means — in three places at once: in the times of
 * this UI, in the night window of the agents and in the schedule of one's own jobs. Before, the
 * UI computed with the zone of the browser and the server in UTC; a
 * Cron-Job „0 8 * * *" lief damit im Sommer um zehn.
 */
export function TimezonePanel() {
  const { flags, inv } = useFlags();
  const [zone, setZone] = useState("");
  const [msg, flash] = useNotice();
  const { data: zones } = useQuery({
    queryKey: ["timezones"], queryFn: () => api.get<string[]>("/timezones"),
    staleTime: 24 * 60 * 60_000,
  });
  useEffect(() => { if (flags) setZone(flags.timezone || ""); }, [flags]);

  const now = zone
    ? new Date().toLocaleString("de-DE", { timeZone: zone, hour: "2-digit", minute: "2-digit" })
    : "";

  return (
    <div className={KARTE}>
      <div className="mb-1 text-sm font-medium">Zeitzone</div>
      <p className="mb-3 text-xs text-muted">
        {tr("account.timezone_hint")}
      </p>
      <div className="flex flex-wrap items-center gap-2 text-sm sm:gap-3">
        <select value={zone} onChange={async (e) => {
          setZone(e.target.value);
          await api.put("/me/timezone", { value: e.target.value });
          inv(); flash(tr("account.saved"));
        }} className={`${FIELD} max-w-xs`}>
          {!zones && <option value={zone}>{zone || "…"}</option>}
          {zones?.map((z) => <option key={z} value={z}>{z}</option>)}
        </select>
        {now && <span className="text-xs text-muted">{tr("account.local_time_now", { time: now })}</span>}
      </div>
      {msg && <div className="mt-2 text-sm text-green-400">{msg}</div>}
    </div>
  );
}

export function MemoryPanel() {
  const { flags, inv } = useFlags();
  const [path, setPath] = useState("");
  const [msg, flash] = useNotice();
  useEffect(() => { if (flags) setPath(flags.vault_memory_path || ""); }, [flags]);

  return (
    <div className={KARTE}>
      <div className="mb-1 text-sm font-medium">{tr("preferences_panel.memory_agents")}</div>
      <p className="mb-3 text-xs text-muted">
        {tr("account.folder_obsidian_vault_where")} <code>{tr("preferences_panel.human_md")}</code>,{" "}
        <code>{tr("preferences_panel.agent_lt_role_gt_md")}</code>{" "}
        {tr("common.text_2")} <code>{tr("preferences_panel.project_lt_key_gt_md")}</code>.
      </p>
      <div className="flex flex-wrap items-center gap-2 text-sm sm:gap-3">
        <input value={path} onChange={(e) => setPath(e.target.value)}
          placeholder={tr("preferences_panel.e_g_04_traccoon_memory")} className={`w-72 ${FIELD}`} />
        <button onClick={async () => {
          await api.put("/me/vault-memory-path", { value: path });
          inv(); flash(tr(path ? "preferences_panel.memory_folder_saved" : "preferences_panel.memory_switched_off"));
        }} className={BUTTON_SMALL.primary}>{tr("preferences_panel.save")}</button>
      </div>
      <p className="mt-2 text-[11px] text-muted">{tr("preferences_panel.empty_means_no_memory")}</p>
      {msg && <div className="mt-2 text-sm text-green-400">{msg}</div>}
    </div>
  );
}

export function NightWindowPanel() {
  const { flags, inv } = useFlags();
  const [start, setStart] = useState(22);
  const [ende, setEnde] = useState(6);
  const [msg, flash] = useNotice();
  useEffect(() => {
    if (!flags) return;
    setStart(flags.night_start_hour ?? 22);
    setEnde(flags.night_end_hour ?? 6);
  }, [flags]);

  const days: number[] = flags?.night_days || [0, 1, 2, 3, 4, 5, 6];
  const tagUm = async (idx: number) => {
    const next = days.includes(idx) ? days.filter((d) => d !== idx) : [...days, idx].sort();
    await api.put("/me/night-window", { start_hour: start, end_hour: ende, days: next });
    inv();
  };

  return (
    <div className={KARTE}>
      <div className="mb-1 text-sm font-medium">{tr("preferences_panel.night_window")}</div>
      <p className="mb-3 text-xs text-muted">{tr("preferences_panel.tickets_marked_as_night_work_only_start_insid")}</p>
      <div className="mb-3 flex items-center gap-2 text-sm">
        <input type="number" min={0} max={23} value={start} onChange={(e) => setStart(+e.target.value)}
          className={`w-16 ${FIELD}`} />
        <span className="text-muted">{tr("common.text")}</span>
        <input type="number" min={0} max={23} value={ende} onChange={(e) => setEnde(+e.target.value)}
          className={`w-16 ${FIELD}`} />
        <span className="text-muted">{tr("preferences_panel.uhr")}</span>
        <button onClick={async () => {
          await api.put("/me/night-window", { start_hour: start, end_hour: ende, days: days });
          inv(); flash(tr("preferences_panel.window_saved"));
        }} className={BUTTON_SMALL.primary}>{tr("preferences_panel.save")}</button>
      </div>
      <div className="mb-3 flex gap-1">
        {DAYS.map((d, i) => (
          <button key={d} onClick={() => tagUm(i)}
            className={`rounded px-2 py-1 text-xs ${days.includes(i)
              ? "bg-brand text-white" : "border border-line text-muted"}`}>{tr(`common.tag_${d}`)}</button>
        ))}
      </div>
      <Toggle label={tr("preferences_panel.ignore_window")} hint={tr("preferences_panel.night_tickets_run_any")}
        on={!!flags?.night_override}
        onChange={async (v) => { await api.put("/me/night-override", { active: v }); inv(); }} />
      {msg && <div className="mt-2 text-sm text-green-400">{msg}</div>}
    </div>
  );
}

export function MySwitchPanel() {
  const { flags, inv } = useFlags();
  const um = async (path: string, on: boolean) => {
    on ? await api.post(path) : await api.del(path);
    inv();
  };
  return (
    <div className={KARTE}>
      <div className="mb-3 text-sm font-medium">{tr("preferences_panel.my_switches")}</div>
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
export function SystemSwitchPanel() {
  const { flags, inv } = useFlags();
  const um = async (path: string, on: boolean) => {
    on ? await api.post(path) : await api.del(path);
    inv();
  };
  return (
    <div className="rounded-lg border border-yellow-500/40 bg-card p-4">
      <div className="mb-3 text-sm font-medium text-yellow-400">{tr("preferences_panel.system_wide_administration")}</div>
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
