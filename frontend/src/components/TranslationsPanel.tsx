import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api";
import { alleSchluessel, ausgeliefert, QUELLSPRACHE, setzeSprache, sprache, tr } from "../i18n";

interface SpracheInfo {
  locale: string; name: string; eigene_texte: number; eingebaut: boolean; enabled: boolean;
}

/**
 * Übersetzungen der Oberfläche verwalten.
 *
 * Die Schlüssel kommen aus dem ausgelieferten deutschen Katalog — er ist die Wahrheit
 * darüber, welche Texte es gibt. Danebengestellt wird, was die gewählte Sprache mitbringt
 * und was hier zur Laufzeit geändert wurde. Wer ein Feld leert, nimmt seine Änderung
 * zurück und bekommt den ausgelieferten Text wieder; nichts wird dadurch leer.
 *
 * Eine neue Sprache braucht keinen Code: Kennung eintragen, übersetzen, fertig. Sie lebt
 * vollständig in der Datenbank und steht sofort in der Sprachwahl des Profils.
 */
export default function TranslationsPanel() {
  const qc = useQueryClient();
  const [locale, setLocale] = useState("en");
  const [suche, setSuche] = useState("");
  const [nurOffene, setNurOffene] = useState(true);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");

  const quelle = alleSchluessel();
  const { data: sprachen } = useQuery({
    queryKey: ["i18n-locales"],
    queryFn: () => api.get<SpracheInfo[]>("/i18n/locales"),
  });
  const { data: overrides } = useQuery({
    queryKey: ["i18n", locale],
    queryFn: () => api.get<{ locale: string; texte: Record<string, string> }>(`/i18n/${locale}`),
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
      setErr(""); setOk(tr("translations_panel.texte_uebernommen", { anzahl: r.uebernommen }));
      qc.invalidateQueries({ queryKey: ["i18n", locale] });
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Import fehlgeschlagen"),
  });

  const zeilen = useMemo(() => {
    const geliefert = ausgeliefert(locale);
    const eigene = overrides?.texte || {};
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
    const geliefert = ausgeliefert(locale);
    const eigene = overrides?.texte || {};
    return Object.keys(quelle).filter((k) => !(eigene[k] ?? geliefert[k])).length;
  }, [quelle, overrides, locale]);

  const exportieren = () => {
    const geliefert = ausgeliefert(locale);
    const eigene = overrides?.texte || {};
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
    <div className="space-y-3 rounded-lg border border-line bg-card p-4">
      <p className="text-sm text-muted">
        {tr("translations_panel.einleitung")}
      </p>

      <div className="flex flex-wrap items-center gap-2 text-sm">
        <select value={locale} onChange={(e) => setLocale(e.target.value)} className={inp}>
          {(sprachen || []).filter((s) => s.locale !== QUELLSPRACHE).map((s) => (
            <option key={s.locale} value={s.locale}>
              {s.name} ({s.locale}){s.eingebaut ? ` · ${tr("translations_panel.ausgeliefert")}` : ""}
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
      </div>

      <Sprachverwaltung sprachen={sprachen || []} gewaehlt={locale} onWaehlen={setLocale}
        onFehler={setErr} onOk={setOk} />

      {err && <div className="rounded border border-red-500/40 bg-red-500/10 p-2 text-sm text-red-300">{err}</div>}
      {ok && <div className="text-xs text-green-400">{ok}</div>}

      {/* Keine Tabelle: drei Spalten (Schlüssel, deutsche Quelle, Übersetzung) sind auf einem
          Handy nicht zu halten — der Schlüssel allein ist breiter als der Bildschirm. Ab sm
          stehen Quelle und Feld nebeneinander, darunter untereinander. */}
      <div className="max-h-[60vh] divide-y divide-line/60 overflow-auto rounded border border-line text-xs">
        {zeilen.map((z) => (
          <div key={z.key} className="p-2">
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
                className={`mt-1 w-full rounded border bg-surface px-1.5 py-1 text-ink sm:mt-0 sm:flex-1 ${
                  z.geaendert ? "border-brand" : "border-line"}`} />
            </div>
          </div>
        ))}
        {!zeilen.length && (
          <div className="px-2 py-2 text-muted">
            {tr(nurOffene ? "translations_panel.nichts_offen" : "translations_panel.kein_treffer")}
          </div>
        )}
      </div>
    </div>
  );
}


/**
 * Sprachen anlegen, benennen, abschalten, löschen.
 *
 * Eine Sprache entstand vorher erst mit ihrem ersten Text — genau in dem Moment, in dem
 * jemand zu übersetzen anfängt, war sie also noch nicht da, und ein Neuladen verlor die
 * Auswahl. Jetzt ist sie ein eigener Eintrag: benannt („Français" statt „fr"), abschaltbar,
 * ohne dass ihre Texte verschwinden, und löschbar mitsamt allem.
 *
 * Die ausgelieferten Sprachen lassen sich nicht wegwerfen — ihr Katalog gehört zur
 * Anwendung. Löschen nimmt dort nur zurück, was hier geändert wurde.
 */
function Sprachverwaltung({ sprachen, gewaehlt, onWaehlen, onFehler, onOk }: {
  sprachen: SpracheInfo[]; gewaehlt: string; onWaehlen: (l: string) => void;
  onFehler: (t: string) => void; onOk: (t: string) => void;
}) {
  const qc = useQueryClient();
  const [kennung, setKennung] = useState("");
  const [name, setName] = useState("");
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
                {s.eingebaut ? tr("translations_panel.ausgeliefert") : tr("translations_panel.eigene")}
                {" · "}
                {tr("translations_panel.eigene_texte", { anzahl: s.eigene_texte })}
              </span>
              {s.locale !== QUELLSPRACHE && (
                <label className="flex items-center gap-1 text-muted">
                  <input type="checkbox" checked={s.enabled}
                    onChange={(e) => aendern.mutate({ locale: s.locale, body: { enabled: e.target.checked } })} />
                  {tr("translations_panel.waehlbar")}
                </label>
              )}
              <div className="ml-auto flex items-center gap-1">
                <button onClick={() => onWaehlen(s.locale)} disabled={s.locale === QUELLSPRACHE}
                  className="rounded border border-line px-2 py-0.5 text-ink hover:bg-surface disabled:opacity-40">
                  {s.locale === gewaehlt ? tr("translations_panel.in_bearbeitung") : tr("translations_panel.bearbeiten")}
                </button>
                {s.locale !== QUELLSPRACHE && (
                  <button
                    onClick={() => { if (confirm(tr("translations_panel.loeschen_frage", { sprache: s.name }))) loeschen.mutate(s.locale); }}
                    title={tr("translations_panel.loeschen_titel")}
                    className="rounded border border-line px-2 py-0.5 text-red-400 hover:bg-surface">✕</button>
                )}
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
    </div>
  );
}
