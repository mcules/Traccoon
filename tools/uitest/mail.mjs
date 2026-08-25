// Browser probe for the mailbox: three columns, own scrolling, folder menu, selection.
//
// Deliberately careful with the data behind it: this runs against a real mailbox. The probe
// opens only a message that is already read (three seconds open would mark an unread one),
// and it clicks nothing that moves or deletes anything.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const token = readFileSync("/w/tok.txt", "utf8").trim();

const browser = await chromium.launch();
const seite = await browser.newPage({ viewport: { width: 1600, height: 900 } });
await seite.addInitScript((t) => localStorage.setItem("traccoon_token", t), token);

const meldungen = [];
seite.on("console", (m) => { if (m.type() === "error") meldungen.push(m.text()); });
seite.on("pageerror", (e) => meldungen.push("pageerror: " + e.message));

await seite.goto(`${BASIS}/mail`, { waitUntil: "networkidle" });
await seite.waitForTimeout(2500);
await seite.screenshot({ path: "/w/mail-01-breit.png" });

// Scrollt die Seite selbst, oder scrollen die Spalten?
const mass = await seite.evaluate(() => {
  const doc = document.documentElement;
  const kasten = [...document.querySelectorAll("div")]
    .filter((d) => d.scrollHeight > d.clientHeight + 4 && getComputedStyle(d).overflowY === "auto")
    .map((d) => ({ h: d.clientHeight, scroll: d.scrollHeight, text: (d.innerText || "").slice(0, 30) }));
  return { seite_scrollt: doc.scrollHeight > doc.clientHeight + 4, spalten: kasten.length,
           kasten: kasten.slice(0, 5) };
});
console.log("SCROLL", JSON.stringify(mass));

// Das Ordnermenü
const zeile = seite.locator("li, [class*='px-3']").filter({ hasText: "Posteingang" }).first();
await zeile.hover().catch(() => {});
await seite.waitForTimeout(300);
const punkte = seite.locator("button[title*='machen kann'], button[title*='can do with']").first();
if (await punkte.count()) {
  await punkte.click({ force: true });
  await seite.waitForTimeout(400);
  await seite.screenshot({ path: "/w/mail-02-menue.png" });
  await seite.keyboard.press("Escape").catch(() => {});
  await seite.mouse.click(1200, 60);
  await seite.waitForTimeout(300);
} else {
  console.log("MENUE nicht gefunden");
}

// Auswahl: die erste Zeile ankreuzen, nur ansehen, nichts auslösen
const kaesten = seite.locator("input[type=checkbox]");
const anzahl = await kaesten.count();
console.log("CHECKBOXEN", anzahl);
if (anzahl > 2) {
  await kaesten.nth(1).click();
  await kaesten.nth(2).click();
  await seite.waitForTimeout(400);
  await seite.screenshot({ path: "/w/mail-03-auswahl.png" });
  await kaesten.nth(1).click();
  await kaesten.nth(2).click();
}

// Eine BEREITS GELESENE Nachricht öffnen (keine mit „neu"-Etikett)
const gelesen = await seite.evaluate(() => {
  const zeilen = [...document.querySelectorAll("div")].filter((d) =>
    d.className.includes("min-w-0 flex-1") && d.innerText && d.innerText.includes("\n"));
  return zeilen.length;
});
console.log("ZEILEN", gelesen);
// Eine Zeile aus der LISTE, nicht aus dem Ordnerbaum: der Betreff steht darin.
const kandidat = seite.getByText("Ein Betreff").first();
console.log("KANDIDAT", await kandidat.count());
if (await kandidat.count()) {
  await kandidat.click({ force: true }).catch(() => {});
  await seite.waitForTimeout(2500);
  await seite.screenshot({ path: "/w/mail-04-lesen.png" });
}

// Die Naht zwischen Liste und Nachricht ziehen, und sie muss den Neuladen überleben.
const naht = seite.locator("[role=separator]").first();
if (await naht.count()) {
  const vorher = await seite.evaluate(() =>
    document.querySelector("[role=separator]").previousElementSibling.getBoundingClientRect().width);
  const kasten = await naht.boundingBox();
  await seite.mouse.move(kasten.x + 4, kasten.y + 200);
  await seite.mouse.down();
  await seite.mouse.move(kasten.x + 204, kasten.y + 200, { steps: 10 });
  await seite.mouse.up();
  await seite.waitForTimeout(300);
  const nachher = await seite.evaluate(() =>
    document.querySelector("[role=separator]").previousElementSibling.getBoundingClientRect().width);
  await seite.screenshot({ path: "/w/mail-06-naht.png" });
  await seite.reload({ waitUntil: "networkidle" });
  await seite.waitForTimeout(2000);
  const nachReload = await seite.evaluate(() =>
    document.querySelector("[role=separator]")?.previousElementSibling.getBoundingClientRect().width);
  console.log("NAHT", JSON.stringify({ vorher, nachher, nachReload }));
} else {
  console.log("NAHT nicht gefunden");
}

// Schmaler: zwei Spalten
await seite.setViewportSize({ width: 1100, height: 900 });
await seite.waitForTimeout(600);
await seite.screenshot({ path: "/w/mail-05-schmal.png" });

console.log("FEHLER", JSON.stringify(meldungen.slice(0, 5)));
await browser.close();
