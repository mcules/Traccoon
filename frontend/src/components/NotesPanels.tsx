import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { tr } from "../i18n";
import { api } from "../api";
import { BUTTON_SMALL } from "./ui";

/**
 * The note area's settings, on the account page with everything else personal.
 *
 * The notes used to carry a settings dialog of their own, with a login, a set of
 * API keys and a theme in it — all three of which this house already has one
 * floor up. What is left is what only the notes know about, and it belongs to
 * whoever reads them.
 */

const CARD = "rounded-lg border border-line bg-card p-4";
const FIELD = "rounded border border-line bg-surface px-2 py-1";

type Prefs = {
  trash: string;
  delete_mode: string;
  default_view: string;
  search_fuzzy: number;
  search_prefix: boolean;
  folder_colours: string;
  folder_colour_opacity: number;
};

type Calendar = {
  id: number;
  name: string;
  url: string;
  link_target: string;
  auth_user: string;
  has_password: boolean;
  enabled: boolean;
  position: number;
};

/** Short green confirmation under a panel; disappears on its own. */
function useNotice(): [string, (t: string) => void] {
  const [msg, setMsg] = useState("");
  return [msg, (t: string) => { setMsg(t); setTimeout(() => setMsg(""), 2500); }];
}

export function NotesPrefsPanel() {
  const qc = useQueryClient();
  const [msg, flash] = useNotice();
  const { data } = useQuery({
    queryKey: ["notes-prefs"],
    queryFn: () => api.get<Prefs>("/notes-native/prefs"),
  });
  const [draft, setDraft] = useState<Prefs | null>(null);
  useEffect(() => { if (data) setDraft(data); }, [data]);

  if (!draft) return null;

  const save = async (change: Partial<Prefs>) => {
    setDraft({ ...draft, ...change });
    await api.put("/notes-native/prefs", change);
    qc.invalidateQueries({ queryKey: ["notes-prefs"] });
    flash(tr("account.saved"));
  };

  return (
    <div className={CARD}>
      <div className="mb-1 text-sm font-medium">{tr("notes_prefs.title")}</div>
      <p className="mb-3 text-xs text-muted">{tr("notes_prefs.hint")}</p>

      <div className="space-y-3 text-sm">
        <label className="flex flex-wrap items-center gap-2">
          <span className="w-44">{tr("notes_prefs.default_view")}</span>
          <select value={draft.default_view} className={FIELD}
                  onChange={(e) => save({ default_view: e.target.value })}>
            <option value="live">{tr("notes_prefs.view_live")}</option>
            <option value="source">{tr("notes_prefs.view_source")}</option>
            <option value="reading">{tr("notes_prefs.view_reading")}</option>
          </select>
        </label>

        <label className="flex flex-wrap items-center gap-2">
          <span className="w-44">{tr("notes_prefs.delete_mode")}</span>
          <select value={draft.delete_mode} className={FIELD}
                  onChange={(e) => save({ delete_mode: e.target.value })}>
            <option value="trash">{tr("notes_prefs.delete_to_trash")}</option>
            <option value="permanent">{tr("notes_prefs.delete_for_good")}</option>
          </select>
        </label>

        <label className="flex flex-wrap items-center gap-2">
          <span className="w-44">{tr("notes_prefs.trash_folder")}</span>
          <input value={draft.trash} className={`${FIELD} w-40`}
                 onChange={(e) => setDraft({ ...draft, trash: e.target.value })}
                 onBlur={(e) => save({ trash: e.target.value })} />
          <span className="text-xs text-muted">{tr("notes_prefs.trash_hint")}</span>
        </label>

        <label className="flex flex-wrap items-center gap-2">
          <span className="w-44">{tr("notes_prefs.search_fuzzy")}</span>
          <input type="range" min={0} max={0.5} step={0.05} value={draft.search_fuzzy}
                 onChange={(e) => setDraft({ ...draft, search_fuzzy: Number(e.target.value) })}
                 onMouseUp={(e) => save({ search_fuzzy: Number((e.target as HTMLInputElement).value) })}
                 onTouchEnd={(e) => save({ search_fuzzy: Number((e.target as HTMLInputElement).value) })} />
          <span className="text-xs text-muted">
            {draft.search_fuzzy === 0 ? tr("notes_prefs.fuzzy_exact")
              : tr("notes_prefs.fuzzy_share", { share: Math.round(draft.search_fuzzy * 100) })}
          </span>
        </label>

        <label className="flex items-center gap-2">
          <input type="checkbox" checked={draft.search_prefix}
                 onChange={(e) => save({ search_prefix: e.target.checked })} />
          <span>{tr("notes_prefs.search_prefix")}</span>
        </label>

        <label className="flex flex-wrap items-center gap-2">
          <span className="w-44">{tr("notes_prefs.folder_colours")}</span>
          <select value={draft.folder_colours} className={FIELD}
                  onChange={(e) => save({ folder_colours: e.target.value })}>
            <option value="">{tr("notes_prefs.colours_as_vault")}</option>
            <option value="off">{tr("notes_prefs.colours_off")}</option>
            <option value="default">{tr("notes_prefs.colours_arrow_and_name")}</option>
            <option value="simple">{tr("notes_prefs.colours_arrow_only")}</option>
            <option value="full">{tr("notes_prefs.colours_whole_row")}</option>
          </select>
        </label>
      </div>
      {msg && <div className="mt-2 text-sm text-green-400">{msg}</div>}
    </div>
  );
}

