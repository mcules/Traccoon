import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, pluginApi, type PluginVerwaltung } from "../api";
import { tr } from "../i18n";
import {
  Aktionen, Bereich, Etikett, Fehlerzeile, ICON, IconKnopf, Knopf, Liste, ListeLeer,
  ListenZeile, LoeschDialog } from "./ui";

/**
 * Plugins verwalten: einspielen, freigeben, abschalten.
 *
 * Der Kern dieser Seite sind die Haken bei den Rechten. Ein Plugin läuft im Browser eines
 * angemeldeten Menschen; was es an Traccoon-Daten sehen darf, kann deshalb nicht das Plugin
 * selbst bestimmen. Sein Manifest *fordert* — hier *erlaubt* jemand. Ohne Haken antwortet
 * die Brücke im Wirt mit einer Sperre, egal was im Zip steht.
 */

/** Wofür ein Recht steht, in einem Satz. Unbekannte zeigt die Liste roh — besser der nackte
 *  Bezeichner als eine erfundene Beschreibung. */
const RECHT_TEXT: Record<string, string> = {
  "series:number": "plugins.recht_series_number",
  "series:location": "plugins.recht_series_location",
  "series:text": "plugins.recht_series_text",
};

export default function PluginsPanel() {
  const qc = useQueryClient();
  const [err, setErr] = useState("");
  const [loeschZiel, setLoeschZiel] = useState<PluginVerwaltung | null>(null);
  const dateiFeld = useRef<HTMLInputElement>(null);

  const { data: plugins } = useQuery({
    queryKey: ["plugins", "alle"], queryFn: () => pluginApi.alle(),
  });
  // Beide Listen erneuern: Die Verwaltung sieht alles, die Bereichsschiene nur das
  // Freigegebene — nach einem Häkchen muss auch sie sich rühren.
  const inv = () => qc.invalidateQueries({ queryKey: ["plugins"] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.fehler"));

  const hochladen = useMutation({
    mutationFn: (datei: File) => pluginApi.hochladen(datei),
    onSuccess: () => { setErr(""); inv(); }, onError: fail,
  });
  const rechte = useMutation({
    mutationFn: ({ slug, body }: { slug: string; body: any }) => pluginApi.rechte(slug, body),
    onSuccess: () => { setErr(""); inv(); }, onError: fail,
  });
  const loeschen = useMutation({
    mutationFn: (slug: string) => pluginApi.del(slug),
    onSuccess: () => { setLoeschZiel(null); inv(); }, onError: fail,
  });

  return (
    <Bereich titel={tr("plugins.titel")} hinweis={tr("plugins.einleitung")} werkzeuge={
      <>
        <input ref={dateiFeld} type="file" accept=".zip" className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) hochladen.mutate(f);
            e.target.value = "";
          }} />
        <Knopf art="haupt" laeuft={hochladen.isPending}
          onClick={() => dateiFeld.current?.click()}>{tr("plugins.hochladen")}</Knopf>
      </>
    }>
      <Fehlerzeile text={err} />
      <Liste>
        {(plugins || []).map((p) => (
          <ListenZeile key={p.slug} gedimmt={!p.enabled}>
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <span className="text-lg leading-none">{p.icon || "\u{1F9E9}"}</span>
                <span className="font-medium text-ink">{p.name}</span>
                <code className="font-mono text-xs text-muted">{p.slug} · {p.version}</code>
                {!p.enabled && <Etikett farbe="neutral">{tr("plugins.aus")}</Etikett>}
                <div className="flex-1" />
                <Aktionen>
                  <Knopf klein art={p.enabled ? "neben" : "zusage"}
                    onClick={() => rechte.mutate({ slug: p.slug, body: { enabled: !p.enabled } })}>
                    {p.enabled ? tr("plugins.abschalten") : tr("plugins.einschalten")}
                  </Knopf>
                  <IconKnopf icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr
                    onClick={() => setLoeschZiel(p)} />
                </Aktionen>
              </div>
              {p.description && <div className="text-xs text-muted">{p.description}</div>}
              <Rechte plugin={p} onSetzen={(liste) =>
                rechte.mutate({ slug: p.slug, body: { reads_granted: liste } })} />
              {(p.allowed_hosts || []).length > 0 && (
                <div className="text-xs text-muted">
                  {tr("plugins.fremde_quellen")}: {p.allowed_hosts.join(", ")}
                </div>
              )}
            </div>
          </ListenZeile>
        ))}
        {plugins?.length === 0 && <ListeLeer>{tr("plugins.keine")}</ListeLeer>}
      </Liste>

      {loeschZiel && (
        <LoeschDialog was={loeschZiel.name} hinweis={tr("plugins.loeschen_hinweis")}
          laeuft={loeschen.isPending}
          onClose={() => setLoeschZiel(null)}
          onLoeschen={() => loeschen.mutate(loeschZiel.slug)} />
      )}
    </Bereich>
  );
}

/** Die Haken: was das Manifest fordert, und was davon gilt. */
function Rechte({ plugin, onSetzen }: {
  plugin: PluginVerwaltung; onSetzen: (liste: string[]) => void;
}) {
  const gefordert = plugin.reads || [];
  if (gefordert.length === 0) {
    return <div className="text-xs text-muted">{tr("plugins.keine_rechte")}</div>;
  }
  const erlaubt = plugin.reads_granted || [];
  const umschalten = (recht: string) =>
    onSetzen(erlaubt.includes(recht) ? erlaubt.filter((r) => r !== recht) : [...erlaubt, recht]);

  return (
    <div className="space-y-1">
      <div className="text-xs text-muted">{tr("plugins.rechte")}</div>
      {gefordert.map((recht) => (
        <label key={recht} className="flex cursor-pointer items-center gap-2 text-sm text-ink">
          <input type="checkbox" checked={erlaubt.includes(recht)}
            onChange={() => umschalten(recht)} />
          <code className="font-mono text-xs text-brand">{recht}</code>
          <span className="text-xs text-muted">
            {RECHT_TEXT[recht] ? tr(RECHT_TEXT[recht]) : ""}
          </span>
        </label>
      ))}
    </div>
  );
}
