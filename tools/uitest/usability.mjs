// Measures what can be measured about operating the interface: overflow, touch targets,
// font sizes, hidden content.
//
// Taste cannot be measured, this can, and it is exactly where a phone falls apart: a table
// standing 200 px past the edge, a button 22 px high, a hint in 10 px type. The numbers of a
// run land in findings-usability.json, and a second run shows whether a change actually
// helped instead of merely looking different.
//
// Thresholds: 36 px is the smallest area a thumb hits reliably (Apple says 44, Material 48,
// 36 is the floor below which it measurably starts to fumble). 11 px is where running text on
// a phone stops being readable without zooming.
import { chromium } from "playwright-core";
import { readFileSync, writeFileSync, existsSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();
const MARKE = process.env.MARKE || "lauf";
const PROJEKT = process.env.PROJEKT || "UNI";

// Every tab on its own: the first version checked only the default tab of each page and
// therefore called the administration clean, although six of its eight tabs show tables that
// get squeezed to nothing at 390 px.
//
// The addresses are the ones of today, and that has to stay that way: a section that no
// longer exists does NOT fail here. It falls through the redirect onto the default tab of
// its page, gets measured a second time and reports itself clean. Five of the old entries
// (`eigene`, `betrieb`, `ausloeser`, `messreihen`, `prefs`) had been doing exactly that
// since the addresses went English — the measurement looked complete and covered four
// sections less than it said.
const SEITEN = [
  ["Projects", "/"],
  ["Inbox", "/inbox"],
  ["Mail", "/mail"],
  ...["person", "appearance", "notifications", "mail", "agents"]
    .map((t) => [`Account/${t}`, `/account/${t}`]),
  // Area and view stand in the path since the sub-menu was regrouped; `?tab=` only redirects.
  ...["board", "list", "backlog", "archive"]
    .map((v) => [`Project/work/${v}`, `/projects/${PROJEKT}/work/${v}`]),
  // Without `office`: the room is a canvas of its own, the same reason the flow editor is
  // measured apart from its drawing area.
  ...["monitor", "testenvs", "hardware"]
    .map((v) => [`Project/operations/${v}`, `/projects/${PROJEKT}/operations/${v}`]),
  ...["pm", "code", "dashboard", "settings"]
    .map((t) => [`Project/${t}`, `/projects/${PROJEKT}/${t}`]),
  ...["own", "default", "operations", "triggers", "metrics", "documents", "locations"]
    .map((t) => [`Processes/${t}`, `/processes/${t}`]),
  ...["secrets", "destinations", "agents", "mcp", "jobs", "webhooks", "skills", "plugins"]
    .map((t) => [`Settings/${t}`, `/settings/${t}`]),
  ...["users", "cost", "models", "maintenance", "mail", "artifacts", "translations"]
    .map((t) => [`Admin/${t}`, `/admin/${t}`]),
  // The flow editor could not be operated on a phone at all and therefore never appeared in
  // the measurement, which is exactly the gap it slipped through.
  ["Flow editor", `/workflows/${process.env.WF || "44"}`],
];
const BREITEN = [["Handy", 390, 844], ["Desktop", 1400, 900]];

const measure = ({ grenze, handy }) => {
  // The canvas of a flow has its own coordinate system: React Flow scales it as a whole, so
  // 12 px type sits in the document as 8.4 px at 0.7 zoom. Measuring inside it would judge
  // the zoom factor, not the design.
  const gezeichnet = (el) => !!el.closest(".react-flow");
  const doc = document.scrollingElement;
  const breite = window.innerWidth;
  const overflow = Math.max(0, doc.scrollWidth - breite);

  // Who stands past the edge? Only the outermost offender counts, otherwise every child
  // reports the fault of its parent as well.
  const raus = [];
  for (const el of document.querySelectorAll("body *")) {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (gezeichnet(el)) continue;
    if (r.right > breite + 2 || r.left < -2) {
      const stil = getComputedStyle(el);
      if (stil.overflowX === "auto" || stil.overflowX === "scroll") continue;  // darf scrollen
      if (raus.some((v) => v.el.contains(el))) continue;
      raus.push({ el, name: el.tagName.toLowerCase() + (el.className && typeof el.className === "string"
        ? "." + el.className.trim().split(/\s+/).slice(0, 2).join(".") : ""),
        ueber: Math.round(r.right - breite) });
    }
  }

  // Touch targets: visible controls below the threshold (thumb 36 px, mouse 24 px).
  const small = [];
  for (const el of document.querySelectorAll(
    "button, a[href], select, input:not([type=hidden]), [role=button], summary")) {
    const r = el.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) continue;
    if (r.top > window.innerHeight * 3) continue;          // far down: does not count
    const stil = getComputedStyle(el);
    if (stil.visibility === "hidden") continue;
    // A link inside running text is text, not a button, and should not grow to 36 px.
    if (el.tagName === "A" && stil.display === "inline") continue;
    // A checkbox keeps its native size (otherwise the browser draws a white block). What gets
    // tapped is the label next to it, and that is the area which counts.
    if (el.tagName === "INPUT" && (el.type === "checkbox" || el.type === "radio")) {
      const lab = el.closest("label");
      const lr = lab && lab.getBoundingClientRect();
      if (!lr || (lr.height >= grenze && lr.width >= 24)) continue;
    }
    if (r.height < grenze || r.width < 24) {
      small.push({ name: (el.textContent || el.getAttribute("aria-label") || el.tagName).trim().slice(0, 30),
        h: Math.round(r.height), w: Math.round(r.width) });
    }
  }

  // Type too small in visible text.
  const kleinschrift = new Map();
  for (const el of document.querySelectorAll("body *")) {
    if (!el.childNodes.length) continue;
    const eigenerText = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim().length > 2);
    if (!eigenerText) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    if (gezeichnet(el)) continue;
    const px = parseFloat(getComputedStyle(el).fontSize);
    if (px < 11) kleinschrift.set(el.textContent.trim().slice(0, 40), Math.round(px * 10) / 10);
  }

  // Clipped instead of scrolled: an element whose content is wider than itself, with no way
  // to scroll. That is the worse case, because it looks as if everything were there.
  const clipped = [];
  for (const el of document.querySelectorAll("body *")) {
    const s = getComputedStyle(el);
    if (s.overflowX === "auto" || s.overflowX === "scroll") continue;
    // `truncate` cuts on purpose and shows an ellipsis, which is a solution, not a defect.
    if (s.textOverflow === "ellipsis") continue;
    if (el.scrollWidth <= el.clientWidth + 24) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 40 || r.height < 12) continue;
    if (gezeichnet(el)) continue;
    clipped.push({ el, name: el.tagName.toLowerCase() + " +"
      + (el.scrollWidth - el.clientWidth) + "px: "
      + (el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 30) });
  }

  // A table with more than two columns is not a table on a phone: either it stands past the
  // edge or its columns squeeze down to one word per line.
  const tabellen = handy ? [...document.querySelectorAll("table")].filter((tab) => {
    const spalten = tab.querySelector("tr")?.children.length || 0;
    return spalten > 2 && tab.getBoundingClientRect().width > 200;
  }).map((tab) => `${tab.querySelector("tr")?.children.length} Spalten`) : [];

  // Text columns narrower than about eighteen characters: every sentence there breaks into a
  // waterfall (the model page showed 45 lines of two words each). The threshold sits below the
  // narrowest deliberate column in the application (the editor's 208 px palette), otherwise
  // the measurement would report a design decision as a defect.
  const wasserfall = [];
  for (const el of document.querySelectorAll("p, div, li, span")) {
    const txt = (el.textContent || "").trim();
    if (txt.length < 120) continue;
    const eigener = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim().length > 40);
    if (!eigener) continue;
    const r = el.getBoundingClientRect();
    if (gezeichnet(el)) continue;
    if (r.width > 0 && r.width < 180 && r.height > 100) wasserfall.push(`${Math.round(r.width)}px breit`);
  }

  // Nobody finds what has to be scrolled sideways on a phone. Tables are the usual suspects:
  // five columns do not fit into 390 px.
  const sideways = [...document.querySelectorAll("body *")].filter((el) => {
    const s = getComputedStyle(el);
    if (s.overflowX !== "auto" && s.overflowX !== "scroll") return false;
    return el.scrollWidth > el.clientWidth + 8 && el.getBoundingClientRect().height > 40;
  }).map((el) => (el.querySelector("table") ? "Tabelle: " : "") +
       el.tagName.toLowerCase() + " +" + (el.scrollWidth - el.clientWidth) + "px");

  return {
    // Only the innermost offender counts: a field that is too wide makes every ancestor too
    // wide as well, and the report "some div is too wide" helps nobody.
    clipped: clipped
      .filter((v, _i, all) => !all.some((w) => w !== v && v.el.contains(w.el)))
      .slice(0, 5).map(({ name }) => name),
    tabellen,
    wasserfall: wasserfall.slice(0, 3),
    sideways: sideways.slice(0, 5),
    overflow,
    overflowing: raus.slice(0, 6).map(({ name, ueber }) => ({ name, ueber })),
    tippziele_klein: small.length,
    tippziele_beispiele: small.slice(0, 5),
    kleinschrift: kleinschrift.size,
    kleinschrift_beispiele: [...kleinschrift.entries()].slice(0, 4),
    // How many destinations are reachable without opening a menu first?
    sichtbare_navigation: [...document.querySelectorAll("header a[href], header button")]
      .filter((el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; }).length,
    hoehe: Math.round(document.scrollingElement.scrollHeight),
  };
};

