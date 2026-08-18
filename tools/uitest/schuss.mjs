import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();
const SEITEN = (process.env.SEITEN || "projekte:/,projekt:/projects/UNI,board:/projects/UNI?tab=board,prozesse:/processes,einstellungen:/settings,profil:/profil")
  .split(",").map((s) => { const i = s.indexOf(":"); return [s.slice(0, i), s.slice(i + 1)]; });
const b = await chromium.launch({ executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome" });
const BREIT = process.env.BREIT === "1";
const c = await b.newContext(BREIT
  ? { viewport: { width: 1400, height: 900 } }
  : { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
await c.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const p = await c.newPage();
for (const [name, pfad] of SEITEN) {
  await p.goto("http://frontend" + pfad, { waitUntil: "networkidle" }).catch(() => {});
  await p.waitForTimeout(1500);
  await p.screenshot({ path: `/w/${BREIT ? "d" : "m"}-${name}.png` });
  const h = await p.evaluate(() => document.scrollingElement.scrollHeight);
  console.log(`${name}: ${Math.round(h / (BREIT ? 900 : 844) * 10) / 10} Bildschirme hoch`);
}
await b.close();
