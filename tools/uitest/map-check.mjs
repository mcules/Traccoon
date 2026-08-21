// What a plugin does in a browser — asked of the map, which is the one that shows it.
//
// The map went blank once, and from the server nothing looked wrong: every file answered 200,
// the bridge delivered its series, the CSP named the tile host and OpenStreetMap served a tile
// on request. It could not look wrong from there either. The tiles are fetched by the browser
// straight from OSM, so they touch no log of ours, and a plugin runs in an iframe with an
// opaque origin, so its console goes nowhere either. Diagnosing it meant guessing.
//
// This probe closes that hole. It counts the tiles in the DOM against the ones that really
// loaded — the difference is the whole question — and hands back what only the browser knows:
// console errors, failed requests, and whether the map container has a height at all.
//
//   docker run --rm --network traccoon_default -v "$PWD/tools/uitest":/w -w /w \
//     -e BASIS=http://frontend mcr.microsoft.com/playwright:v1.56.0-noble node /w/map-check.mjs
//
// PLUGIN=<slug> looks at a different plugin; the tile count then stays at zero, everything
// else applies to any of them.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const PLUGIN = process.env.PLUGIN || "map";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const page = await ctx.newPage();

const noise = [];
// The console of the iframe as well: `page.on("console")` catches every frame, and the
// interesting message (a refused image) arrives from exactly the one without an origin.
page.on("console", (m) => { if (m.type() === "error") noise.push(`console: ${m.text().slice(0, 200)}`); });
page.on("pageerror", (e) => noise.push(`script: ${String(e).slice(0, 200)}`));
page.on("requestfailed", (r) => noise.push(`failed: ${r.url().slice(0, 110)} — ${r.failure()?.errorText}`));
page.on("response", (r) => { if (r.status() >= 400) noise.push(`http ${r.status()}: ${r.url().slice(0, 110)}`); });

await page.goto(`${BASIS}/p/${PLUGIN}`, { waitUntil: "networkidle", timeout: 45000 });
// Tiles arrive after the map has sized itself, which happens after the layout of the host.
// Without the wait the count is honest and useless.
await page.waitForTimeout(4000);

const frame = page.frames().find((f) => f.url().includes(`/plugins/${PLUGIN}/`));
if (!frame) {
  console.log(`No iframe of the plugin. Reached: ${page.url()}`);
  console.log(noise.join("\n"));
  await browser.close();
  process.exit(1);
}

const found = await frame.evaluate(() => {
  const box = document.getElementById("map");
  const tiles = [...document.querySelectorAll("img.leaflet-tile")];
  return {
    container: box ? `${box.clientWidth}×${box.clientHeight}` : "missing",
    leaflet: typeof window.L !== "undefined",
    bridge: typeof window.traccoon !== "undefined",
    tiles_in_dom: tiles.length,
    tiles_loaded: tiles.filter((t) => t.complete && t.naturalWidth > 0).length,
    first_tile: tiles[0]?.src || null,
    sidebar: (document.getElementById("series")?.innerText || "").replace(/\s+/g, " ").slice(0, 120),
  };
});

await page.screenshot({ path: "/w/map-check.png" });
console.log(JSON.stringify(found, null, 1));
if (noise.length) console.log("\n" + noise.join("\n"));

// A container without a height, a tile that stays empty, a bridge that never arrived: each of
// them is the fault, and each of them looks like "no map" to whoever is standing in front of it.
const broken = found.container === "missing" || found.container.endsWith("×0")
  || !found.leaflet || !found.bridge
  || (PLUGIN === "map" && found.tiles_loaded === 0);
console.log(broken ? "\nBROKEN" : "\nok");
await browser.close();
process.exit(broken ? 1 : 0);
