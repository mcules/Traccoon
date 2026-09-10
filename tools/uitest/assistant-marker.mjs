// The conversation switcher: does the dropdown look like one, do the numbered
// markers carry a state, does a right-click archive, and does a new conversation
// become the current one.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";
const BASIS = process.env.BASIS || "http://frontend";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();

const browser = await chromium.launch({ executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome" });
const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 } });
await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const page = await ctx.newPage();
page.on("pageerror", (e) => console.log("  SEITENFEHLER:", String(e).slice(0, 200)));

const marker = () =>
  page.evaluate(() => {
    const reihe = [...document.querySelectorAll("button")].filter(
      (b) => /^\d+$/.test((b.textContent || "").trim()) && b.className.includes("h-6"));
    return reihe.map((b) => {
      const s = getComputedStyle(b);
      return { n: b.textContent.trim(), hg: s.backgroundColor, rahmen: s.borderColor,
               titel: (b.title || "").slice(0, 60) };
    });
  });

const kopf = () =>
  page.evaluate(() => {
    const b = [...document.querySelectorAll("button[aria-expanded]")][0];
    if (!b) return "(kein Aufklappknopf)";
    const s = getComputedStyle(b);
    return `Rahmen=${s.borderColor} Flaeche=${s.backgroundColor} Text=${(b.textContent||"").trim().slice(0,30)}`;
  });

try {
  await page.goto(`${BASIS}/notes`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2500);
  // Assistenten öffnen
  for (const t of ["Assistent", "assistant"]) {
    const b = page.locator(`[title*="${t}" i]`).first();
    if (await b.count()) { await b.click(); break; }
  }
  await page.waitForTimeout(2000);
  console.log("Aufklappknopf:", await kopf());
  console.log("Marker:", JSON.stringify(await marker(), null, 0));

  // Neue Unterhaltung -> muss die aktive werden
  const vorher = (await marker()).length;
  const plus = page.locator('[title*="Neue Unterhaltung" i], [title*="New conversation" i]').first();
  if (await plus.count()) {
    await plus.click();
    await page.waitForTimeout(1800);
    const m = await marker();
    console.log(`nach "neue Unterhaltung": ${vorher} -> ${m.length} Marker`);
    const aktiv = m.filter((x) => !/transparent|rgba\(0, 0, 0, 0\)/.test(x.rahmen));
    console.log("  aktiver Marker (blauer Rahmen):", JSON.stringify(aktiv));
    // Rechtsklick auf den letzten -> archiviert (leer => geloescht)
    const reihe = page.locator("button").filter({ hasText: /^\d+$/ });
    const n = await reihe.count();
    if (n) {
      await reihe.nth(n - 1).click({ button: "right" });
      await page.waitForTimeout(1800);
      console.log(`nach Rechtsklick: ${(await marker()).length} Marker`);
    }
  }
  await page.screenshot({ path: "/w/assistant-marker.png" });
} finally {
  await browser.close();
}
