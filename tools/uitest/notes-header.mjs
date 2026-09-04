// Die Kopfzeile des Hauses steht auch ueber den Notizen, und der Assistent
// liegt nur noch dort — nicht mehr als eigener Reiter in der Notiz-Leiste.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";
const BASIS = process.env.BASIS || "http://frontend";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();
const ok = (was, gut, mehr = "") =>
  console.log(`${gut ? "OK  " : "FAIL"} ${was}${mehr ? " — " + mehr : ""}`);
const browser = await chromium.launch({
  executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome" });
const fehler = [];
try {
  for (const [breit, hoch] of [[1400, 950], [390, 780]]) {
    const ctx = await browser.newContext({ viewport: { width: breit, height: hoch } });
    await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
    const page = await ctx.newPage();
    page.on("pageerror", (e) => fehler.push(String(e).slice(0, 160)));
    await page.goto(`${BASIS}/notes`, { waitUntil: "domcontentloaded" });
    // Auf dem schmalen Fenster braucht das Notiz-Buendel spuerbar laenger; auf
    // "Loading…" zu messen misst die Ladeanzeige.
    await page.waitForSelector("header", { timeout: 30000 }).catch(() => {});
    await page.waitForTimeout(2500);
    const m = await page.evaluate(() => {
      const h = document.querySelector("header");
      const hb = h?.getBoundingClientRect();
      const mitte = h ? document.elementFromPoint(hb.left + hb.width / 2, hb.top + hb.height / 2) : null;
      const arbeit = document.querySelector(".workbench, [class*='workspace'], main");
      return {
        kopf: hb ? Math.round(hb.height) : -1,
        sichtbar: !!(h && mitte && (h === mitte || h.contains(mitte))),
        assistentKnopf: !!document.querySelector('header button[title="Assistent"]'),
        unten: arbeit ? Math.round(arbeit.getBoundingClientRect().top) : -1,
        querScroll: document.documentElement.scrollWidth > document.documentElement.clientWidth,
      };
    });
    ok(`${breit}px: die Kopfzeile steht da und wird nicht verdeckt`, m.sichtbar, `${m.kopf}px hoch`);
    ok(`${breit}px: der Assistent sitzt in der Kopfzeile`, m.assistentKnopf);
    ok(`${breit}px: die Arbeitsflaeche beginnt darunter`, m.unten >= m.kopf - 2, `bei ${m.unten}px`);
    ok(`${breit}px: nichts laeuft seitlich ueber`, !m.querScroll);
    const reiter = await page.locator('[title="Assistent"]').count();
    ok(`${breit}px: kein zweiter Assistenten-Weg in der Notiz-Leiste`, reiter === 1, `${reiter} Knoepfe`);
    await page.screenshot({ path: `/w/95-notes-header-${breit}.png` });
    await ctx.close();
  }
} catch (e) {
  ok("durchgelaufen", false, String(e).slice(0, 200));
} finally {
  ok("kein Fehler auf der Seite", fehler.length === 0, fehler.join(" | "));
  await browser.close();
}
