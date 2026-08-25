// Gehen bei einer Massenaktion wirklich die angekreuzten Nummern mit?
// Die Anfrage wird abgefangen und selbst beantwortet: im Postfach passiert nichts.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";
const BASIS = process.env.BASIS || "http://frontend";
const token = readFileSync("/w/tok.txt", "utf8").trim();
const browser = await chromium.launch();
const seite = await browser.newPage({ viewport: { width: 1600, height: 900 } });
await seite.addInitScript((t) => localStorage.setItem("traccoon_token", t), token);

let gesendet = null;
await seite.route("**/messages/bulk", async (route) => {
  gesendet = JSON.parse(route.request().postData() || "{}");
  await new Promise((r) => setTimeout(r, 500));
  await route.fulfill({ status: 200, contentType: "application/json",
    body: JSON.stringify({ done: gesendet.uids?.length || 0, action: gesendet.action }) });
});

await seite.goto(`${BASIS}/mail`, { waitUntil: "networkidle" });
await seite.waitForTimeout(2500);

const kaesten = seite.locator("main .divide-y input[type=checkbox]");
await kaesten.nth(0).click();
await kaesten.nth(1).click();
await kaesten.nth(2).click();
await seite.waitForTimeout(400);
const vorher = await seite.locator("main .divide-y > div").count();

await seite.getByText("Gelesen", { exact: true }).first().click();
await seite.waitForTimeout(1200);
const gelesen = gesendet;

gesendet = null;
await kaesten.nth(0).click();
await kaesten.nth(1).click();
await seite.waitForTimeout(300);
await seite.getByText("🗑 Löschen", { exact: false }).first().click();
await seite.waitForTimeout(400);
const nachLoeschen = await seite.locator("main .divide-y > div").count();
await seite.waitForTimeout(1200);

console.log(JSON.stringify({
  gelesen: gelesen && { action: gelesen.action, uids: gelesen.uids?.length, flag: gelesen.flag },
  geloescht: gesendet && { action: gesendet.action, uids: gesendet.uids?.length },
  zeilenVorher: vorher, zeilenNachLoeschen: nachLoeschen }));
await seite.screenshot({ path: "/w/mehrfach.png" });
await browser.close();
