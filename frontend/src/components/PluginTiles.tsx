import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { PluginContribution, PluginInfo } from "../api";
import { usePlugins } from "../plugins";
import { usePluginBridge, type TileNotes } from "../lib/pluginBridge";
import { tr } from "../i18n";
import { Breakdown, Hover } from "./ui";

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
/**
 * The tiles as **entries of the grid they are placed in**, without a grid of their own.
 *
 * They used to bring their own row at the foot of the start page. A tile is a key figure like
 * the ones in the head — the shield says how many findings are open, and that is exactly the
 * kind of thing one wants above, next to one's own numbers, not below three lists. Whoever
 * places them decides the columns; the tile only fills the cell it gets, which is why it
 * stretches instead of insisting on its declared height.
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
    <>
      {tiles.map(({ plugin, tile }) => (
        <PluginTile key={`${plugin.slug}:${tile.path}`} plugin={plugin} tile={tile} />
      ))}
    </>
  );
}

function PluginTile({ plugin, tile }: { plugin: PluginInfo; tile: PluginContribution }) {
  const frame = useRef<HTMLIFrameElement>(null);
  // What stands behind the figures of the tile — the plugin sends it, the host draws it. It
  // cannot draw it itself: its frame takes no mouse, so no tooltip of its own would ever open.
  const [note, setNote] = useState<TileNotes | null>(null);
  usePluginBridge(plugin.slug, frame, plugin.reads_granted || [], setNote);

  const anchor = (tile.path || "tile").replace(/^\//, "");
  const height = Math.min(320, Math.max(64, Number(tile.height) || 120));
  const zones = note?.zones ?? [];

  const card = (
    <Link
      to={`/p/${plugin.slug}`}
      title={tr(tile.label || plugin.name)}
      style={{ minHeight: height }}
      className="relative block h-full overflow-hidden rounded-lg border border-line bg-card hover:border-brand"
    >
      {/* The declared height sits on the CARD as a minimum, the frame lies over it. A
          percentage height on the frame itself would fall back to the 150px an iframe is by
          default — its parent has no height of its own to measure against — and that default
          was what made the whole head of the page too tall. Out of the flow it cannot do
          that: the card takes the height of its row, the frame fills exactly that. */}
      <iframe
        ref={frame}
        src={`/api/plugins/${plugin.slug}/app/#${anchor}`}
        title={tr(tile.label || plugin.name)}
        className="pointer-events-none absolute inset-0 h-full w-full border-0"
        sandbox="allow-scripts"
      />
      {/* The mouse targets over the figures the plugin has drawn. Nothing to see: the figure
          is already there, underneath. They lie INSIDE the link on purpose — a zone has to
          take the mouse to open its tooltip at all, and anything that takes the mouse over
          the card would otherwise swallow the click that is supposed to lead into the plugin.
          As a descendant of the link it passes the click on by itself. */}
      {zones.map((z) => (
        <span key={z.key} className="absolute block"
          style={{ left: z.rect.x, top: z.rect.y, width: z.rect.w, height: z.rect.h }}>
          <Hover className="block h-full w-full"
            note={<Breakdown title={z.title} rows={z.rows} />}>
            <span className="block h-full w-full" />
          </Hover>
        </span>
      ))}
    </Link>
  );

  // A note per figure and one over the whole card would both stand open at once — entering a
  // zone does not leave the card. The zones are the better answer of the two, so where they
  // exist they are the only one; a tile that sends none keeps the single note over everything.
  return note?.whole
    ? <Hover className="block" note={<Breakdown title={note.whole.title} rows={note.whole.rows} />}>{card}</Hover>
    : card;
}
