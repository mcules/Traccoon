// Probe of the measurement series view: overview, detail with a period, the forecast line,
// the value table, and dropping a single outlier.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();
const ok = (was, gut, detail = "") =>
  console.log(`${gut ? "OK  " : "FEHL"} ${was}${detail ? " — " + detail : ""}`);

const browser = await chromium.launch({
  executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome",
});
const ctx = await browser.newContext({ viewport: { width: 1400, height: 1100 } });
await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const page = await ctx.newPage();
const fehler = [];
page.on("pageerror", (e) => fehler.push(String(e).slice(0, 160)));

try {
  await page.goto(`${BASIS}/processes/messreihen`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);
  ok("Reihen stehen in der Übersicht",
     await page.getByText("akku.shelter").first().isVisible().catch(() => false));

  // Do not pin this to one test series: the test data is cleaned up now and then, and the
  // first series that exists does the job just as well.
  await page.locator('button:has-text("▸")').first().click();
  await page.waitForTimeout(1500);
  ok("Zeitraum lässt sich wählen",
     await page.getByRole("button", { name: "30 Tage" }).isVisible().catch(() => false));
  const zeilenGenug = (await page.locator("table tbody tr").count()) >= 3;
  const gestrichelt = await page.locator("svg line[stroke-dasharray='6 5']").count();
  if (zeilenGenug) ok("Prognose wird als gestrichelte Verlängerung gezeichnet", gestrichelt > 0);
  else console.log("--   Prognose — übersprungen, die Reihe hat zu wenige Punkte");
  const erklaerung = await page.getByText(/Fortschreibung dieser Punkte/i).first()
    .textContent().catch(() => "");
  if (zeilenGenug) ok("Die Gerade wird in Worten erklärt", !!erklaerung,
     (erklaerung || "").replace(/\s+/g, " ").trim().slice(0, 110));
  const zeilen = await page.locator("table tbody tr").count();
  ok("Wertetabelle steht darunter", zeilen >= 1, `${zeilen} Zeilen`);
  await page.screenshot({ path: "/w/21-detail.png" });

  // Drop an outlier, the forecast has to change visibly. That only works with a series that
  // holds several values: test series get cleaned up now and then, and a real series with a
  // single value is not a failure.
  if (zeilen >= 3) {
    const vorher = await page.getByText(/Güte/).first().textContent().catch(() => "");
    page.once("dialog", (d) => d.accept());
    await page.locator("table tbody tr").first().locator("button").click();
    await page.waitForTimeout(2000);
    const nachher = await page.getByText(/Güte/).first().textContent().catch(() => "");
    ok("Einzelner Wert lässt sich entfernen", vorher !== nachher,
       `${(vorher || "").trim()} → ${(nachher || "").trim()}`);
  } else {
    console.log(`--   Einzelner Wert lässt sich entfernen — übersprungen, nur ${zeilen} Werte in der Reihe`);
  }

  if (zeilenGenug) {
    await page.getByRole("button", { name: "7 Tage" }).click();
    await page.waitForTimeout(1500);
    const wenig = await page.locator("table tbody tr").count();
    ok("Kürzerer Zeitraum zeigt weniger Werte", wenig < zeilen, `${wenig} Zeilen`);
  } else {
    console.log("--   Kürzerer Zeitraum — übersprungen, zu wenige Werte");
  }
  await page.screenshot({ path: "/w/22-nach-loeschen.png" });

  ok("Keine JavaScript-Fehler", fehler.length === 0, fehler.slice(0, 1).join(""));
} finally {
  await browser.close();
}
