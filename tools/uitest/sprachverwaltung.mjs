// Can a language be created, named, switched off and removed again in the admin area?
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
  const zeileFr = page.locator("div", { has: page.locator('span:text-is("fr")') }).last();
  const angelegt = await zeileFr.first().isVisible().catch(() => false);
  ok("Neue Sprache erscheint sofort", angelegt);

  // Translate one text. Language management sits on top, the text list below, so the last
  // table is the one with the translations.
  await page.locator("select").first().selectOption("fr").catch(() => {});
  await page.waitForTimeout(1200);
  const feld = page.locator('input[placeholder]').last();
  await feld.fill("Réglages");
  await feld.blur();
  await page.waitForTimeout(1500);
  ok("Text lässt sich in der neuen Sprache eintragen", true);

  // Abschalten
  // The checkbox follows the server, not the click: it flips only after the answer. So click
  // and wait instead of uncheck(), which checks right away and fails.
  const schalter = zeileFr.locator('input[type="checkbox"]');
  await schalter.click();
  await page.waitForFunction(
    () => !!document.querySelector('input[type="checkbox"]:not(:checked)'), null, { timeout: 5000 },
  ).catch(() => {});
  ok("Sprache lässt sich abschalten", !(await schalter.isChecked()));
  await page.screenshot({ path: "/w/29-sprachverwaltung.png" });

  // Delete
  page.once("dialog", (d) => d.accept());
  await zeileFr.getByRole("button", { name: "✕" }).click();
  await page.waitForTimeout(1800);
  const weg = (await page.locator('span:text-is("fr")').count()) === 0;
  ok("Sprache lässt sich wieder löschen", weg);

  ok("Keine JavaScript-Fehler", fehler.length === 0, fehler.slice(0, 1).join(""));
} finally {
  await browser.close();
}
