import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import { Aktionen, ICON, IconKnopf, LoeschDialog, Bereich, Fehlerzeile, Liste, ListeLeer, ListenZeile} from "./ui";
import { alleSchluessel, ausgeliefert, QUELLSPRACHE, setzeSprache, sprache, tr } from "../i18n";

interface SpracheInfo {
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
  const [suche, setSuche] = useState("");
  const [nurOffene, setNurOffene] = useState(true);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");

  // Two sources, one list: the browser brings its catalog along, and the texts of the server
  // (notifications, setup steps) are known only to the server. Without the second part half
  // the application would stay German while the other half switches.
  const { data: serverTexte } = useQuery({
    queryKey: ["i18n-server-catalog", locale],
    queryFn: () => api.get<{ texts: Record<string, string>; shipped: Record<string, string> }>(
      `/i18n/server-catalog?locale=${locale}`),
  });
  const quelle = { ...alleSchluessel(), ...(serverTexte?.texts || {}) };
  // The shipped translation comes from the server as well; otherwise its texts would count
  // as open here although they have long been translated.
  const geliefertAlle = (l: string) => (
    l === locale ? { ...ausgeliefert(l), ...(serverTexte?.shipped || {}) } : ausgeliefert(l));
  const { data: sprachen } = useQuery({
    queryKey: ["i18n-locales"],
    queryFn: () => api.get<SpracheInfo[]>("/i18n/locales"),
  });
  const { data: overrides } = useQuery({
    queryKey: ["i18n", locale],
    queryFn: () => api.get<{ locale: string; texts: Record<string, string> }>(`/i18n/${locale}`),
  });

