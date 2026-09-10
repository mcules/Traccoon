// The two new switches in the mailbox settings: are they there, and do they save?
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";
const BASIS = process.env.BASIS || "http://frontend";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();

const browser = await chromium.launch({ executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome" });
const ctx = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const page = await ctx.newPage();
page.on("pageerror", (e) => console.log("  SEITENFEHLER:", String(e).slice(0, 200)));

try {
  await page.goto(`${BASIS}/account/mail`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2500);
  const bearbeiten = page.locator('button').filter({ hasText: /Bearbeiten|Ändern|✏/ }).first();
  if (await bearbeiten.count()) { await bearbeiten.click(); await page.waitForTimeout(1500); }

  const reiter = page.locator('button').filter({ hasText: /Ordner/ }).first();
  console.log("Reiter „Ordner“:", await reiter.count());
  if (await reiter.count()) { await reiter.click(); await page.waitForTimeout(800); }

  for (const text of [/Papierkorb markiert als gelesen/, /Vor „alles gelesen“ nachfragen/]) {
    const l = page.locator("label").filter({ hasText: text }).first();
    const da = await l.count();
    const an = da ? await l.locator('input[type=checkbox]').isChecked() : null;
    console.log(`${text} -> vorhanden: ${da}, angehakt: ${an}`);
  }
  await page.screenshot({ path: "/w/mail-account-behaviour.png", fullPage: false });
} finally {
  await browser.close();
}
