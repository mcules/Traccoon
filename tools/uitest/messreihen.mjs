// Probe der Messreihen-Ansicht: Übersicht, Detail mit Zeitraum, Prognosegerade,
// Wertetabelle und das Entfernen eines einzelnen Ausreißers.
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

  await page.getByRole("button", { name: /Prüfreihe Ansicht/i }).first().click();
  await page.waitForTimeout(1500);
  ok("Zeitraum lässt sich wählen",
     await page.getByRole("button", { name: "30 Tage" }).isVisible().catch(() => false));
  const gestrichelt = await page.locator("svg line[stroke-dasharray='6 5']").count();
  ok("Prognose wird als gestrichelte Verlängerung gezeichnet", gestrichelt > 0);
  const erklaerung = await page.getByText(/Fortschreibung dieser Punkte/i).first()
    .textContent().catch(() => "");
  ok("Die Gerade wird in Worten erklärt", !!erklaerung,
     (erklaerung || "").replace(/\s+/g, " ").trim().slice(0, 110));
  const zeilen = await page.locator("table tbody tr").count();
  ok("Wertetabelle steht darunter", zeilen >= 8, `${zeilen} Zeilen`);
  await page.screenshot({ path: "/w/21-detail.png" });

  // Ausreißer entfernen — die Prognose muss sich sichtbar ändern.
  const vorher = await page.getByText(/Güte/).first().textContent().catch(() => "");
  page.once("dialog", (d) => d.accept());
  await page.locator("table tbody tr", { hasText: "88" }).first()
    .locator("button").click();
  await page.waitForTimeout(2000);
  const nachher = await page.getByText(/Güte/).first().textContent().catch(() => "");
  ok("Einzelner Wert lässt sich entfernen", vorher !== nachher,
     `${(vorher || "").trim()} → ${(nachher || "").trim()}`);

  await page.getByRole("button", { name: "7 Tage" }).click();
  await page.waitForTimeout(1500);
  const wenig = await page.locator("table tbody tr").count();
  ok("Kürzerer Zeitraum zeigt weniger Werte", wenig < zeilen, `${wenig} Zeilen`);
  await page.screenshot({ path: "/w/22-nach-loeschen.png" });

  ok("Keine JavaScript-Fehler", fehler.length === 0, fehler.slice(0, 1).join(""));
} finally {
  await browser.close();
}
