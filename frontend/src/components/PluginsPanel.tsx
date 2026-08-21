import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, pluginApi, type PluginVerwaltung } from "../api";
import { tr } from "../i18n";
import {
  Actions, Area, Etikett, Fehlerzeile, ICON, IconButton, Button, Listing, ListingLeer,
  ListenLine, LoeschDialog } from "./ui";

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
  const [loeschTarget, setLoeschTarget] = useState<PluginVerwaltung | null>(null);
  const fileField = useRef<HTMLInputElement>(null);

  const { data: plugins } = useQuery({
    queryKey: ["plugins", "alle"], queryFn: () => pluginApi.alle(),
  });
  // Beide Listen erneuern: Die Verwaltung sieht alles, die Bereichsschiene nur das
  // Freigegebene — nach einem Häkchen muss auch sie sich rühren.
  const inv = () => qc.invalidateQueries({ queryKey: ["plugins"] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.fehler"));

  const hochladen = useMutation({
    mutationFn: (file: File) => pluginApi.hochladen(file),
    onSuccess: () => { setErr(""); inv(); }, onError: fail,
  });
  const rechte = useMutation({
    mutationFn: ({ slug, body }: { slug: string; body: any }) => pluginApi.rechte(slug, body),
    onSuccess: () => { setErr(""); inv(); }, onError: fail,
  });
  const remove = useMutation({
    mutationFn: (slug: string) => pluginApi.del(slug),
    onSuccess: () => { setLoeschTarget(null); inv(); }, onError: fail,
  });

  return (
    <Area titel={tr("plugins.titel")} hinweis={tr("plugins.einleitung")} werkzeuge={
      <>
        <input ref={fileField} type="file" accept=".zip" className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) hochladen.mutate(f);
            e.target.value = "";
          }} />
        <Button art="haupt" laeuft={hochladen.isPending}
          onClick={() => fileField.current?.click()}>{tr("plugins.hochladen")}</Button>
      </>
    }>
      <Fehlerzeile text={err} />
      <Listing>
        {(plugins || []).map((p) => (
          <ListenLine key={p.slug} gedimmt={!p.enabled}>
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <span className="text-lg leading-none">{p.icon || "\u{1F9E9}"}</span>
                <span className="font-medium text-ink">{p.name}</span>
                <code className="font-mono text-xs text-muted">{p.slug} · {p.version}</code>
                {!p.enabled && <Etikett farbe="neutral">{tr("plugins.aus")}</Etikett>}
                <div className="flex-1" />
                <Actions>
                  <Button klein art={p.enabled ? "neben" : "zusage"}
                    onClick={() => rechte.mutate({ slug: p.slug, body: { enabled: !p.enabled } })}>
                    {p.enabled ? tr("plugins.abschalten") : tr("plugins.einschalten")}
                  </Button>
                  <IconButton icon={ICON.loeschen} titel={tr("common.loeschen")} gefahr
                    onClick={() => setLoeschTarget(p)} />
                </Actions>
              </div>
              {p.description && <div className="text-xs text-muted">{p.description}</div>}
              <Rechte plugin={p} onSetzen={(listing) =>
                rechte.mutate({ slug: p.slug, body: { reads_granted: listing } })} />
              {(p.allowed_hosts || []).length > 0 && (
                <div className="text-xs text-muted">
                  {tr("plugins.fremde_quellen")}: {p.allowed_hosts.join(", ")}
                </div>
              )}
            </div>
          </ListenLine>
        ))}
        {plugins?.length === 0 && <ListingLeer>{tr("plugins.keine")}</ListingLeer>}
      </Listing>

      {loeschTarget && (
        <LoeschDialog was={loeschTarget.name} hinweis={tr("plugins.loeschen_hinweis")}
          laeuft={remove.isPending}
          onClose={() => setLoeschTarget(null)}
          onLoeschen={() => remove.mutate(loeschTarget.slug)} />
      )}
    </Area>
  );
}

/** Die Haken: was das Manifest fordert, und was davon gilt. */
function Rechte({ plugin, onSetzen: onSet }: {
  plugin: PluginVerwaltung; onSetzen: (listing: string[]) => void;
}) {
  const gefordert = plugin.reads || [];
  if (gefordert.length === 0) {
    return <div className="text-xs text-muted">{tr("plugins.keine_rechte")}</div>;
  }
  const allowed = plugin.reads_granted || [];
  const umschalten = (recht: string) =>
    onSet(allowed.includes(recht) ? allowed.filter((r) => r !== recht) : [...allowed, recht]);

  return (
    <div className="space-y-1">
      <div className="text-xs text-muted">{tr("plugins.rechte")}</div>
      {gefordert.map((recht) => (
        <label key={recht} className="flex cursor-pointer items-center gap-2 text-sm text-ink">
          <input type="checkbox" checked={allowed.includes(recht)}
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
