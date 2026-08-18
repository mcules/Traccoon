// Misst, was an der Bedienung messbar ist: Umbrüche, Tippziele, Schriftgrößen, Wege.
//
// Geschmack lässt sich nicht messen, das hier schon — und genau daran hakt es auf dem Handy:
// eine Tabelle, die 200 px über den Rand steht, ein Knopf mit 22 px Höhe, ein Hinweistext in
// 10 px. Die Zahlen aus diesem Lauf landen in befund-bedienbarkeit.json; ein zweiter Lauf
// danach zeigt, ob eine Änderung wirklich etwas gebracht hat statt nur anders auszusehen.
//
// Schwellen: 36 px ist die kleinste Fläche, die ein Daumen zuverlässig trifft (Apple nennt 44,
// Material 48 — 36 ist die Untergrenze, unter der es messbar hakt). 11 px ist die Grenze, ab
// der Fließtext auf einem Handy nicht mehr ohne Zoom lesbar ist.
import { chromium } from "playwright-core";
import { readFileSync, writeFileSync, existsSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();
const MARKE = process.env.MARKE || "lauf";
const PROJEKT = process.env.PROJEKT || "UNI";

// Jeder Reiter einzeln: die erste Fassung prüfte nur den Standardreiter jeder Seite und
// hielt deshalb die Administration für sauber, obwohl sechs ihrer acht Reiter Tabellen
// zeigen, die auf 390 px zusammengequetscht werden.
const SEITEN = [
  ["Projekte", "/"],
  ["Eingang", "/inbox"],
  ["Profil", "/profil"],
  ...["board", "list", "dashboard", "monitor", "workflows", "settings"]
    .map((t) => [`Projekt/${t}`, `/projects/${PROJEKT}?tab=${t}`]),
  ...["eigene", "standard", "betrieb", "ausloeser", "messreihen"]
    .map((t) => [`Prozesse/${t}`, `/processes/${t}`]),
  ...["secrets", "prefs", "processes", "destinations", "agents", "mcp", "jobs", "webhooks", "skills"]
    .map((t) => [`Einstellungen/${t}`, `/settings/${t}`]),
  ...["users", "cost", "models", "maintenance", "mail", "destinations", "artifacts", "translations"]
    .map((t) => [`Admin/${t}`, `/admin/${t}`]),
];
const BREITEN = [["Handy", 390, 844], ["Desktop", 1400, 900]];

const messen = ({ grenze, handy }) => {
  const doc = document.scrollingElement;
  const breite = window.innerWidth;
  const ueberstand = Math.max(0, doc.scrollWidth - breite);

  // Wer steht über den Rand? Nur der äußerste Übeltäter zählt, sonst meldet jedes Kind
  // seines Elternteils Fehler mit.
  const raus = [];
  for (const el of document.querySelectorAll("body *")) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (r.right > breite + 2 || r.left < -2) {
      const stil = getComputedStyle(el);
      if (stil.overflowX === "auto" || stil.overflowX === "scroll") continue;  // darf scrollen
      if (raus.some((v) => v.el.contains(el))) continue;
      raus.push({ el, name: el.tagName.toLowerCase() + (el.className && typeof el.className === "string"
        ? "." + el.className.trim().split(/\s+/).slice(0, 2).join(".") : ""),
        ueber: Math.round(r.right - breite) });
    }
  }

  // Tippziele: sichtbare Bedienelemente unter der Grenze (Daumen 36 px, Maus 24 px).
  const klein = [];
  for (const el of document.querySelectorAll(
    "button, a[href], select, input:not([type=hidden]), [role=button], summary")) {
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) continue;
    if (r.top > window.innerHeight * 3) continue;          // weit unten: zählt nicht mit
    const stil = getComputedStyle(el);
    if (stil.visibility === "hidden") continue;
    // Ein Link im Fließtext ist Text, kein Knopf — er soll nicht auf 36 px wachsen.
    if (el.tagName === "A" && stil.display === "inline") continue;
    // Ein Kästchen behält seine native Größe (sonst zeichnet der Browser einen weißen Klotz).
    // Getippt wird auf die Beschriftung daneben — sie ist die Fläche, die zählt.
    if (el.tagName === "INPUT" && (el.type === "checkbox" || el.type === "radio")) {
      const lab = el.closest("label");
      const lr = lab && lab.getBoundingClientRect();
      if (!lr || (lr.height >= grenze && lr.width >= 24)) continue;
    }
    if (r.height < grenze || r.width < 24) {
      klein.push({ name: (el.textContent || el.getAttribute("aria-label") || el.tagName).trim().slice(0, 30),
        h: Math.round(r.height), w: Math.round(r.width) });
    }
  }

  // Zu kleine Schrift in sichtbarem Text.
  const kleinschrift = new Map();
  for (const el of document.querySelectorAll("body *")) {
    if (!el.childNodes.length) continue;
    const eigenerText = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim().length > 2);
    if (!eigenerText) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    const px = parseFloat(getComputedStyle(el).fontSize);
    if (px < 11) kleinschrift.set(el.textContent.trim().slice(0, 40), Math.round(px * 10) / 10);
  }

  // Abgeschnitten statt gescrollt: ein Element, dessen Inhalt breiter ist als es selbst, ohne
  // dass man scrollen könnte. Das ist der schlimmere Fall — es sieht aus, als wäre alles da.
  const abgeschnitten = [];
  for (const el of document.querySelectorAll("body *")) {
    const s = getComputedStyle(el);
    if (s.overflowX === "auto" || s.overflowX === "scroll") continue;
    // `truncate` schneidet mit Absicht ab und zeigt „…" — das ist eine Lösung, kein Mangel.
    if (s.textOverflow === "ellipsis") continue;
    if (el.scrollWidth <= el.clientWidth + 24) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 40 || r.height < 12) continue;
    abgeschnitten.push({ el, name: el.tagName.toLowerCase() + " +"
      + (el.scrollWidth - el.clientWidth) + "px: "
      + (el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 30) });
  }

  // Tabellen mit mehr als zwei Spalten sind auf einem Handy keine Tabellen mehr: entweder
  // stehen sie über den Rand oder ihre Spalten quetschen sich auf ein Wort je Zeile.
  const tabellen = handy ? [...document.querySelectorAll("table")].filter((tab) => {
    const spalten = tab.querySelector("tr")?.children.length || 0;
    return spalten > 2 && tab.getBoundingClientRect().width > 200;
  }).map((tab) => `${tab.querySelector("tr")?.children.length} Spalten`) : [];

  // Textspalten, die schmaler sind als etwa zwanzig Zeichen: dort bricht jeder Satz zum
  // Wasserfall (die Modell-Seite zeigte 45 Zeilen à zwei Wörter).
  const wasserfall = [];
  for (const el of document.querySelectorAll("p, div, li, span")) {
    const txt = (el.textContent || "").trim();
    if (txt.length < 120) continue;
    const eigener = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim().length > 40);
    if (!eigener) continue;
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.width < 210 && r.height > 100) wasserfall.push(`${Math.round(r.width)}px breit`);
  }

  // Was seitwärts weggescrollt werden muss, findet auf einem Handy niemand. Tabellen sind
  // die üblichen Kandidaten: fünf Spalten passen nicht in 390 px.
  const seitwaerts = [...document.querySelectorAll("body *")].filter((el) => {
    const s = getComputedStyle(el);
    if (s.overflowX !== "auto" && s.overflowX !== "scroll") return false;
    return el.scrollWidth > el.clientWidth + 8 && el.getBoundingClientRect().height > 40;
  }).map((el) => (el.querySelector("table") ? "Tabelle: " : "") +
       el.tagName.toLowerCase() + " +" + (el.scrollWidth - el.clientWidth) + "px");

  return {
    // Nur der innerste Übeltäter zählt: ein zu breites Feld macht jeden seiner Vorfahren
    // ebenfalls „zu breit", und die Meldung „div ist zu breit" hilft niemandem.
    abgeschnitten: abgeschnitten
      .filter((v, _i, alle) => !alle.some((w) => w !== v && v.el.contains(w.el)))
      .slice(0, 5).map(({ name }) => name),
    tabellen,
    wasserfall: wasserfall.slice(0, 3),
    seitwaerts: seitwaerts.slice(0, 5),
    ueberstand,
    ueberlaeufer: raus.slice(0, 6).map(({ name, ueber }) => ({ name, ueber })),
    tippziele_klein: klein.length,
    tippziele_beispiele: klein.slice(0, 5),
    kleinschrift: kleinschrift.size,
    kleinschrift_beispiele: [...kleinschrift.entries()].slice(0, 4),
    // Wie viele Ziele erreicht man ohne vorher ein Menü zu öffnen?
    sichtbare_navigation: [...document.querySelectorAll("header a[href], header button")]
      .filter((el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; }).length,
    hoehe: Math.round(document.scrollingElement.scrollHeight),
  };
};

