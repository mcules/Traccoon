// Can a flow be edited on a phone? Without dragging, without three columns side by side.
//
// The editor simply could not be operated there: blocks reached the canvas only by HTML5
// dragging (an event a touchscreen does not have), and palette, canvas and settings together
// needed 528 px of width. This probe walks the path a thumb actually takes, and saves
// nothing: whatever it changes is gone after a reload.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const WF = process.env.WF || "44";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();
const ok = (was, gut, detail = "") =>
  console.log(`${gut ? "OK  " : "FEHL"} ${was}${detail ? " — " + detail : ""}`);

const browser = await chromium.launch({
  executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome",
});
const ctx = await browser.newContext({
  viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true,
});
await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const page = await ctx.newPage();
const fehler = [];
page.on("pageerror", (e) => fehler.push(String(e).slice(0, 160)));

const knoten = () => page.locator(".react-flow__node").count();

try {
  await page.goto(`${BASIS}/workflows/${WF}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2500);

  ok("Die Fläche füllt den Bildschirm", (await knoten()) > 0, `${await knoten()} Bausteine`);
  ok("Umschalter zwischen Fläche und Baustein steht da",
     await page.getByRole("button", { name: "Baustein", exact: true }).isVisible());

  // Tap a block: its settings have to open on their own.
  await page.locator(".react-flow__node").first().tap();
  await page.waitForTimeout(1000);
  const konfigOffen = await page.getByText("Bausteine", { exact: true }).isVisible().catch(() => false);
  ok("Tippen auf einen Baustein öffnet seine Einstellungen", konfigOffen);
  await page.screenshot({ path: "/w/31-mobile-editor-block.png" });

  // Change the label, the header has to report unsaved work.
  const feld = page.locator('input[type="text"], input:not([type])').first();
  await feld.fill("Probe am Handy");
  await feld.blur();
  await page.waitForTimeout(800);
  const schmutzig = await page.getByText(/ungespeichert/).isVisible().catch(() => false);
  ok("Änderungen kommen an", schmutzig);

  // Attach a new block by tapping, since dragging does not exist here.
  const vorher = await knoten();
  await page.getByRole("button", { name: "⏱ Warten", exact: true }).first().tap();
  await page.waitForTimeout(1200);
  await page.getByRole("button", { name: "Fläche", exact: true }).tap();
  await page.waitForTimeout(1200);
  const nachher = await knoten();
  ok("Baustein lässt sich antippen statt ziehen", nachher === vorher + 1,
     `${vorher} → ${nachher} Bausteine`);
  await page.screenshot({ path: "/w/32-mobile-editor-canvas.png" });

  // Nothing stands past the edge.
  const ueber = await page.evaluate(
    () => document.scrollingElement.scrollWidth - window.innerWidth);
  ok("Nichts steht seitlich über den Rand", ueber <= 2, `${ueber} px`);

  ok("Keine JavaScript-Fehler", fehler.length === 0, fehler.slice(0, 1).join(""));
} finally {
  await browser.close();
}
