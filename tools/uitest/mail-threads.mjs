// The mailbox: conversations in the list, and the two new habits of an account.
//
// What a screenshot cannot say: whether the switch really groups (fewer rows, a count on
// them), whether unfolding shows the members, and whether "mark everything read" still puts
// a dialog in the way.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";
const BASIS = process.env.BASIS || "http://frontend";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();

const browser = await chromium.launch({ executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome" });
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const page = await ctx.newPage();
page.on("pageerror", (e) => console.log("  SEITENFEHLER:", String(e).slice(0, 200)));

const zeilen = () =>
  page.evaluate(() => {
    const rows = [...document.querySelectorAll('[class*="divide-y"] > div')];
    return rows.length;
  });

const kopfzeile = () =>
  page.evaluate(() => {
    const b = [...document.querySelectorAll("button[aria-pressed]")]
      .find((x) => /verlauf|thread/i.test(x.textContent || ""));
    const zahl = [...document.querySelectorAll("span")]
      .map((s) => (s.textContent || "").trim())
      .find((t) => /^\d+\+?\s+(Verläufe|Nachrichten|conversations|Messages)$/i.test(t));
    return { schalter: b ? b.textContent.trim() : "(keiner)",
             gedrueckt: b ? b.getAttribute("aria-pressed") : "?", zahl: zahl || "(keine)" };
  });

try {
  await page.goto(`${BASIS}/mail`, { waitUntil: "networkidle" });
  await page.waitForTimeout(4000);
  console.log("flach:      ", JSON.stringify(await kopfzeile()), "Zeilen:", await zeilen());

  const schalter = page.locator("button[aria-pressed]").filter({ hasText: /verläufe|threads/i }).first();
  if (!(await schalter.count())) throw new Error("Schalter für Verläufe nicht gefunden");
  await schalter.click();
  await page.waitForTimeout(4000);
  console.log("gruppiert:  ", JSON.stringify(await kopfzeile()), "Zeilen:", await zeilen());

  // Ein Verlauf mit mehreren Nachrichten: aufklappen muss Zeilen bringen.
  const pfeil = page.locator('button[aria-expanded="false"]').filter({ hasText: "▸" }).first();
  if (await pfeil.count()) {
    const vor = await zeilen();
    await pfeil.click();
    await page.waitForTimeout(800);
    const nach = await zeilen();
    console.log(`aufgeklappt: ${vor} -> ${nach} Zeilen (+${nach - vor})`);
  } else {
    console.log("aufgeklappt: kein Verlauf mit mehreren Nachrichten sichtbar");
  }
  await page.screenshot({ path: "/w/mail-threads.png" });

  // Ordner als gelesen markieren: darf nicht mehr fragen.
  const menue = page.locator('[title*="Ordner" i], [aria-label*="Ordner" i]').first();
  console.log("Menü-Knopf vorhanden:", await menue.count());
} finally {
  await browser.close();
}
