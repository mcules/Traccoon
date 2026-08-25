// Zeigt die Beispielordner-Mail ihre Bilder, ohne dass jemand etwas erlauben muss?
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";
const BASIS = process.env.BASIS || "http://frontend";
const token = readFileSync("/w/tok.txt", "utf8").trim();
const browser = await chromium.launch();
const seite = await browser.newPage({ viewport: { width: 1600, height: 900 } });
await seite.addInitScript((t) => localStorage.setItem("traccoon_token", t), token);
await seite.goto(`${BASIS}/mail`, { waitUntil: "networkidle" });
await seite.waitForTimeout(2500);

// In den Beispielordner-Ordner: erst „Sonstiges" aufklappen.
for (const name of ["Sonstiges", "Beispielordner"]) {
  const ziel = seite.getByText(name, { exact: true }).first();
  if (await ziel.count()) {
    const zeile = ziel.locator("xpath=ancestor::div[contains(@class,'grid-cols-')][1]");
    if (name === "Sonstiges") await zeile.locator("button").first().click({ force: true });
    else await ziel.click({ force: true });
    await seite.waitForTimeout(1500);
  }
}
const erste = seite.locator("main .divide-y > div")
  .filter({ has: seite.locator("input[type=checkbox]") }).nth(0);
await erste.click();
await seite.waitForTimeout(3000);

const rahmen = seite.frameLocator("iframe").first();
const bilder = await rahmen.locator("img").evaluateAll((imgs) => imgs.map((i) => ({
  quelle: (i.getAttribute("src") || "(kein src)").slice(0, 24),
  breite: i.naturalWidth, hoehe: i.naturalHeight,
}))).catch((e) => [{ fehler: String(e).slice(0, 60) }]);
const hinweis = (await seite.locator("main").innerText()).includes("Bilder blockiert");
console.log(JSON.stringify({ bilder: bilder.slice(0, 4), hinweisBlockiert: hinweis }));
await seite.screenshot({ path: "/w/beispielordner.png" });
await browser.close();
