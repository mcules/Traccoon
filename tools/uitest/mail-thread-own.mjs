// Sieht man in einem Verlauf, welche Nachricht von mir ist?
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";
const BASIS = process.env.BASIS || "http://frontend";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();

const browser = await chromium.launch({ executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome" });
const ctx = await browser.newContext({ viewport: { width: 1600, height: 620 } });
await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const page = await ctx.newPage();
page.on("pageerror", (e) => console.log("  SEITENFEHLER:", String(e).slice(0, 200)));
try {
  await page.goto(`${BASIS}/mail`, { waitUntil: "networkidle" });
  await page.waitForTimeout(4500);
  // Der USB-Box-Verlauf: dort liegen zwei eigene Antworten drin.
  const pfeil = page.locator('button[aria-expanded="false"]').filter({ hasText: "▸" }).nth(1);
  await pfeil.click();
  await page.waitForTimeout(900);
  console.log(await page.evaluate(() => {
    const gruppen = [...document.querySelectorAll('[class*="divide-y"]')]
      .filter((g) => g.querySelector('input[type=checkbox]'));
    const rows = gruppen.length ? [...gruppen[gruppen.length - 1].children] : [];
    return rows.slice(0, 8).map((r) => (r.textContent || "").trim().slice(0, 72));
  }));
  await page.screenshot({ path: "/w/mail-thread-own.png" });
} finally {
  await browser.close();
}
