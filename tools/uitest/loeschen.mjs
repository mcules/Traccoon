// Verschwindet eine gelöschte Mail sofort aus der Liste, und kommt sie zurück, wenn es
// schiefgeht?
//
// Die Anfrage wird abgefangen und erreicht den Server nie: geprüft wird die Oberfläche, und
// im echten Postfach bleibt jede Mail, wo sie ist.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const token = readFileSync("/w/tok.txt", "utf8").trim();
const browser = await chromium.launch();
const seite = await browser.newPage({ viewport: { width: 1600, height: 900 } });
await seite.addInitScript((t) => localStorage.setItem("traccoon_token", t), token);

// Die Anfrage wird aufgehalten und dann mit einem Erfolg beantwortet, OHNE dass der Server
// sie je sieht. Ein Abbruch wäre zu schnell zurück: der Fehlerfall holt die Liste sofort neu,
// und dann kann man nicht mehr unterscheiden, ob die Zeile nie weg war oder schon wieder da.
let versucht = 0;
await seite.route("**/messages/*/delete", async (route) => {
  versucht++;
  await new Promise((r) => setTimeout(r, 3000));
  await route.fulfill({ status: 204, body: "" });
});

await seite.goto(`${BASIS}/mail`, { waitUntil: "networkidle" });
await seite.waitForTimeout(2500);

const zeilen = () => seite.locator("main input[type=checkbox]").count();
// Nur Zeilen mit Kreuzchen sind Mailzeilen: `.divide-y` hat auch der Ordnerbaum.
const mailzeilen = seite.locator("main .divide-y > div")
  .filter({ has: seite.locator("input[type=checkbox]") });
const erste = await mailzeilen.nth(0).innerText();
const vorher = await zeilen();

// Die erste Mail öffnen und löschen.
await mailzeilen.nth(0).click();
await seite.waitForTimeout(2000);
await seite.getByText("Löschen", { exact: false }).first().click();
await seite.waitForTimeout(150);
const sofort = await zeilen();
const nochDa = (await seite.locator("main").innerText()).includes(erste.split("\n")[0]);

// Nach der Antwort gleicht die Liste mit dem Postfach ab, und dort liegt die Mail noch:
// sie kommt also zurück. Genau das beweist, dass das Verschwinden vorher unsere Arbeit war.
await seite.waitForTimeout(6000);
const danach = await zeilen();
const wiederDa = (await seite.locator("main").innerText()).includes(erste.split("\n")[0]);

console.log(JSON.stringify({ betreff: erste.split("\n")[0].slice(0, 40), versucht,
  vorher, sofort, danach, sofortNochSichtbar: nochDa, danachWiederDa: wiederDa }));
await seite.screenshot({ path: "/w/loeschen.png" });
await browser.close();
