// "Mark everything read" without a question — and the two new switches in the account.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";
const BASIS = process.env.BASIS || "http://frontend";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();

const browser = await chromium.launch({ executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome" });
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const page = await ctx.newPage();
page.on("pageerror", (e) => console.log("  SEITENFEHLER:", String(e).slice(0, 200)));
const rufe = [];
page.on("request", (r) => { if (/read-all/.test(r.url())) rufe.push(r.method() + " " + r.url()); });

try {
  await page.goto(`${BASIS}/mail`, { waitUntil: "networkidle" });
  await page.waitForTimeout(4000);

  // Das "…" am offenen Ordner
  const punkte = page.locator('button:has-text("…"), button:has-text("⋯")');
  console.log("Punkte-Knöpfe:", await punkte.count());
  await punkte.last().click();
  await page.waitForTimeout(500);
  const eintrag = page.locator('button, [role="menuitem"]')
    .filter({ hasText: /Alle gelesen/ }).first();
  console.log("Eintrag „Alle gelesen“ gefunden:", await eintrag.count());
  await eintrag.click();
  await page.waitForTimeout(1500);

  const dialog = await page.locator('text=/Alle als gelesen markieren\\?/').count();
  console.log("Sicherheitsabfrage sichtbar:", dialog ? "JA (falsch)" : "nein");
  console.log("read-all gerufen:", JSON.stringify(rufe));
  await page.screenshot({ path: "/w/mail-folder-read.png" });
} finally {
  await browser.close();
}
