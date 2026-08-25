// Die Abo-Übersicht mit ihren zwei Reitern. Abgemeldet wird NICHT: die Anfrage wird
// abgefangen und selbst beantwortet, damit im echten Postfach kein Abo gekündigt wird.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const token = readFileSync("/w/tok.txt", "utf8").trim();
const browser = await chromium.launch();
const seite = await browser.newPage({ viewport: { width: 1600, height: 900 } });
await seite.addInitScript((t) => localStorage.setItem("traccoon_token", t), token);

let gefragt = 0;
await seite.route("**/newsletters/unsubscribe", async (route) => {
  gefragt++;
  await new Promise((r) => setTimeout(r, 800));
  await route.fulfill({ status: 200, contentType: "application/json",
    body: JSON.stringify({ done: true, way: "one_click", detail: "HTTP 200 (Sonde)" }) });
});

await seite.goto(`${BASIS}/mail`, { waitUntil: "networkidle" });
await seite.waitForTimeout(2500);
await seite.locator("button[title*='privat']").first().click({ force: true });
await seite.waitForTimeout(300);
await seite.getByText("Newsletter-Abos").first().click();
await seite.waitForTimeout(18000);

const zeilen = () => seite.locator("[role=dialog] [class*='divide-y'] > div").count();
const vorher = await zeilen();
const ersteZeile = await seite.locator("[role=dialog] [class*='divide-y'] > div").nth(0).innerText();
await seite.getByText("Abmelden", { exact: true }).first().click();
await seite.waitForTimeout(1500);
const nachher = await zeilen();
const nochDa = (await seite.locator("[role=dialog]").innerText())
  .includes(ersteZeile.split("\n")[0]);
await seite.screenshot({ path: "/w/abos-01-nach-abmelden.png" });

// Der Reiter mit der Historie.
await seite.getByText("Abgemeldet", { exact: false }).first().click();
await seite.waitForTimeout(800);
const historie = await seite.locator("[role=dialog] [class*='divide-y'] > div").allInnerTexts();
await seite.screenshot({ path: "/w/abos-02-historie.png" });
console.log(JSON.stringify({ gefragt, vorher, nachher, nochInListe: nochDa,
  historie: historie.slice(0, 3).map((t) => t.replace(/\n/g, " · ").slice(0, 80)) }));
await browser.close();
