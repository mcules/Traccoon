import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, pluginApi, type PluginAdmin } from "../api";
import { tr } from "../i18n";
import {
  Actions, Area, Tag, Errorrow, ICON, IconButton, Button, Listing, ListingEmpty,
  ListenLine, DeleteDialog } from "./ui";

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
const RIGHT_TEXT: Record<string, string> = {
  "series:number": "plugins.recht_series_number",
  "series:location": "plugins.recht_series_location",
  "series:text": "plugins.recht_series_text",
};

export default function PluginsPanel() {
  const qc = useQueryClient();
  const [err, setErr] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<PluginAdmin | null>(null);
  const fileField = useRef<HTMLInputElement>(null);

  const { data: plugins } = useQuery({
    queryKey: ["plugins", "alle"], queryFn: () => pluginApi.all(),
  });
  // Beide Listen erneuern: Die Verwaltung sieht alles, die Bereichsschiene nur das
  // Freigegebene — nach einem Häkchen muss auch sie sich rühren.
  const inv = () => qc.invalidateQueries({ queryKey: ["plugins"] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.fehler"));

  const upload = useMutation({
    mutationFn: (file: File) => pluginApi.upload(file),
    onSuccess: () => { setErr(""); inv(); }, onError: fail,
  });
  const rights = useMutation({
    mutationFn: ({ slug, body }: { slug: string; body: any }) => pluginApi.rights(slug, body),
    onSuccess: () => { setErr(""); inv(); }, onError: fail,
  });
  const remove = useMutation({
    mutationFn: (slug: string) => pluginApi.del(slug),
    onSuccess: () => { setDeleteTarget(null); inv(); }, onError: fail,
  });

  return (
    <Area title={tr("plugins.titel")} hint={tr("plugins.einleitung")} tools={
      <>
        <input ref={fileField} type="file" accept=".zip" className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) upload.mutate(f);
            e.target.value = "";
          }} />
        <Button variant="primary" runs={upload.isPending}
          onClick={() => fileField.current?.click()}>{tr("plugins.hochladen")}</Button>
      </>
    }>
      <Errorrow text={err} />
      <Listing>
        {(plugins || []).map((p) => (
          <ListenLine key={p.slug} dimmed={!p.enabled}>
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <span className="text-lg leading-none">{p.icon || "\u{1F9E9}"}</span>
                <span className="font-medium text-ink">{p.name}</span>
                <code className="font-mono text-xs text-muted">{p.slug} · {p.version}</code>
                {!p.enabled && <Tag color="neutral">{tr("plugins.aus")}</Tag>}
                <div className="flex-1" />
                <Actions>
                  <Button small variant={p.enabled ? "secondary" : "confirm"}
                    onClick={() => rights.mutate({ slug: p.slug, body: { enabled: !p.enabled } })}>
                    {p.enabled ? tr("plugins.abschalten") : tr("plugins.einschalten")}
                  </Button>
                  <IconButton icon={ICON.remove} title={tr("common.loeschen")} danger
                    onClick={() => setDeleteTarget(p)} />
                </Actions>
              </div>
              {p.description && <div className="text-xs text-muted">{p.description}</div>}
              <Rights plugin={p} onSet={(listing) =>
                rights.mutate({ slug: p.slug, body: { reads_granted: listing } })} />
              {(p.allowed_hosts || []).length > 0 && (
                <div className="text-xs text-muted">
                  {tr("plugins.fremde_quellen")}: {p.allowed_hosts.join(", ")}
                </div>
              )}
            </div>
          </ListenLine>
        ))}
        {plugins?.length === 0 && <ListingEmpty>{tr("plugins.keine")}</ListingEmpty>}
      </Listing>

      {deleteTarget && (
        <DeleteDialog was={deleteTarget.name} hint={tr("plugins.loeschen_hinweis")}
          runs={remove.isPending}
          onClose={() => setDeleteTarget(null)}
          onDelete={() => remove.mutate(deleteTarget.slug)} />
      )}
    </Area>
  );
}

/** Die Haken: was das Manifest fordert, und was davon gilt. */
function Rights({ plugin, onSet: onSet }: {
  plugin: PluginAdmin; onSet: (listing: string[]) => void;
}) {
  const requested = plugin.reads || [];
  if (requested.length === 0) {
    return <div className="text-xs text-muted">{tr("plugins.keine_rechte")}</div>;
  }
  const allowed = plugin.reads_granted || [];
  const toggle = (right: string) =>
    onSet(allowed.includes(right) ? allowed.filter((r) => r !== right) : [...allowed, right]);

  return (
    <div className="space-y-1">
      <div className="text-xs text-muted">{tr("plugins.rechte")}</div>
      {requested.map((right) => (
        <label key={right} className="flex cursor-pointer items-center gap-2 text-sm text-ink">
          <input type="checkbox" checked={allowed.includes(right)}
            onChange={() => toggle(right)} />
          <code className="font-mono text-xs text-brand">{right}</code>
          <span className="text-xs text-muted">
            {RIGHT_TEXT[right] ? tr(RIGHT_TEXT[right]) : ""}
          </span>
        </label>
      ))}
    </div>
  );
}
