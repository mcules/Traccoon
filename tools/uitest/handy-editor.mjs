// Lässt sich ein Ablauf am Handy bearbeiten? Ohne Ziehen, ohne drei Spalten nebeneinander.
//
// Der Editor war dort schlicht nicht bedienbar: die Bausteine kamen nur per HTML5-Ziehen auf
// die Fläche (ein Ereignis, das ein Touchscreen nicht kennt), und Palette, Fläche und
// Einstellungen brauchten zusammen 528 px Breite. Diese Probe prüft den Weg, den ein Daumen
// wirklich geht — und speichert nichts: was sie ändert, ist nach dem Neuladen wieder weg.
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

  // Ein Baustein antippen: die Einstellungen müssen von allein aufgehen.
  await page.locator(".react-flow__node").first().tap();
  await page.waitForTimeout(1000);
  const konfigOffen = await page.getByText("Bausteine", { exact: true }).isVisible().catch(() => false);
  ok("Tippen auf einen Baustein öffnet seine Einstellungen", konfigOffen);
  await page.screenshot({ path: "/w/31-handy-editor-baustein.png" });

  // Beschriftung ändern — der Kopf muss „ungespeichert" melden.
  const feld = page.locator('input[type="text"], input:not([type])').first();
  await feld.fill("Probe am Handy");
  await feld.blur();
  await page.waitForTimeout(800);
  const schmutzig = await page.getByText(/ungespeichert/).isVisible().catch(() => false);
  ok("Änderungen kommen an", schmutzig);

  // Neuen Baustein per Tipp anhängen — ohne Ziehen, das es hier nicht gibt.
  const vorher = await knoten();
  await page.getByRole("button", { name: "⏱ Warten", exact: true }).first().tap();
  await page.waitForTimeout(1200);
  await page.getByRole("button", { name: "Fläche", exact: true }).tap();
  await page.waitForTimeout(1200);
  const nachher = await knoten();
  ok("Baustein lässt sich antippen statt ziehen", nachher === vorher + 1,
     `${vorher} → ${nachher} Bausteine`);
  await page.screenshot({ path: "/w/32-handy-editor-flaeche.png" });

  // Nichts steht über den Rand.
  const ueber = await page.evaluate(
    () => document.scrollingElement.scrollWidth - window.innerWidth);
  ok("Nichts steht seitlich über den Rand", ueber <= 2, `${ueber} px`);

  ok("Keine JavaScript-Fehler", fehler.length === 0, fehler.slice(0, 1).join(""));
} finally {
  await browser.close();
}
