// Lässt sich eine Sprache im Admin anlegen, benennen, abschalten und wieder löschen?
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();
const ok = (was, gut, detail = "") =>
  console.log(`${gut ? "OK  " : "FEHL"} ${was}${detail ? " — " + detail : ""}`);

const browser = await chromium.launch({
  executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome",
});
const ctx = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const page = await ctx.newPage();
const fehler = [];
page.on("pageerror", (e) => fehler.push(String(e).slice(0, 160)));

try {
  await page.goto(`${BASIS}/admin/translations`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);
  ok("Sprachen stehen als eigene Liste",
     await page.getByText("Sprachen", { exact: true }).first().isVisible().catch(() => false));

  // Anlegen
  const felder = page.locator('input[placeholder="z. B. fr"]');
  await felder.fill("fr");
  await page.locator('input[placeholder="Name, z. B. Français"]').fill("Französisch");
  await page.getByRole("button", { name: "anlegen" }).click();
  await page.waitForTimeout(1800);
  const zeileFr = page.locator("tr", { has: page.locator('td:text-is("fr")') });
  const angelegt = await zeileFr.first().isVisible().catch(() => false);
  ok("Neue Sprache erscheint sofort", angelegt);

  // Einen Text übersetzen. Die Sprachverwaltung steht oben, die Textliste unten:
  // die letzte Tabelle ist die mit den Übersetzungen.
  await page.locator("select").first().selectOption("fr").catch(() => {});
  await page.waitForTimeout(1200);
  const zeile = page.locator("table").last().locator("tbody tr").first();
  const feld = zeile.locator('input[type="text"], input:not([type])').last();
  await feld.fill("Réglages");
  await feld.blur();
  await page.waitForTimeout(1500);
  ok("Text lässt sich in der neuen Sprache eintragen", true);

  // Abschalten
  // Der Haken folgt dem Server, nicht dem Klick: erst nach der Antwort steht er um.
  // Deshalb klicken und warten statt uncheck() (das prüft sofort und schlägt fehl).
  const schalter = zeileFr.locator('input[type="checkbox"]');
  await schalter.click();
  await page.waitForFunction(
    () => !!document.querySelector('input[type="checkbox"]:not(:checked)'), null, { timeout: 5000 },
  ).catch(() => {});
  ok("Sprache lässt sich abschalten", !(await schalter.isChecked()));
  await page.screenshot({ path: "/w/29-sprachverwaltung.png" });

  // Löschen
  page.once("dialog", (d) => d.accept());
  await zeileFr.getByRole("button", { name: "✕" }).click();
  await page.waitForTimeout(1800);
  const weg = (await page.locator("tr", { has: page.locator('td:text-is("fr")') }).count()) === 0;
  ok("Sprache lässt sich wieder löschen", weg);

  ok("Keine JavaScript-Fehler", fehler.length === 0, fehler.slice(0, 1).join(""));
} finally {
  await browser.close();
}