const EMPTY: Omit<Calendar, "id" | "has_password"> & { auth_password: string } = {
  name: "", url: "", link_target: "", auth_user: "", auth_password: "",
  enabled: true, position: 0,
};

export function NotesCalendarsPanel() {
  const qc = useQueryClient();
  const [msg, flash] = useNotice();
  const [draft, setDraft] = useState(EMPTY);
  const [error, setError] = useState("");
  const { data } = useQuery({
    queryKey: ["notes-calendars"],
    queryFn: () => api.get<{ calendars: Calendar[] }>("/notes-native/calendars"),
  });
  const reload = () => qc.invalidateQueries({ queryKey: ["notes-calendars"] });

  const add = async () => {
    setError("");
    try {
      await api.post("/notes-native/calendars", { ...draft, position: (data?.calendars.length ?? 0) });
      setDraft(EMPTY);
      reload();
      flash(tr("account.saved"));
    } catch (e: any) {
      setError(e?.message || tr("common.save_failed"));
    }
  };

  return (
    <div className={CARD}>
      <div className="mb-1 text-sm font-medium">{tr("notes_calendars.title")}</div>
      <p className="mb-3 text-xs text-muted">{tr("notes_calendars.hint")}</p>

      <div className="space-y-2">
        {data?.calendars.map((c) => (
          <div key={c.id} className="flex flex-wrap items-center gap-2 rounded border border-line px-2 py-1 text-sm">
            <input type="checkbox" checked={c.enabled} title={tr("notes_calendars.fetch_it")}
                   onChange={async (e) => {
                     await api.patch(`/notes-native/calendars/${c.id}`, { enabled: e.target.checked });
                     reload();
                   }} />
            <span className="font-medium">{c.name || tr("notes_calendars.unnamed")}</span>
            <span className="min-w-0 flex-1 truncate text-xs text-muted" title={c.url}>{c.url}</span>
            {c.has_password && (
              <span className="text-xs text-muted" title={tr("notes_calendars.password_kept")}>🔒</span>
            )}
            <button className={BUTTON_SMALL.danger} onClick={async () => {
              await api.del(`/notes-native/calendars/${c.id}`);
              reload();
            }}>{tr("common.delete")}</button>
          </div>
        ))}
        {data && data.calendars.length === 0 && (
          <div className="text-xs text-muted">{tr("notes_calendars.none_yet")}</div>
        )}
      </div>

      <div className="mt-4 space-y-2 border-t border-line pt-3 text-sm">
        <div className="text-xs font-medium text-muted">{tr("notes_calendars.add")}</div>
        <input className={`${FIELD} w-full`} placeholder={tr("notes_calendars.name_placeholder")}
               value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
        <input className={`${FIELD} w-full`} placeholder={tr("notes_calendars.url_placeholder")}
               value={draft.url} onChange={(e) => setDraft({ ...draft, url: e.target.value })} />
        <input className={`${FIELD} w-full`} placeholder={tr("notes_calendars.link_placeholder")}
               value={draft.link_target}
               onChange={(e) => setDraft({ ...draft, link_target: e.target.value })} />
        <div className="flex flex-wrap gap-2">
          <input className={`${FIELD} flex-1`} placeholder={tr("notes_calendars.user_placeholder")}
                 value={draft.auth_user}
                 onChange={(e) => setDraft({ ...draft, auth_user: e.target.value })} />
          <input className={`${FIELD} flex-1`} type="password"
                 placeholder={tr("notes_calendars.password_placeholder")}
                 value={draft.auth_password}
                 onChange={(e) => setDraft({ ...draft, auth_password: e.target.value })} />
        </div>
        <button className={BUTTON_SMALL.primary} onClick={add} disabled={!draft.url.trim()}>
          {tr("notes_calendars.add_button")}
        </button>
        {error && <div className="text-sm text-red-400">{error}</div>}
      </div>
      {msg && <div className="mt-2 text-sm text-green-400">{msg}</div>}
    </div>
  );
}
