// Der Assistent nimmt Platz, statt zu verdecken: der Inhalt wird schmaler.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";
const BASIS = process.env.BASIS || "http://frontend";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();
const ok = (was, gut, mehr = "") =>
  console.log(`${gut ? "OK  " : "FAIL"} ${was}${mehr ? " — " + mehr : ""}`);
const browser = await chromium.launch({
  executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome" });
const fehler = [];
const breite = async (page, wahl) => page.evaluate((w) => {
  const el = document.querySelector(w);
  return el ? Math.round(el.getBoundingClientRect().width) : -1;
}, wahl);
try {
  for (const [w, seite, inhalt] of [[1400, "/projects", "main"], [1400, "/notes", "main"]]) {
    const ctx = await browser.newContext({ viewport: { width: w, height: 950 } });
    await ctx.addInitScript((t) => {
      localStorage.setItem("traccoon_token", t);
      localStorage.removeItem("traccoon.assistant.open");
    }, TOKEN);
    const page = await ctx.newPage();
    page.on("pageerror", (e) => fehler.push(String(e).slice(0, 160)));
    await page.goto(`${BASIS}${seite}`, { waitUntil: "domcontentloaded" });
    await page.waitForSelector("header");
    await page.waitForTimeout(2500);
    const vorher = await breite(page, inhalt);
    await page.locator('header button[title="Assistent"]').click();
    await page.waitForTimeout(1200);
    const nachher = await breite(page, inhalt);
    const panel = await breite(page, "aside[class*='border-l']");
    ok(`${seite}: der Inhalt wird schmaler statt verdeckt`, nachher < vorher - 200,
       `${vorher} -> ${nachher}px, Panel ${panel}px`);
    const ueber = await page.evaluate(() => {
      const a = document.querySelector("aside[class*='border-l']");
      const m = document.querySelector("main");
      if (!a || !m) return true;
      const ab = a.getBoundingClientRect(), mb = m.getBoundingClientRect();
      return ab.left < mb.right - 2;   // Ueberlappung?
    });
    ok(`${seite}: es ueberlappt nichts`, !ueber);
    await page.screenshot({ path: `/w/96-assistent-spalte-${seite.slice(1)}.png` });
    await ctx.close();
  }
} catch (e) {
  ok("durchgelaufen", false, String(e).slice(0, 200));
} finally {
  ok("kein Fehler auf der Seite", fehler.length === 0, fehler.join(" | "));
  await browser.close();
}
