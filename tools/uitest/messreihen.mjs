// Kurze Browser-Probe der Messreihen-Ansicht: steht die Reihe da, stimmt die Prognose,
// und zeichnet der Aufklapper eine Linie?
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();
const ok = (was, gut, detail = "") =>
  console.log(`${gut ? "OK  " : "FEHL"} ${was}${detail ? " — " + detail : ""}`);

const browser = await chromium.launch({
  executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome",
});
const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const page = await ctx.newPage();
const fehler = [];
page.on("pageerror", (e) => fehler.push(String(e).slice(0, 160)));

try {
  await page.goto(`${BASIS}/processes/messreihen`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1800);
  const akku = page.getByText("akku.shelter").first();
  ok("Reihe steht in der Übersicht", await akku.isVisible().catch(() => false));
  const prognose = await page.getByText(/leer am \d{4}-\d{2}-\d{2}/i).first()
    .textContent().catch(() => "");
  ok("Prognose wird angezeigt", !!prognose, (prognose || "").trim());
  await page.screenshot({ path: "/w/12-messreihen.png" });

  await akku.click().catch(() => {});
  // Der Aufklapper hängt am Namen, nicht am Schlüssel.
  await page.getByRole("button", { name: /Akku Shelter/i }).first().click().catch(() => {});
  await page.waitForTimeout(1600);
  const linie = await page.locator("svg polyline").count();
  ok("Verlauf wird gezeichnet", linie > 0, `${linie} Linie(n)`);
  await page.screenshot({ path: "/w/13-verlauf.png" });
  ok("Keine JavaScript-Fehler", fehler.length === 0, fehler.slice(0, 1).join(""));
} finally {
  await browser.close();
}
