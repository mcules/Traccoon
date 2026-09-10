// Sagt das erste Feld, dass auch der Benutzername geht?
import { chromium } from "playwright-core";
const BASIS = process.env.BASIS || "http://frontend";
const browser = await chromium.launch({ executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome" });
const page = await (await browser.newContext({ viewport: { width: 700, height: 600 } })).newPage();
try {
  await page.goto(`${BASIS}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);
  console.log(await page.evaluate(() =>
    [...document.querySelectorAll("input")].map((i) => ({
      platzhalter: i.placeholder, typ: i.type, autocomplete: i.autocomplete }))));
  await page.screenshot({ path: "/w/login-feld.png" });
} finally { await browser.close(); }