const browser = await chromium.launch({
  executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome",
});
const bericht = { marke: MARKE, seiten: {} };
let punkte = 0, maxPunkte = 0;

for (const [gname, breite, hoehe] of BREITEN) {
  const ctx = await browser.newContext({ viewport: { width: breite, height: hoehe },
    isMobile: breite < 500, hasTouch: breite < 500 });
  await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
  const page = await ctx.newPage();
  for (const [sname, pfad] of SEITEN) {
    try {
      await page.goto(BASIS + pfad, { waitUntil: "networkidle", timeout: 30000 });
    } catch { /* langsame Seite: trotzdem messen, was da ist */ }
    await page.waitForTimeout(1200);
    const m = await page.evaluate(messen, { grenze: breite < 500 ? 36 : 24, handy: breite < 500 });
    // Am Schreibtisch ist seitliches Scrollen ein Mittel (das Board ist so gebaut), am Handy
    // ein Mangel — deshalb wird es nur dort überhaupt vermerkt.
    if (breite >= 500) m.seitwaerts = [];
    bericht.seiten[`${sname} @ ${gname}`] = m;
    // Punkte: je Messgröße ein Punkt, wenn sie sauber ist.
    maxPunkte += 3;
    if (m.ueberstand <= 2) punkte++;
    maxPunkte++; if (m.abgeschnitten.length === 0) punkte++;
    if (breite < 500) {
      maxPunkte += 3;
      if (m.seitwaerts.length === 0) punkte++;
      if (m.tabellen.length === 0) punkte++;
      if (m.wasserfall.length === 0) punkte++;
    }
    if (m.tippziele_klein === 0) punkte++;
    if (m.kleinschrift === 0) punkte++;
  }
  await ctx.close();
}
await browser.close();

