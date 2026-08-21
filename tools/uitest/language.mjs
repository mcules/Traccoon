// Does the interface carry a second language? Switching in the profile, editing in the admin.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();
const ok = (was, gut, detail = "") =>
  console.log(`${gut ? "OK  " : "FEHL"} ${was}${detail ? " — " + detail : ""}`);

const browser = await chromium.launch({
  executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome",
});
const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const page = await ctx.newPage();
const fehler = [];
page.on("pageerror", (e) => fehler.push(String(e).slice(0, 160)));

try {
  await page.goto(`${BASIS}/profil`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);
  ok("Sprachwahl steht im Profil",
     await page.getByText("Sprache", { exact: true }).first().isVisible().catch(() => false));

  // Switch to English
  await page.locator("section", { hasText: "Sprache" }).first().locator("select")
    .selectOption("en");
  await page.getByRole("button", { name: "Speichern" }).first().click();
  await page.waitForTimeout(2000);
  const englisch = await page.getByText("Language", { exact: true }).first()
    .isVisible().catch(() => false);
  ok("Die Oberfläche wechselt auf Englisch", englisch);
  await page.screenshot({ path: "/w/26-englisch.png" });

  // Check the navigation and a second page
  await page.goto(`${BASIS}/processes/messreihen`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);
  const reihen = await page.getByText("Series", { exact: false }).first()
    .isVisible().catch(() => false);
  ok("Auch andere Seiten sind übersetzt", reihen);
  await page.screenshot({ path: "/w/27-englisch-messreihen.png" });

  // The admin translation view
  await page.goto(`${BASIS}/admin/translations`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);
  const zeilen = await page.locator('input[placeholder]').count();
  ok("Die Verwaltung listet die Schlüssel", zeilen > 0, `${zeilen} Zeilen sichtbar`);
  const zaehler = await page.getByText(/\d+ (of|von) \d+ (open|offen)/).first()
    .textContent().catch(() => "");
  ok("Sie zeigt, wie viel noch fehlt", !!zaehler, (zaehler || "").trim());
  await page.screenshot({ path: "/w/28-verwaltung.png" });

  // Back to German so operation stays as it was
  await page.goto(`${BASIS}/profil`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1200);
  await page.locator("section", { hasText: "Language" }).first().locator("select")
    .selectOption("de");
  await page.getByRole("button", { name: "Save" }).first().click();
  await page.waitForTimeout(1500);
  ok("Zurück auf Deutsch",
     await page.getByText("Sprache", { exact: true }).first().isVisible().catch(() => false));

  ok("Keine JavaScript-Fehler", fehler.length === 0, fehler.slice(0, 1).join(""));
} finally {
  await browser.close();
}
