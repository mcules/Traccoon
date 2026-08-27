import { useRef } from "react";
import { Link } from "react-router-dom";
import { PluginContribution, PluginInfo } from "../api";
import { usePlugins } from "../plugins";
import { usePluginBridge } from "../lib/pluginBridge";
import { tr } from "../i18n";

/**
 * What the plugins contribute to the dashboard.
 *
 * A tile is the same plugin behind the same fence as its page, only small: the host gives it
 * no more rights here than there. Deliberately an iframe and not data the host renders —
 * otherwise every plugin would have to teach Traccoon a new shape of tile, and the fence
 * would end where the drawing begins.
 *
 * The click belongs to the host, not to the plugin: the frame is `pointer-events-none` and
 * the link around it leads to the full page. That way a tile cannot navigate anywhere on its
 * own, and the person still gets the one obvious gesture.
 */
export default function PluginTiles() {
  const plugins = usePlugins();
  const tiles: { plugin: PluginInfo; tile: PluginContribution }[] = [];
  for (const p of plugins) {
    for (const c of p.contributions || []) {
      if (c.type === "tile") tiles.push({ plugin: p, tile: c });
    }
  }
  if (!tiles.length) return null;

  return (
    <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {tiles.map(({ plugin, tile }) => (
        <PluginTile key={`${plugin.slug}:${tile.path}`} plugin={plugin} tile={tile} />
      ))}
    </div>
  );
}

function PluginTile({ plugin, tile }: { plugin: PluginInfo; tile: PluginContribution }) {
  const frame = useRef<HTMLIFrameElement>(null);
  usePluginBridge(plugin.slug, frame, plugin.reads_granted || []);

  const anchor = (tile.path || "tile").replace(/^\//, "");
  const height = Math.min(320, Math.max(64, Number(tile.height) || 120));

  return (
    <Link
      to={`/p/${plugin.slug}`}
      title={tr(tile.label || plugin.name)}
      className="block overflow-hidden rounded-lg border border-line bg-card hover:border-brand"
    >
      <iframe
        ref={frame}
        src={`/api/plugins/${plugin.slug}/app/#${anchor}`}
        title={tr(tile.label || plugin.name)}
        style={{ height }}
        className="pointer-events-none block w-full border-0"
        sandbox="allow-scripts"
      />
    </Link>
  );
}
