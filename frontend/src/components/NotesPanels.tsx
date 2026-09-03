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