const browser = await chromium.launch({
  executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome",
});
const bericht = { marke: MARKE, seiten: {} };
let points = 0, maxPoints = 0;

for (const [gname, breite, hoehe] of BREITEN) {
  const ctx = await browser.newContext({ viewport: { width: breite, height: hoehe },
    isMobile: breite < 500, hasTouch: breite < 500 });
  await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
  const page = await ctx.newPage();
  for (const [sname, pfad] of SEITEN) {
    try {
      await page.goto(BASIS + pfad, { waitUntil: "networkidle", timeout: 30000 });
    } catch { /* a slow page: measure what is there anyway */ }
    await page.waitForTimeout(1200);
    const m = await page.evaluate(measure, { grenze: breite < 500 ? 36 : 24, handy: breite < 500 });
    // At a desk sideways scrolling is a device (the board is built that way), on a phone it is
    // a defect, so it is only recorded there.
    if (breite >= 500) m.sideways = [];
    bericht.seiten[`${sname} @ ${gname}`] = m;
    // Score: one point per measure when it comes out clean.
    maxPoints += 3;
    if (m.overflow <= 2) points++;
    maxPoints++; if (m.clipped.length === 0) points++;
    if (breite < 500) {
      maxPoints += 3;
      if (m.sideways.length === 0) points++;
      if (m.tabellen.length === 0) points++;
      if (m.wasserfall.length === 0) points++;
    }
    if (m.tippziele_klein === 0) points++;
    if (m.kleinschrift === 0) points++;
  }
  await ctx.close();
}
await browser.close();

