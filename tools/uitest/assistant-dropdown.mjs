// Does opening the conversation list push the conversation down?
//
// It used to sit between the head and the log as a sibling, so unfolding it
// grew the column and everything below moved. This measures the log instead of
// looking at it: same top, same height, before and after.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";
const BASIS = process.env.BASIS || "http://frontend";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();

const browser = await chromium.launch({ executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome" });
const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 } });
await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const page = await ctx.newPage();
page.on("pageerror", (e) => console.log("  SEITENFEHLER:", String(e).slice(0, 200)));

const log = () =>
  page.evaluate(() => {
    const l = document.querySelector('[data-assistant="log"]');
    if (!l) return null;
    const r = l.getBoundingClientRect();
    const erste = l.querySelector('[data-assistant="turn"]');
    return { top: Math.round(r.top), hoehe: Math.round(r.height),
             ersteBlase: erste ? Math.round(erste.getBoundingClientRect().top) : -1 };
  });

const liste = () =>
  page.evaluate(() => {
    const k = [...document.querySelectorAll("div")].find(
      (d) => d.className.includes("top-full") && d.className.includes("absolute"));
    if (!k) return null;
    const s = getComputedStyle(k);
    const r = k.getBoundingClientRect();
    return { lage: s.position, z: s.zIndex, schatten: s.boxShadow.slice(0, 40),
             flaeche: s.backgroundColor, rand: s.borderRadius,
             breite: Math.round(r.width), hoehe: Math.round(r.height),
             deckt: Math.round(r.bottom) };
  });

try {
  await page.goto(`${BASIS}/notes`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2500);
  for (const t of ["Assistent", "assistant"]) {
    const b = page.locator(`[title*="${t}" i]`).first();
    if (await b.count()) { await b.click(); break; }
  }
  await page.waitForTimeout(2500);

  const zu = await log();
  console.log("zugeklappt: ", JSON.stringify(zu));

  const knopf = page.locator("button[aria-expanded]").first();
  await knopf.click();
  await page.waitForTimeout(600);
  const auf = await log();
  console.log("aufgeklappt:", JSON.stringify(auf));
  console.log("Liste:      ", JSON.stringify(await liste()));
  await page.screenshot({ path: "/w/dropdown-auf.png" });

  const dz = auf.top - zu.top, dh = auf.hoehe - zu.hoehe;
  console.log(`\nERGEBNIS: Log top ${zu.top} -> ${auf.top} (${dz >= 0 ? "+" : ""}${dz}), ` +
    `Hoehe ${zu.hoehe} -> ${auf.hoehe} (${dh >= 0 ? "+" : ""}${dh})`);
  console.log(dz === 0 && dh === 0 ? "  nichts verschoben" : "  VERSCHOBEN");

  // Klick daneben schliesst wieder
  await page.mouse.click(400, 500);
  await page.waitForTimeout(500);
  console.log("nach Klick daneben:", (await liste()) ? "noch offen" : "zu");
} finally {
  await browser.close();
}
