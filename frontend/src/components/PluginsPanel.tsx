import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, pluginApi, type PluginAdmin } from "../api";
import { tr } from "../i18n";
import {
  Actions, Area, Tag, Errorrow, ICON, IconButton, Button, Listing, ListingEmpty,
  ListRow, DeleteDialog } from "./ui";

/**
 * Plugins verwalten: einspielen, freigeben, abschalten.
 *
 * The core of this page are the ticks on the rights. A plugin runs in the browser of a
 * logged-in person; what it may see of Traccoon's data can therefore not be decided by the
 * plugin itself. Its manifest *demands* — here somebody *allows*. Without a tick the bridge in
 * the host answers with a block, whatever stands in the zip.
 */

/** What a right stands for, in one sentence. Unknown ones the list shows raw — better the bare
 *  identifier than an invented description. */
const RIGHT_TEXT: Record<string, string> = {
  "series:number": "plugins.read_metric_series_values",
  "series:location": "plugins.read_location_series_points",
  "series:text": "plugins.read_storages_entries",
};

export default function PluginsPanel() {
  const qc = useQueryClient();
  const [err, setErr] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<PluginAdmin | null>(null);
  const fileField = useRef<HTMLInputElement>(null);

  const { data: plugins } = useQuery({
    queryKey: ["plugins", "alle"], queryFn: () => pluginApi.all(),
  });
  // Refresh both lists: the administration sees everything, the area rail only what is
  // released — after a tick it has to move as well.
  const inv = () => qc.invalidateQueries({ queryKey: ["plugins"] });
  const fail = (e: unknown) => setErr(e instanceof ApiError ? e.message : tr("common.error"));

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
    <Area title={tr("plugins.plugins")} hint={tr("plugins.self_contained_views_installed")} tools={
      <>
        <input ref={fileField} type="file" accept=".zip" className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) upload.mutate(f);
            e.target.value = "";
          }} />
        <Button variant="primary" runs={upload.isPending}
          onClick={() => fileField.current?.click()}>{tr("plugins.install_zip")}</Button>
      </>
    }>
      <Errorrow text={err} />
      <Listing>
        {(plugins || []).map((p) => (
          <ListRow key={p.slug} dimmed={!p.enabled}>
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <span className="text-lg leading-none">{p.icon || "\u{1F9E9}"}</span>
                <span className="font-medium text-ink">{p.name}</span>
                <code className="font-mono text-xs text-muted">{p.slug} · {p.version}</code>
                {!p.enabled && <Tag color="neutral">{tr("plugins.switched_off")}</Tag>}
                <div className="flex-1" />
                <Actions>
                  <Button small variant={p.enabled ? "secondary" : "confirm"}
                    onClick={() => rights.mutate({ slug: p.slug, body: { enabled: !p.enabled } })}>
                    {p.enabled ? tr("plugins.switch_off") : tr("plugins.switch")}
                  </Button>
                  <IconButton icon={ICON.remove} title={tr("common.delete")} danger
                    onClick={() => setDeleteTarget(p)} />
                </Actions>
              </div>
              {p.description && <div className="text-xs text-muted">{p.description}</div>}
              <Rights plugin={p} onSet={(listing) =>
                rights.mutate({ slug: p.slug, body: { reads_granted: listing } })} />
              {(p.allowed_hosts || []).length > 0 && (
                <div className="text-xs text-muted">
                  {tr("plugins.may_fetch")}: {p.allowed_hosts.join(", ")}
                </div>
              )}
            </div>
          </ListRow>
        ))}
        {plugins?.length === 0 && <ListingEmpty>{tr("plugins.no_plugin_installed_yet")}</ListingEmpty>}
      </Listing>

      {deleteTarget && (
        <DeleteDialog was={deleteTarget.name} hint={tr("plugins.plugin_s_files_own")}
          runs={remove.isPending}
          onClose={() => setDeleteTarget(null)}
          onDelete={() => remove.mutate(deleteTarget.slug)} />
      )}
    </Area>
  );
}

/** The ticks: what the manifest demands, and what of it applies. */
function Rights({ plugin, onSet: onSet }: {
  plugin: PluginAdmin; onSet: (listing: string[]) => void;
}) {
  const requested = plugin.reads || [];
  if (requested.length === 0) {
    return <div className="text-xs text-muted">{tr("plugins.asks_no_traccoon_data")}</div>;
  }
  const allowed = plugin.reads_granted || [];
  const toggle = (right: string) =>
    onSet(allowed.includes(right) ? allowed.filter((r) => r !== right) : [...allowed, right]);

  return (
    <div className="space-y-1">
      <div className="text-xs text-muted">{tr("plugins.requested_rights")}</div>
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