bericht.points = points;
bericht.maxPoints = maxPoints;
bericht.quote = Math.round((points / maxPoints) * 100);

const pfad = "/w/findings-usability.json";
const alt = existsSync(pfad) ? JSON.parse(readFileSync(pfad, "utf8")) : null;
writeFileSync(pfad, JSON.stringify(bericht, null, 2) + "\n");

console.log(`\n${MARKE}: ${points}/${maxPoints} sauber (${bericht.quote} %)`);
if (alt) console.log(`vorher (${alt.marke}): ${alt.points}/${alt.maxPoints} (${alt.quote} %)`);
console.log("");
for (const [name, m] of Object.entries(bericht.seiten)) {
  const flaws = [];
  if (m.overflow > 2) flaws.push(`${m.overflow}px past the edge (${m.overflowing.map((u) => u.name).join(", ")})`);
  if (m.tippziele_klein) flaws.push(`${m.tippziele_klein} zu kleine Tippziele`);
  if (m.kleinschrift) flaws.push(`${m.kleinschrift}× Schrift < 11px`);
  if (m.sideways?.length) flaws.push(`hidden sideways: ${m.sideways.join(", ")}`);
  if (m.clipped?.length) flaws.push(`clipped: ${m.clipped.join(" | ")}`);
  if (m.tabellen?.length) flaws.push(`Tabelle am Handy: ${m.tabellen.join(", ")}`);
  if (m.wasserfall?.length) flaws.push(`Textwasserfall: ${m.wasserfall.join(", ")}`);
  console.log(flaws.length ? `FEHL ${name}: ${flaws.join(" · ")}` : `OK   ${name}`);
}
