// Sieht man eine ungelesene Nachricht in einem Verlauf?
//
// Zwei Zahlen nebeneinander (Länge des Verlaufs, davon ungelesen) gingen unter. Jetzt trägt
// die Zeile einen Streifen in der Hausfarbe und ein benanntes Etikett. Das misst, ob beides
// wirklich am richtigen Element hängt.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";
const BASIS = process.env.BASIS || "http://frontend";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();
const ORDNER = process.env.ORDNER || "Junk";

const browser = await chromium.launch({ executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome" });
const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const page = await ctx.newPage();
page.on("pageerror", (e) => console.log("  SEITENFEHLER:", String(e).slice(0, 200)));

try {
  await page.goto(`${BASIS}/mail`, { waitUntil: "networkidle" });
  await page.waitForTimeout(4000);

  // Die Einstellung kommt jetzt vom Server: der Schalter muss schon an sein.
  const schalter = page.locator("button[aria-pressed]").filter({ hasText: /Verläufe|Threads/i }).first();
  console.log("Schalter gedrückt:", await schalter.getAttribute("aria-pressed"));
  console.log("Kopfzahl:", await page.evaluate(() =>
    [...document.querySelectorAll("span")].map((s) => (s.textContent || "").trim())
      .find((t) => /^\d+\+?\s+\S+$/.test(t)) || "(keine)"));

  const ordner = page.locator("button").filter({ hasText: new RegExp(ORDNER) }).first();
  if (await ordner.count()) { await ordner.click(); await page.waitForTimeout(3500); }
  else console.log("Ordner nicht gefunden:", ORDNER);

  const zeilen = await page.evaluate(() => {
    // Die Nachrichtenliste ist die divide-y-Gruppe, deren Zeilen ein Kästchen tragen.
    const gruppen = [...document.querySelectorAll('[class*="divide-y"]')]
      .filter((g) => g.querySelector('input[type=checkbox]'));
    const rows = gruppen.length ? [...gruppen[gruppen.length - 1].children] : [];
    return rows.slice(0, 10).map((r) => {
      const box = r.matches("[class*=border-l-2]") ? r : r.querySelector("[class*=border-l-2]") || r;
      const s = getComputedStyle(box);
      const etiketten = [...r.querySelectorAll("span")].map((x) => (x.textContent || "").trim())
        .filter((t) => /neu|new/i.test(t));
      return { streifen: s.borderLeftColor, breite: s.borderLeftWidth,
               etikett: etiketten[0] || "", text: (r.textContent || "").trim().slice(0, 40) };
    });
  });
  zeilen.forEach((z) => console.log(`  ${JSON.stringify(z.text)} | Streifen ${z.breite} ${z.streifen} | ${z.etikett || "—"}`));
  await page.screenshot({ path: "/w/mail-thread-unread.png" });
} finally {
  await browser.close();
}
