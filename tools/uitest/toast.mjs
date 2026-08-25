// Kommt die Kurzmeldung unten rechts, und geht sie wieder?
// „Alle gelesen" auf einem Ordner ist echt, deshalb wird die Anfrage abgefangen.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";
const BASIS = process.env.BASIS || "http://frontend";
const token = readFileSync("/w/tok.txt", "utf8").trim();
const browser = await chromium.launch();
const seite = await browser.newPage({ viewport: { width: 1600, height: 900 } });
await seite.addInitScript((t) => localStorage.setItem("traccoon_token", t), token);
await seite.route("**/folders/read-all", async (route) => {
  await route.fulfill({ status: 200, contentType: "application/json",
    body: JSON.stringify({ marked: 1 }) });
});
await seite.goto(`${BASIS}/mail`, { waitUntil: "networkidle" });
await seite.waitForTimeout(2500);

// Das Menü eines Ordners, nicht das eines Postfachs: nur dort steht „Alle gelesen".
// Beide Menütitel enden auf „machen kann", nur das des Postfachs nennt es beim Namen.
await seite.locator('button[title*="machen kann"]:not([title*="Postfach"])')
  .first().click({ force: true });
await seite.waitForTimeout(400);
await seite.getByText("Alle gelesen").first().click();
await seite.waitForTimeout(400);
await seite.getByText("Markieren", { exact: true }).first().click();
await seite.waitForTimeout(900);

const da = await seite.locator("[role=status]").filter({ hasText: /markiert|gelesen/ }).count();
const platz = await seite.evaluate(() => {
  const t = document.querySelector("[role=status]");
  if (!t) return null;
  const r = t.getBoundingClientRect();
  return { unten: Math.round(window.innerHeight - r.bottom),
           rechts: Math.round(window.innerWidth - r.right), text: t.innerText.slice(0, 40) };
});
// Die anderen drei Töne dazu, damit man sie nebeneinander sieht.
await seite.evaluate(() => {
  const w = window;
  const zeigen = w.__toast;
  if (zeigen) {
    zeigen("Etwas ist schiefgegangen", "error");
    zeigen("Das solltest du dir ansehen", "warning");
    zeigen("Eine Auskunft", "info");
  }
});
await seite.waitForTimeout(400);
await seite.screenshot({ path: "/w/toast.png" });
await seite.waitForTimeout(5000);
const spaeter = await seite.locator("[role=status]").filter({ hasText: /markiert|gelesen/ }).count();
console.log(JSON.stringify({ sofortDa: da, platz, nachFuenfSekunden: spaeter }));
await browser.close();
