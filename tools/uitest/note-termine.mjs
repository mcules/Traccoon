// How an appointment line is drawn, and that nothing moves while it is.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";
const BASIS = process.env.BASIS || "http://frontend";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();
const NOTIZ = process.env.NOTIZ || "05 Daily Notes/2026/09/2026-09-09.md";

const browser = await chromium.launch({ executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome" });
const ctx = await browser.newContext({ viewport: { width: 1100, height: 900 } });
await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const page = await ctx.newPage();
page.on("pageerror", (e) => console.log("  SEITENFEHLER:", String(e).slice(0, 200)));

const teile = () =>
  page.evaluate(() => {
    const n = (s) => document.querySelectorAll(s).length;
    const erste = document.querySelector(".cm-appt-cal, .appt-cal");
    const stil = erste ? getComputedStyle(erste) : null;
    return {
      zeit: n(".cm-appt-time, .appt-time"),
      ganztags: n(".cm-appt-allday, .appt-allday"),
      kalender: n(".cm-appt-cal, .appt-cal"),
      trenner_sichtbar: [...document.querySelectorAll(".cm-appt-sep")]
        .filter((e) => getComputedStyle(e).display !== "none").length,
      badge: stil ? `${stil.borderRadius} ${stil.backgroundColor} ${stil.fontSize}` : "(keins)",
      kinder: n(".cm-appt-child, .appt-child"),
      spalten: [...document.querySelectorAll(".cm-appt-time, .cm-appt-allday")].map((e) => {
        const r = e.getBoundingClientRect();
        const s = getComputedStyle(e);
        return `${JSON.stringify((e.textContent||"").trim())} breit=${Math.round(r.width)} `
          + `rechts=${Math.round(r.right)} min=${s.minWidth} font=${s.fontSize}`;
      }),
      // Wo die Texte tatsaechlich anfangen: Termin-Titel und die Notizen darunter.
      x: [...document.querySelectorAll(".cm-line")]
        .filter((l) => /Vostura|Privat|Notizen|Exception/.test(l.textContent || ""))
        .map((l) => {
          const s = getComputedStyle(l);
          const links = Math.round(l.getBoundingClientRect().left
            + parseFloat(s.paddingLeft || "0"));
          return `${links}px ${JSON.stringify((l.textContent || "").trim().slice(0, 22))}`;
        }),
    };
  });

const hin = async (modus) => {
  await page.evaluate((m) => {
    const b = [...document.querySelectorAll("button")].find((x) => (x.title || "").toLowerCase().includes(m));
    b?.click();
  }, modus);
  await page.waitForTimeout(900);
};

try {
  await page.goto(`${BASIS}/note/${encodeURI(NOTIZ)}`, { waitUntil: "networkidle" });
  await page.waitForSelector(".cm-content");
  await page.waitForTimeout(3500);
  for (let i = 0; i < 40; i++) {
    const da = await page.evaluate(() => {
      const sc = document.querySelector(".cm-scroller").getBoundingClientRect();
      return [...document.querySelectorAll(".cm-line")].some((l) => {
        const r = l.getBoundingClientRect();
        return /Vostura|Privat/.test(l.textContent || "") && r.top > sc.top + 40 && r.bottom < sc.bottom - 200;
      });
    });
    if (da) break;
    await page.evaluate(() => { const s = document.querySelector(".cm-scroller"); s.scrollTop += 200; s.dispatchEvent(new Event("scroll")); });
    await page.waitForTimeout(150);
  }
  await page.waitForTimeout(600);
  console.log("Live-Ansicht:", JSON.stringify(await teile()));
  await page.screenshot({ path: "/w/termine-live.png" });
} finally {
  await browser.close();
}
