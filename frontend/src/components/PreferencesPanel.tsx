import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";

const DAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"];

/** Per-User-Flags (Kurzbeschreibung fürs UI). */
const USER_FLAGS: [string, string, string][] = [
  ["shift_end", "Feierabend", "Keine neuen Agenten-Läufe starten (laufende gehen zu Ende)."],
  ["sonnet_max", "Sonnet bevorzugen", "Günstigeres Modell für Routine-Aufgaben verwenden."],
  ["show_token_prices", "Token-Preise zeigen", "Kosten je Lauf in der Oberfläche einblenden."],
  ["ticket_notify", "Ticket-Benachrichtigungen", "Bei Ticket-Ereignissen per Telegram melden."],
];
const GLOBAL_FLAGS: [string, string, string, string][] = [
  ["global_pause", "Not-Aus (alle Nutzer)", "Dispatcher pausiert komplett — nichts wird mehr gestartet.", "/runner/global-pause"],
  ["strict_success", "Strenge Abnahme (alle Nutzer)", "Lauf gilt nur als Erfolg, wenn der Verify-Befehl grün ist.", "/strict-success"],
];

export default function PreferencesPanel({ isAdmin }: { isAdmin: boolean }) {
  const qc = useQueryClient();
  const { data: flags } = useQuery({ queryKey: ["flags"], queryFn: () => api.get<any>("/me/flags") });
  const inv = () => qc.invalidateQueries({ queryKey: ["flags"] });

  const [runners, setRunners] = useState(3);
  const [chat, setChat] = useState("");
  const [notify, setNotify] = useState("needed");
  const [gedaechtnis, setGedaechtnis] = useState("");
  const [nightStart, setNightStart] = useState(22);
  const [nightEnd, setNightEnd] = useState(6);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    if (!flags) return;
    setRunners(flags.max_runners ?? 3);
    setChat(flags.telegram_chat_id || "");
    setNotify(flags.assistant_notify || "needed");
    setGedaechtnis(flags.vault_memory_path || "");
    setNightStart(flags.night_start_hour ?? 22);
    setNightEnd(flags.night_end_hour ?? 6);
  }, [flags]);

  const toggle = async (path: string, on: boolean) => {
    on ? await api.post(path) : await api.del(path);
    inv();
  };
  const flash = (t: string) => { setMsg(t); setTimeout(() => setMsg(""), 2500); };

  const toggleDay = async (idx: number) => {
    const cur: number[] = flags?.night_days || [0, 1, 2, 3, 4, 5, 6];
    const next = cur.includes(idx) ? cur.filter((d) => d !== idx) : [...cur, idx].sort();
    await api.put("/me/night-window", { start_hour: nightStart, end_hour: nightEnd, days: next });
    inv();
  };

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-line bg-card p-4">
        <div className="mb-3 text-sm font-medium">Agenten-Betrieb</div>
        <div className="mb-3 flex items-center gap-3 text-sm">
          <label className="text-muted">Gleichzeitige Läufe (max. 20)</label>
          <input type="number" min={1} max={20} value={runners}
            onChange={(e) => setRunners(+e.target.value)}
            className="w-20 rounded border border-line bg-surface px-2 py-1" />
          <button onClick={async () => { await api.put("/me/runner-limit", { value: runners }); inv(); flash("Limit gespeichert."); }}
            className="rounded bg-brand px-3 py-1 text-white">Speichern</button>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <label className="text-muted">Telegram-Chat-ID</label>
          <input value={chat} onChange={(e) => setChat(e.target.value)} placeholder="z. B. 123456789"
            className="w-48 rounded border border-line bg-surface px-2 py-1" />
          <button onClick={async () => { await api.put("/me/telegram-chat", { value: chat }); inv(); flash("Chat-ID gespeichert."); }}
            className="rounded bg-brand px-3 py-1 text-white">Speichern</button>
        </div>
      </div>

      <div className="rounded-lg border border-line bg-card p-4">
        <div className="mb-1 text-sm font-medium">Meldungen des Assistenten</div>
        <p className="mb-3 text-xs text-muted">
          Der persönliche Assistent arbeitet den Posteingang still ab. Ob er sich meldet,
          entscheidet er selbst: nur wenn du etwas wissen musst oder willst (Frist, Betrag,
          Entscheidung, Störung). Das Ergebnis jedes Laufs steht ohnehin im Posteingang.
        </p>
        <div className="flex items-center gap-3 text-sm">
          <select value={notify} onChange={async (e) => {
            setNotify(e.target.value);
            await api.put("/me/assistant-notify", { value: e.target.value });
            inv(); flash("Gespeichert.");
          }} className="rounded border border-line bg-surface px-2 py-1">
            <option value="needed">Nur wenn ich etwas wissen muss (empfohlen)</option>
            <option value="always">Jeder erledigte Eingang</option>
            <option value="errors">Nur Pannen</option>
            <option value="never">Gar nicht</option>
          </select>
        </div>
        <p className="mt-2 text-[11px] text-muted">
          Fragen, die du im Chat stellst, werden immer beantwortet.
        </p>
      </div>

      <div className="rounded-lg border border-line bg-card p-4">
        <div className="mb-1 text-sm font-medium">Gedächtnis der Agenten</div>
        <p className="mb-3 text-xs text-muted">
          Ordner in deinem Obsidian-Vault, in dem die Agenten festhalten, was du ihnen beibringst —
          damit du es nicht wiederholen musst. Sie legen dort <code>Mensch.md</code>,{" "}
          <code>Agent-&lt;rolle&gt;.md</code> und <code>Projekt-&lt;KEY&gt;.md</code> an und lesen
          es zu Beginn jedes Laufs. Du kannst die Notizen jederzeit selbst korrigieren.
        </p>
        <div className="flex items-center gap-3 text-sm">
          <input value={gedaechtnis} onChange={(e) => setGedaechtnis(e.target.value)}
            placeholder="z. B. 04 Traccoon/Gedächtnis"
            className="w-72 rounded border border-line bg-surface px-2 py-1" />
          <button onClick={async () => {
            await api.put("/me/vault-memory-path", { value: gedaechtnis });
            inv(); flash(gedaechtnis ? "Gedächtnis-Ordner gespeichert." : "Gedächtnis abgeschaltet.");
          }} className="rounded bg-brand px-3 py-1 text-white">Speichern</button>
        </div>
        <p className="mt-2 text-[11px] text-muted">
          Leer = kein Gedächtnis. Der Zugriff läuft über deine eigene MCP-Gruppe, das Gelernte
          bleibt also persönlich.
        </p>
      </div>

      <div className="rounded-lg border border-line bg-card p-4">
        <div className="mb-1 text-sm font-medium">Nacht-Fenster</div>
        <p className="mb-3 text-xs text-muted">Tickets mit der Markierung „Nachtarbeit" starten nur in diesem Fenster.</p>
        <div className="mb-3 flex items-center gap-2 text-sm">
          <input type="number" min={0} max={23} value={nightStart} onChange={(e) => setNightStart(+e.target.value)}
            className="w-16 rounded border border-line bg-surface px-2 py-1" />
          <span className="text-muted">bis</span>
          <input type="number" min={0} max={23} value={nightEnd} onChange={(e) => setNightEnd(+e.target.value)}
            className="w-16 rounded border border-line bg-surface px-2 py-1" />
          <span className="text-muted">Uhr</span>
          <button onClick={async () => {
            await api.put("/me/night-window", { start_hour: nightStart, end_hour: nightEnd, days: flags?.night_days || [0, 1, 2, 3, 4, 5, 6] });
            inv(); flash("Fenster gespeichert.");
          }} className="rounded bg-brand px-3 py-1 text-white">Speichern</button>
        </div>
        <div className="mb-3 flex gap-1">
          {DAYS.map((d, i) => (
            <button key={d} onClick={() => toggleDay(i)}
              className={`rounded px-2 py-1 text-xs ${(flags?.night_days || []).includes(i)
                ? "bg-brand text-white" : "border border-line text-muted"}`}>{d}</button>
          ))}
        </div>
        <Toggle label="Fenster ignorieren" hint="Nacht-Tickets laufen jederzeit."
          on={!!flags?.night_override}
          onChange={async (v) => { await api.put("/me/night-override", { active: v }); inv(); }} />
      </div>

      <div className="rounded-lg border border-line bg-card p-4">
        <div className="mb-3 text-sm font-medium">Meine Schalter</div>
        <div className="space-y-2">
          {USER_FLAGS.map(([key, label, hint]) => (
            <Toggle key={key} label={label} hint={hint} on={!!flags?.[key]}
              onChange={(v) => toggle(`/me/${key.replace(/_/g, "-")}`, v)} />
          ))}
        </div>
      </div>

      {isAdmin && (
        <div className="rounded-lg border border-yellow-500/40 bg-card p-4">
          <div className="mb-3 text-sm font-medium text-yellow-400">Systemweit (Administration)</div>
          <div className="space-y-2">
            {GLOBAL_FLAGS.map(([key, label, hint, path]) => (
              <Toggle key={key} label={label} hint={hint} on={!!flags?.[key]}
                onChange={(v) => toggle(path, v)} />
            ))}
          </div>
        </div>
      )}
      {msg && <div className="text-sm text-green-400">{msg}</div>}
    </div>
  );
}

function Toggle({ label, hint, on, onChange }: {
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
