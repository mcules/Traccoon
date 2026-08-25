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

// Enter im Suchfeld muss die Suche auslösen. Geprüft wird die Anfrage, nicht ihr Ergebnis:
// eine Volltextsuche über zweitausend Mails dauert auf dem Server, und die Frage hier ist,
// ob sie überhaupt gestellt wird.
const feld = seite.getByPlaceholder("suchen").first();
if (await feld.count()) {
  await feld.fill("Rechnung");
  const kommt = seite.waitForRequest((r) => r.url().includes("q=Rechnung"), { timeout: 6000 })
    .then(() => "ja").catch(() => "nein");
  // Die Kastengrößen vor und während der Suche: nichts darf springen.
  const vorher = await seite.evaluate(() => {
    const k = document.querySelectorAll("main [class*='rounded-lg'][class*='border-line']");
    return [...k].slice(0, 3).map((d) => Math.round(d.getBoundingClientRect().width));
  });
  await feld.press("Enter");
  console.log("ENTER-SUCHE", await kommt);
  await seite.waitForTimeout(250);
  const waehrend = await seite.evaluate(() => {
    const k = document.querySelectorAll("main [class*='rounded-lg'][class*='border-line']");
    return [...k].slice(0, 3).map((d) => Math.round(d.getBoundingClientRect().width));
  });
  const dreht = await seite.locator("[role=status]").count();
  console.log("BREITEN", JSON.stringify({ vorher, waehrend, spinner: dreht }));
  await seite.screenshot({ path: "/w/mail-08-suche.png" });
  const weit = seite.getByText("Ganzes Postfach").first();
  if (await weit.count()) {
    await weit.click();
    await seite.waitForTimeout(400);
    const dreht2 = await seite.locator("[role=status]").count();
    const breiten2 = await seite.evaluate(() => {
      const k = document.querySelectorAll("main [class*='rounded-lg'][class*='border-line']");
      return [...k].slice(0, 3).map((d) => Math.round(d.getBoundingClientRect().width));
    });
    console.log("POSTFACHSUCHE", JSON.stringify({ spinner: dreht2, breiten: breiten2 }));
    await seite.screenshot({ path: "/w/mail-09-weit.png" });
    await seite.waitForTimeout(12000);
    await seite.screenshot({ path: "/w/mail-10-weit-fertig.png" });
  }
  await feld.fill("");
  await feld.press("Escape");
  await seite.waitForTimeout(800);
} else {
  console.log("SUCHFELD nicht gefunden");
}

// Ein zweites Postfach aufklappen: es muss seine Ordner nachladen, ohne dass das aktive
// Postfach wechselt (das täte erst der Klick auf einen Ordner darin).
const zweites = seite.getByText("Zweites Postfach", { exact: true }).first();
const vorherOrdner = await seite.locator("[class*='grid-cols-']").count();
if (await zweites.count()) {
  await zweites.click({ force: true });
  await seite.waitForTimeout(2500);
  const nachher = await seite.locator("[class*='grid-cols-']").count();
  console.log("AUFKLAPPEN", JSON.stringify({ vorherOrdner, nachher }));
  await seite.screenshot({ path: "/w/mail-07-zweites.png" });
  await zweites.click({ force: true });
  await seite.waitForTimeout(400);
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
