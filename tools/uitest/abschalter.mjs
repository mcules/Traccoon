// Kann man einen Schritt abschalten, und sieht man es dem Graphen an?
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();
const ok = (was, gut, detail = "") =>
  console.log(`${gut ? "OK  " : "FEHL"} ${was}${detail ? " — " + detail : ""}`);

const browser = await chromium.launch({
  executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome",
});
const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 } });
await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const page = await ctx.newPage();
const fehler = [];
page.on("pageerror", (e) => fehler.push(String(e).slice(0, 160)));

try {
  await page.goto(`${BASIS}/workflows/44`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2500);

  // Einen Aktions-Knoten anklicken
  await page.getByText("Akkustand festhalten").first().click();
  await page.waitForTimeout(800);
  const schalter = page.getByText(/Diesen Schritt abschalten/i).first();
  ok("Der Schalter steht in der Konfiguration",
     await schalter.isVisible().catch(() => false));

  await schalter.click();
  await page.waitForTimeout(600);
  const wahl = await page.getByText(/Was soll dann passieren\?/i).first()
    .isVisible().catch(() => false);
  ok("Man wählt zwischen Überspringen und Abbrechen", wahl);
  const hinweis = await page.getByText(/geht über den normalen Ausgang weiter/i).first()
    .isVisible().catch(() => false);
  ok("Der gewählte Fall wird erklärt", hinweis);

  const marke = await page.locator(".react-flow__node", { hasText: "Akkustand festhalten" })
    .getByText("aus").count();
  ok("Der Knoten zeigt im Graphen, dass er aus ist", marke > 0);
  await page.screenshot({ path: "/w/25-abschalter.png" });

  // Auf „abbrechen" umstellen — der Hinweis muss sich ändern
  await page.locator("select").filter({ hasText: "überspringen und weitermachen" }).first()
    .selectOption("abbrechen").catch(() => {});
  await page.waitForTimeout(500);
  const hinweis2 = await page.getByText(/enden hier als abgebrochen/i).first()
    .isVisible().catch(() => false);
  ok("Der Abbruch-Fall wird eigens erklärt", hinweis2);

  ok("Keine JavaScript-Fehler", fehler.length === 0, fehler.slice(0, 1).join(""));
} finally {
  await browser.close();
}
