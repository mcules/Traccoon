// Der Assistent im ganzen Haus: aufklappbar auf jeder Seite, und was
// mitgeschickt werden kann, sagt die Seite selbst.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();
const ok = (was, gut, mehr = "") =>
  console.log(`${gut ? "OK  " : "FAIL"} ${was}${mehr ? " — " + mehr : ""}`);

const browser = await chromium.launch({
  executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome",
});
const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const fehler = [];
let page = await ctx.newPage();
page.on("pageerror", (e) => fehler.push(String(e).slice(0, 200)));
// Jede Seite in einem frischen Fenster: sonst misst man, was von der vorigen
// Seite noch angemeldet war, statt was diese Seite anbietet.
const frisch = async () => {
  await page.close();
  page = await ctx.newPage();
  page.on("pageerror", (e) => fehler.push(String(e).slice(0, 200)));
};

const auf = async () => {
  const knopf = page.locator('button[title="Assistent"]').first();
  if (await knopf.count() === 0) return false;
  if (await page.locator("aside textarea").count() === 0) await knopf.click();
  await page.waitForTimeout(900);
  return true;
};
const angebot = async () => {
  const s = page.locator("aside select").first();
  const label = page.locator("aside label").filter({ hasText: /mitschicken|Notiz/ }).first();
  return {
    haken: (await label.count()) ? (await label.innerText()).trim() : "",
    wahl: (await s.count()) ? await s.locator("option").allInnerTexts() : [],
  };
};

try {
  // ── Eine gewoehnliche Seite ────────────────────────────────────────────
  await page.goto(`${BASIS}/projects`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2000);
  ok("der Assistent ist von einer gewoehnlichen Seite aus erreichbar", await auf());
  let a = await angebot();
  ok("dort wird die Seite angeboten", /Seite mitschicken/.test(a.haken), a.haken);
  ok("und keine Auswahl noetig", a.wahl.length === 0, a.wahl.join(" | "));
  await page.screenshot({ path: "/w/90-assistent-seite.png" });

  // ── Der Kalender ───────────────────────────────────────────────────────
  await frisch();
  await page.goto(`${BASIS}/notes`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2500);
  const kalender = page.locator('[title*="Kalender"], button:has-text("Kalender")').first();
  if (await kalender.count()) { await kalender.click(); await page.waitForTimeout(2500); }
  await auf();
  a = await angebot();
  ok("im Kalender stehen Monat, Woche und Tag vorn",
     a.wahl.slice(0, 3).join("|") === "dieser Monat|diese Woche|dieser Tag", a.wahl.join(" | "));
  await page.screenshot({ path: "/w/91-assistent-kalender.png" });

  // ── Eine offene Notiz ──────────────────────────────────────────────────
  // Ueber den Reiter statt ueber die Adresse: die Notizansicht stellt beim
  // Laden ihren zuletzt offenen Reiter wieder her, ein Tiefenlink schaltet ihn
  // nicht um. Gemessen werden soll, was auf dem Schirm steht.
  await frisch();
  await page.goto(`${BASIS}/notes`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2500);
  const reiter = page.locator('[class*="tab"]').filter({ hasText: /^\d{4}-\d{2}-\d{2}$/ }).first();
  if (await reiter.count()) { await reiter.click(); await page.waitForTimeout(2000); }
  await auf();
  a = await angebot();
  ok("in einer Notiz steht die Notiz vorn",
     (a.wahl[0] || a.haken).includes("Notiz"), (a.haken + " | " + a.wahl.join(" | ")).trim());
  await page.screenshot({ path: "/w/92-assistent-notiz.png" });

  // ── Und die Unterhaltung ist dieselbe ──────────────────────────────────
  const blasen = await page.locator("aside .whitespace-pre-wrap").count();
  ok("die Unterhaltung steht auch hier", blasen > 0, `${blasen} Blasen`);
} catch (e) {
  ok("durchgelaufen", false, String(e).slice(0, 220));
} finally {
  ok("kein Fehler auf der Seite", fehler.length === 0, fehler.join(" | "));
  await browser.close();
}