  const speichern = useMutation({
    mutationFn: ({ key, text }: { key: string; text: string }) =>
      api.put(`/i18n/${locale}/${encodeURIComponent(key)}`, { text }),
    onSuccess: () => {
      setErr("");
      qc.invalidateQueries({ queryKey: ["i18n", locale] });
      qc.invalidateQueries({ queryKey: ["i18n-locales"] });
      if (locale === sprache()) void setzeSprache(locale);
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Fehler"),
  });

  const einspielen = useMutation({
    mutationFn: (texte: Record<string, string>) =>
      api.post(`/i18n/${locale}/import`, { texte, ersetzen: false }),
    onSuccess: (r: any) => {
      setErr(""); setOk(tr("translations_panel.texte_uebernommen", { anzahl: r.imported }));
      qc.invalidateQueries({ queryKey: ["i18n", locale] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Import fehlgeschlagen"),
  });

  const zeilen = useMemo(() => {
    const geliefert = geliefertAlle(locale);
    const eigene = overrides?.texts || {};
    return Object.entries(quelle)
      .map(([key, deutsch]) => ({
        key, deutsch,
        wert: eigene[key] ?? geliefert[key] ?? "",
        geaendert: key in eigene,
      }))
      .filter((z) => !nurOffene || !z.wert)
      .filter((z) => !suche.trim()
        || z.key.toLowerCase().includes(suche.toLowerCase())
        || z.deutsch.toLowerCase().includes(suche.toLowerCase()));
  }, [quelle, overrides, locale, nurOffene, suche]);

  const offen = useMemo(() => {
    const geliefert = geliefertAlle(locale);
    const eigene = overrides?.texts || {};
    return Object.keys(quelle).filter((k) => !(eigene[k] ?? geliefert[k])).length;
  }, [quelle, overrides, locale]);

  const exportieren = () => {
    const geliefert = geliefertAlle(locale);
    const eigene = overrides?.texts || {};
    const alles: Record<string, string> = {};
    Object.keys(quelle).forEach((k) => { alles[k] = eigene[k] ?? geliefert[k] ?? ""; });
    const blob = new Blob([JSON.stringify(alles, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `traccoon-${locale}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const importieren = async (datei: File) => {
    try {
      const daten = JSON.parse(await datei.text());
      if (daten && typeof daten === "object") einspielen.mutate(daten);
    } catch {
      setErr(tr("translations_panel.kein_json"));
    }
  };

  const inp = "rounded border border-line bg-surface px-2 py-1 text-sm text-ink";

  return (
    <Bereich hinweis={tr("translations_panel.einleitung")} werkzeuge={<>
        <select value={locale} onChange={(e) => setLocale(e.target.value)} className={inp}>
          {(sprachen || []).filter((s) => s.locale !== QUELLSPRACHE).map((s) => (
            <option key={s.locale} value={s.locale}>
              {s.name} ({s.locale}){s.builtin ? ` · ${tr("translations_panel.ausgeliefert")}` : ""}
            </option>
          ))}
        </select>
        <input value={suche} onChange={(e) => setSuche(e.target.value)}
          placeholder={tr("translations_panel.suchen_schluessel_oder_deutscher_text")} className={`${inp} min-w-56 flex-1`} />
        <label className="flex items-center gap-1.5 text-xs text-muted">
          <input type="checkbox" checked={nurOffene} onChange={(e) => setNurOffene(e.target.checked)} />
          {tr("translations_panel.nur_offene")}
        </label>
        <span className="text-xs text-muted">
          {tr("translations_panel.offen_von", { offen, gesamt: Object.keys(quelle).length })}
        </span>
        <div className="flex-1" />
        <button onClick={exportieren}
          className="rounded border border-line px-2 py-1 text-xs text-ink hover:bg-surface">
          Export
        </button>
        <label className="cursor-pointer rounded border border-line px-2 py-1 text-xs text-ink hover:bg-surface">
          Import
          <input type="file" accept="application/json" className="hidden"
            onChange={(e) => e.target.files?.[0] && importieren(e.target.files[0])} />
        </label>
      </>}>
      <Sprachverwaltung sprachen={sprachen || []} gewaehlt={locale} onWaehlen={setLocale}
        onFehler={setErr} onOk={setOk} />

      <Fehlerzeile text={err} />
      {ok && <div className="text-xs text-green-400">{ok}</div>}

      {/* Keine Tabelle: drei Spalten (Schlüssel, deutsche Quelle, Übersetzung) sind auf einem
          Handy nicht zu halten — der Schlüssel allein ist breiter als der Bildschirm. Ab sm
          stehen Quelle und Feld nebeneinander, darunter untereinander. */}
      <div className="max-h-[60vh] overflow-auto">
      <Liste>
        {zeilen.map((z) => (
          <ListenZeile key={z.key}>
            <div className="break-all font-mono text-[11px] text-muted">{z.key}</div>
            <div className="mt-1 gap-2 sm:flex">
              <div className="min-w-0 flex-1 text-ink">{z.deutsch}</div>
              <input
                defaultValue={z.wert}
                placeholder={z.deutsch}
                onBlur={(e) => {
                  if (e.target.value !== z.wert) {
                    speichern.mutate({ key: z.key, text: e.target.value });
                  }
                }}
                className={`mt-1 w-full rounded border bg-card px-1.5 py-1 text-ink sm:mt-0 sm:flex-1 ${
                  z.geaendert ? "border-brand" : "border-line"}`} />
            </div>
          </ListenZeile>
        ))}
        {!zeilen.length && (
          <ListeLeer>
            {tr(nurOffene ? "translations_panel.nichts_offen" : "translations_panel.kein_treffer")}
          </ListeLeer>
        )}
      </Liste>
      </div>
    </Bereich>
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
function Sprachverwaltung({ sprachen, gewaehlt, onWaehlen, onFehler, onOk }: {
  sprachen: SpracheInfo[]; gewaehlt: string; onWaehlen: (l: string) => void;
  onFehler: (t: string) => void; onOk: (t: string) => void;
}) {
  const qc = useQueryClient();
  const [kennung, setKennung] = useState("");
  const [name, setName] = useState("");
  const [loeschSprache, setLoeschSprache] = useState<SpracheInfo | null>(null);
  const inp = "rounded border border-line bg-surface px-2 py-1 text-xs text-ink";
  const frisch = () => qc.invalidateQueries({ queryKey: ["i18n-locales"] });
  const fehler = (e: unknown) => onFehler(e instanceof ApiError ? e.message : tr("common.fehler"));

  const anlegen = useMutation({
    mutationFn: () => api.post("/i18n/locales", { locale: kennung.trim().toLowerCase(), name: name.trim() }),
    onSuccess: () => {
      onOk(tr("translations_panel.sprache_angelegt", { sprache: kennung.trim().toLowerCase() }));
      onWaehlen(kennung.trim().toLowerCase());
      setKennung(""); setName(""); frisch();
    },
    onError: fehler,
  });
  const aendern = useMutation({
    mutationFn: ({ locale, body }: { locale: string; body: Record<string, unknown> }) =>
      api.put(`/i18n/locales/${locale}`, body),
    onSuccess: () => { onFehler(""); frisch(); }, onError: fehler,
  });
  const loeschen = useMutation({
    mutationFn: (locale: string) => api.del(`/i18n/locales/${locale}`),
    onSuccess: () => { onFehler(""); onWaehlen("en"); frisch(); }, onError: fehler,
  });

  return (
    <div className="space-y-2 border-t border-line pt-2">
      <div className="text-xs font-medium text-muted">{tr("translations_panel.sprachen")}</div>
      <div className="text-xs">
        {sprachen.map((s) => (
            <div key={s.locale} className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line/60 py-1.5">
              <span className="font-mono text-[11px] text-muted">{s.locale}</span>
              <input defaultValue={s.name}
                onBlur={(e) => e.target.value !== s.name
                  && aendern.mutate({ locale: s.locale, body: { name: e.target.value } })}
                className={`${inp} w-36`} />
              <span className="text-muted">
                {s.builtin ? tr("translations_panel.ausgeliefert") : tr("translations_panel.eigene")}
                {" · "}
                {tr("translations_panel.own_texts", { anzahl: s.own_texts })}
              </span>
              {s.locale !== QUELLSPRACHE && (
                <label className="flex items-center gap-1 text-muted">
                  <input type="checkbox" checked={s.enabled}
                    onChange={(e) => aendern.mutate({ locale: s.locale, body: { enabled: e.target.checked } })} />
                  {tr("translations_panel.waehlbar")}
                </label>
              )}
              <div className="ml-auto">
                <Aktionen>
                  <IconKnopf icon={ICON.bearbeiten} aktiv={s.locale === gewaehlt}
                    titel={s.locale === gewaehlt ? tr("translations_panel.in_bearbeitung") : tr("translations_panel.bearbeiten")}
                    disabled={s.locale === QUELLSPRACHE} onClick={() => onWaehlen(s.locale)} />
                  {s.locale !== QUELLSPRACHE && (
                    <IconKnopf icon={ICON.loeschen} titel={tr("translations_panel.loeschen_titel")} gefahr
                      onClick={() => setLoeschSprache(s)} />
                  )}
                </Aktionen>
              </div>
            </div>
          ))}
        </div>

      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-muted">{tr("translations_panel.neue_sprache")}</span>
        <input value={kennung} onChange={(e) => setKennung(e.target.value)}
          placeholder={tr("translations_panel.kennung_platzhalter")} className={`${inp} w-24`} />
        <input value={name} onChange={(e) => setName(e.target.value)}
          placeholder={tr("translations_panel.name_platzhalter")} className={`${inp} w-40`} />
        <button onClick={() => kennung.trim() && anlegen.mutate()}
          className="rounded border border-line px-2 py-1 text-ink hover:bg-surface">
          {tr("translations_panel.anlegen")}
        </button>
      </div>
      {loeschSprache && (
        <LoeschDialog was={loeschSprache.name} hinweis={tr("translations_panel.loeschen_titel")}
          laeuft={loeschen.isPending}
          onClose={() => setLoeschSprache(null)}
          onLoeschen={() => { loeschen.mutate(loeschSprache.locale); setLoeschSprache(null); }} />
      )}
    </div>
  );
}