bericht.punkte = punkte;
bericht.maxPunkte = maxPunkte;
bericht.quote = Math.round((punkte / maxPunkte) * 100);

const pfad = "/w/befund-bedienbarkeit.json";
const alt = existsSync(pfad) ? JSON.parse(readFileSync(pfad, "utf8")) : null;
writeFileSync(pfad, JSON.stringify(bericht, null, 2) + "\n");

console.log(`\n${MARKE}: ${punkte}/${maxPunkte} sauber (${bericht.quote} %)`);
if (alt) console.log(`vorher (${alt.marke}): ${alt.punkte}/${alt.maxPunkte} (${alt.quote} %)`);
console.log("");
for (const [name, m] of Object.entries(bericht.seiten)) {
  const mangel = [];
  if (m.ueberstand > 2) mangel.push(`${m.ueberstand}px über den Rand (${m.ueberlaeufer.map((u) => u.name).join(", ")})`);
  if (m.tippziele_klein) mangel.push(`${m.tippziele_klein} zu kleine Tippziele`);
  if (m.kleinschrift) mangel.push(`${m.kleinschrift}× Schrift < 11px`);
  if (m.seitwaerts?.length) mangel.push(`seitwärts versteckt: ${m.seitwaerts.join(", ")}`);
  if (m.abgeschnitten?.length) mangel.push(`abgeschnitten: ${m.abgeschnitten.join(" | ")}`);
  if (m.tabellen?.length) mangel.push(`Tabelle am Handy: ${m.tabellen.join(", ")}`);
  if (m.wasserfall?.length) mangel.push(`Textwasserfall: ${m.wasserfall.join(", ")}`);
  console.log(mangel.length ? `FEHL ${name}: ${mangel.join(" · ")}` : `OK   ${name}`);
}
