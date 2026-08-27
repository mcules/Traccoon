import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useParams } from "react-router-dom";
import { tr } from "../i18n";
import { usePlugins } from "../plugins";
import { usePluginBridge } from "../lib/pluginBridge";
import { usePageChrome } from "../pageChrome";
import { Area } from "../components/ui";

/**
 * The page that shows one plugin full screen.
 *
 * What it may ask the host for, and the fence around it, live in `lib/pluginBridge` — the
 * dashboard tiles hang on the same bridge, and a security boundary is worth having once.
 */
export default function PluginHost() {
  const { slug = "" } = useParams();
  const loc = useLocation();
  const plugins = usePlugins();
  const frame = useRef<HTMLIFrameElement>(null);
  const [error, setError] = useState("");

  const plugin = useMemo(() => plugins.find((p) => p.slug === slug), [plugins, slug]);
  usePageChrome(plugin ? tr(plugin.name) : tr("plugins.plugins"), []);

  usePluginBridge(slug, frame, plugin?.reads_granted || []);

  useEffect(() => setError(""), [slug]);

  if (plugins.length && !plugin) {
    return <Area hint={tr("plugins.plugin_does_not_exist")}><div /></Area>;
  }

  // The anchor decides which page of a plugin with several contributions is meant.
  const src = `/api/plugins/${slug}/app/${loc.hash || ""}`;

  return (
    <div className="h-[calc(100vh-9rem)] min-h-[420px] w-full overflow-hidden rounded-lg border border-line bg-surface">
      {error && <div className="p-3 text-sm text-red-500">{error}</div>}
      <iframe
        ref={frame}
        src={src}
        title={plugin ? tr(plugin.name) : slug}
        onError={() => setError(tr("plugins.plugin_cannot_loaded"))}
        className="h-full w-full border-0"
        sandbox="allow-scripts allow-popups allow-forms"
      />
    </div>
  );
}
