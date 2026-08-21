import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import { Actions, ICON, IconButton, DeleteDialog, Area, Errorrow, Listing, ListingEmpty, ListenLine, BUTTON_SMALL} from "./ui";
import { allKey, shipped, SOURCELANGUAGE, setLanguage, language, tr } from "../i18n";

interface LanguageInfo {
  locale: string; name: string; own_texts: number; builtin: boolean; enabled: boolean;
}

/**
 * Manage the translations of the interface.
 *
 * The keys come from the shipped German catalog: it is the truth about which texts exist.
 * Beside it stands what the chosen language brings along and what was changed here at
 * runtime. Whoever empties a field takes their change back and gets the shipped text again;
 * nothing becomes empty through that.
 *
 * A new language needs no code: enter the identifier, translate, done. It lives completely
 * in the database and stands in the language choice of the profile immediately.
 */
export default function TranslationsPanel() {
  const qc = useQueryClient();
  const [locale, setLocale] = useState("en");
  const [search, setSearch] = useState("");
  const [onlyOpen, setOnlyOpen] = useState(true);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");

  // Two sources, one list: the browser brings its catalog along, and the texts of the server
  // (notifications, setup steps) are known only to the server. Without the second part half
  // the application would stay German while the other half switches.
  const { data: serverTexts } = useQuery({
    queryKey: ["i18n-server-catalog", locale],
    queryFn: () => api.get<{ texts: Record<string, string>; shipped: Record<string, string> }>(
      `/i18n/server-catalog?locale=${locale}`),
  });
  const source = { ...allKey(), ...(serverTexts?.texts || {}) };
  // The shipped translation comes from the server as well; otherwise its texts would count
  // as open here although they have long been translated.
  const deliveredAll = (l: string) => (
    l === locale ? { ...shipped(l), ...(serverTexts?.shipped || {}) } : shipped(l));
  const { data: languages } = useQuery({
    queryKey: ["i18n-locales"],
    queryFn: () => api.get<LanguageInfo[]>("/i18n/locales"),
  });
  const { data: overrides } = useQuery({
    queryKey: ["i18n", locale],
    queryFn: () => api.get<{ locale: string; texts: Record<string, string> }>(`/i18n/${locale}`),
  });

  const save = useMutation({
    mutationFn: ({ key, text }: { key: string; text: string }) =>
      api.put(`/i18n/${locale}/${encodeURIComponent(key)}`, { text }),
    onSuccess: () => {
      setErr("");
      qc.invalidateQueries({ queryKey: ["i18n", locale] });
      qc.invalidateQueries({ queryKey: ["i18n-locales"] });
      if (locale === language()) void setLanguage(locale);
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });

  const feed = useMutation({
    mutationFn: (texts: Record<string, string>) =>
      api.post(`/i18n/${locale}/import`, { texts, replace: false }),
    onSuccess: (r: any) => {
      setErr(""); setOk(tr("translations_panel.count_texts_imported", { count: r.imported }));
      qc.invalidateQueries({ queryKey: ["i18n", locale] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Import fehlgeschlagen"),
  });

  const lines = useMemo(() => {
    const delivered = deliveredAll(locale);
    const own = overrides?.texts || {};
    return Object.entries(source)
      .map(([key, german]) => ({
        key, german,
        value: own[key] ?? delivered[key] ?? "",
        geaendert: key in own,
      }))
      .filter((z) => !onlyOpen || !z.value)
      .filter((z) => !search.trim()
        || z.key.toLowerCase().includes(search.toLowerCase())
        || z.german.toLowerCase().includes(search.toLowerCase()));
  }, [source, overrides, locale, onlyOpen, search]);

  const open = useMemo(() => {
    const delivered = deliveredAll(locale);
    const own = overrides?.texts || {};
    return Object.keys(source).filter((k) => !(own[k] ?? delivered[k])).length;
  }, [source, overrides, locale]);

  const exportItem = () => {
    const delivered = deliveredAll(locale);
    const own = overrides?.texts || {};
    const everything: Record<string, string> = {};
    Object.keys(source).forEach((k) => { everything[k] = own[k] ?? delivered[k] ?? ""; });
    const blob = new Blob([JSON.stringify(everything, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `traccoon-${locale}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const importItem = async (file: File) => {
    try {
      const data = JSON.parse(await file.text());
      if (data && typeof data === "object") feed.mutate(data);
    } catch {
      setErr(tr("translations_panel.file_not_valid_json"));
    }
  };

  const inp = "rounded border border-line bg-surface px-2 py-1 text-sm text-ink";

  return (
    <Area hint={tr("translations_panel.translations_keys_come_german")} tools={<>
        <select value={locale} onChange={(e) => setLocale(e.target.value)} className={inp}>
          {(languages || []).filter((s) => s.locale !== SOURCELANGUAGE).map((s) => (
            <option key={s.locale} value={s.locale}>
              {s.name} ({s.locale}){s.builtin ? ` · ${tr("translations_panel.shipped")}` : ""}
            </option>
          ))}
        </select>
        <input value={search} onChange={(e) => setSearch(e.target.value)}
          placeholder={tr("translations_panel.search_key_or_german_text")} className={`${inp} min-w-56 flex-1`} />
        <label className="flex items-center gap-1.5 text-xs text-muted">
          <input type="checkbox" checked={onlyOpen} onChange={(e) => setOnlyOpen(e.target.checked)} />
          {tr("translations_panel.open_only")}
        </label>
        <span className="text-xs text-muted">
          {tr("translations_panel.open_total_open", { open: open, total: Object.keys(source).length })}
        </span>
        <div className="flex-1" />
        <button onClick={exportItem}
          className={BUTTON_SMALL.secondary}>
          Export
        </button>
        <label className="cursor-pointer rounded border border-line px-2 py-1 text-xs text-ink hover:bg-surface">
          Import
          <input type="file" accept="application/json" className="hidden"
            onChange={(e) => e.target.files?.[0] && importItem(e.target.files[0])} />
        </label>
      </>}>
      <Languageadmin languages={languages || []} chosen={locale} onChoose={setLocale}
        onError={setErr} onOk={setOk} />

      <Errorrow text={err} />
      {ok && <div className="text-xs text-green-400">{ok}</div>}

      {/* Keine Tabelle: drei Spalten (Schlüssel, deutsche Quelle, Übersetzung) sind auf einem
          Handy nicht zu halten — der Schlüssel allein ist breiter als der Bildschirm. Ab sm
          stehen Quelle und Feld nebeneinander, darunter untereinander. */}
      <div className="max-h-[60vh] overflow-auto">
      <Listing>
        {lines.map((z) => (
          <ListenLine key={z.key}>
            <div className="break-all font-mono text-[11px] text-muted">{z.key}</div>
            <div className="mt-1 gap-2 sm:flex">
              <div className="min-w-0 flex-1 text-ink">{z.german}</div>
              <input
                defaultValue={z.value}
                placeholder={z.german}
                onBlur={(e) => {
                  if (e.target.value !== z.value) {
                    save.mutate({ key: z.key, text: e.target.value });
                  }
                }}
                className={`mt-1 w-full rounded border bg-card px-1.5 py-1 text-ink sm:mt-0 sm:flex-1 ${
                  z.geaendert ? "border-brand" : "border-line"}`} />
            </div>
          </ListenLine>
        ))}
        {!lines.length && (
          <ListingEmpty>
            {tr(onlyOpen ? "translations_panel.nothing_open_language" : "translations_panel.no_match")}
          </ListingEmpty>
        )}
      </Listing>
      </div>
    </Area>
  );
}


/**
 * Create, name, switch off and delete languages.
 *
 * A language used to come into being only with its first text, so exactly at the moment
 * somebody starts translating it did not exist yet, and a reload lost the selection. Now it
 * is an entry of its own: named ("Français" instead of "fr"), switchable off without its
 * texts disappearing, and deletable together with everything.
 *
 * The shipped languages cannot be thrown away: their catalog belongs to the application.
 * Deleting there only takes back what was changed here.
 */
function Languageadmin({ languages, chosen, onChoose, onError: onError, onOk }: {
  languages: LanguageInfo[]; chosen: string; onChoose: (l: string) => void;
  onError: (t: string) => void; onOk: (t: string) => void;
}) {
  const qc = useQueryClient();
  const [ident, setIdent] = useState("");
  const [name, setName] = useState("");
  const [deleteLanguage, setDeleteLanguage] = useState<LanguageInfo | null>(null);
  const inp = "rounded border border-line bg-surface px-2 py-1 text-xs text-ink";
  const fresh = () => qc.invalidateQueries({ queryKey: ["i18n-locales"] });
  const error = (e: unknown) => onError(e instanceof ApiError ? e.message : tr("common.error"));

  const create = useMutation({
    mutationFn: () => api.post("/i18n/locales", { locale: ident.trim().toLowerCase(), name: name.trim() }),
    onSuccess: () => {
      onOk(tr("translations_panel.language_language_created", { language: ident.trim().toLowerCase() }));
      onChoose(ident.trim().toLowerCase());
      setIdent(""); setName(""); fresh();
    },
    onError: error,
  });
  const update = useMutation({
    mutationFn: ({ locale, body }: { locale: string; body: Record<string, unknown> }) =>
      api.put(`/i18n/locales/${locale}`, body),
    onSuccess: () => { onError(""); fresh(); }, onError: error,
  });
  const remove = useMutation({
    mutationFn: (locale: string) => api.del(`/i18n/locales/${locale}`),
    onSuccess: () => { onError(""); onChoose("en"); fresh(); }, onError: error,
  });

  return (
    <div className="space-y-2 border-t border-line pt-2">
      <div className="text-xs font-medium text-muted">{tr("translations_panel.languages")}</div>
      <div className="text-xs">
        {languages.map((s) => (
            <div key={s.locale} className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line/60 py-1.5">
              <span className="font-mono text-[11px] text-muted">{s.locale}</span>
              <input defaultValue={s.name}
                onBlur={(e) => e.target.value !== s.name
                  && update.mutate({ locale: s.locale, body: { name: e.target.value } })}
                className={`${inp} w-36`} />
              <span className="text-muted">
                {s.builtin ? tr("translations_panel.shipped") : tr("translations_panel.created_here")}
                {" · "}
                {tr("translations_panel.count_own_texts", { count: s.own_texts })}
              </span>
              {s.locale !== SOURCELANGUAGE && (
                <label className="flex items-center gap-1 text-muted">
                  <input type="checkbox" checked={s.enabled}
                    onChange={(e) => update.mutate({ locale: s.locale, body: { enabled: e.target.checked } })} />
                  {tr("translations_panel.selectable")}
                </label>
              )}
              <div className="ml-auto">
                <Actions>
                  <IconButton icon={ICON.edit} active={s.locale === chosen}
                    title={s.locale === chosen ? tr("translations_panel.editing") : tr("translations_panel.edit")}
                    disabled={s.locale === SOURCELANGUAGE} onClick={() => onChoose(s.locale)} />
                  {s.locale !== SOURCELANGUAGE && (
                    <IconButton icon={ICON.remove} title={tr("translations_panel.deletes_changes_made_here")} danger
                      onClick={() => setDeleteLanguage(s)} />
                  )}
                </Actions>
              </div>
            </div>
          ))}
        </div>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-muted">{tr("translations_panel.new_language")}</span>
        <input value={ident} onChange={(e) => setIdent(e.target.value)}
          placeholder={tr("translations_panel.e_g_fr")} className={`${inp} w-24`} />
        <input value={name} onChange={(e) => setName(e.target.value)}
          placeholder={tr("translations_panel.name_e_g_fran")} className={`${inp} w-40`} />
        <button onClick={() => ident.trim() && create.mutate()}
          className={BUTTON_SMALL.secondary}>
          {tr("translations_panel.create")}
        </button>
      </div>
      {deleteLanguage && (
        <DeleteDialog was={deleteLanguage.name} hint={tr("translations_panel.deletes_changes_made_here")}
          runs={remove.isPending}
          onClose={() => setDeleteLanguage(null)}
          onDelete={() => { remove.mutate(deleteLanguage.locale); setDeleteLanguage(null); }} />
      )}
    </div>
  );
}
