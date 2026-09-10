// The reported sequence, click by click: open a daily note, click in the task
// list inside the header, then click an appointment. Only ever clicks what is
// genuinely in the middle of the window — clicking a line at the edge makes the
// test scroll it in itself and then measure its own doing.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();
const A = process.env.EINS || "05 Daily Notes/2026/09/2026-09-09.md";
const B = process.env.ZWEI || "05 Daily Notes/2026/09/2026-09-08.md";

const browser = await chromium.launch({
  executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome",
});
const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 } });
await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const page = await ctx.newPage();
page.on("pageerror", (e) => console.log("  SEITENFEHLER:", String(e).slice(0, 200)));

const lage = () =>
  page.evaluate(() => {
    const sc = document.querySelector(".cm-scroller");
    const box = document.querySelector(".cm-content");
    const emb = document.querySelector(".cm-note-embed");
    return {
      top: Math.round(sc?.scrollTop ?? -1),
      hoehe: Math.round(box?.getBoundingClientRect().height ?? -1),
      emb: emb ? Math.round(emb.getBoundingClientRect().height) : -1,
    };
  });

/** Click whatever is in the middle of the window and matches, without scrolling.
 *  Searches leaf elements, not lines: the header is a single line 1431px tall,
 *  and the task list lives inside it. */
const finden = (muster) =>
  page.evaluate((m) => {
    const re = new RegExp(m);
    const sc = document.querySelector(".cm-scroller").getBoundingClientRect();
    const mitte = sc.top + sc.height / 2;
    let best = null;
    const kandidaten = [
      ...document.querySelectorAll(".cm-content > .cm-line"),   // ordinary lines
      ...[...document.querySelectorAll(".cm-content *")].filter((e) => !e.children.length),
    ];
    for (const el of kandidaten) {
      const text = (el.textContent || "").trim();
      if (!text || !re.test(text)) continue;
      const r = el.getBoundingClientRect();
      if (r.height === 0 || r.height > 80) continue;
      if (r.top < sc.top + 40 || r.bottom > sc.bottom - 40) continue;
      const d = Math.abs((r.top + r.bottom) / 2 - mitte);
      if (!best || d < best.d)
        best = { d, x: Math.round(r.left + Math.min(40, r.width / 2)),
                 y: Math.round(r.top + r.height / 2), text: text.slice(0, 40) };
    }
    return best;
  }, muster);

const klickMittig = async (was, muster) => {
  const ziel = await finden(muster);
  if (!ziel) { console.log(`    ${was}: nichts Passendes mittig im Fenster`); return; }
  const v = await lage();
  // What the reader is actually looking at: does the clicked text stay put?
  const vorY = ziel.y;
  const teile = () => page.evaluate(() => {
    const emb = document.querySelector(".cm-note-embed");
    if (!emb) return ["(keine Einbettung)"];
    const out = [];
    const lauf = (el, tiefe) => {
      for (const k of el.children) {
        const r = k.getBoundingClientRect();
        if (r.height >= 20)
          out.push(`${"  ".repeat(tiefe)}${k.tagName.toLowerCase()}.${(k.className || "").toString().split(" ")[0]} ${Math.round(r.height)}px "${(k.textContent||"").trim().slice(0,26)}"`);
        if (tiefe < 2) lauf(k, tiefe + 1);
      }
    };
    lauf(emb, 0);
    return out;
  });
  const vorTeile = await teile();
  await page.mouse.click(ziel.x, ziel.y);
  await page.waitForTimeout(1400);
  // The SAME element, found by its exact text — comparing a leaf's position
  // before with its container's position after measures the probe, not the app.
  const nachY = await page.evaluate((txt) => {
    for (const el of document.querySelectorAll(".cm-content *")) {
      if (el.children.length) continue;
      if ((el.textContent || "").trim().includes(txt.slice(0, 22)))
        return Math.round(el.getBoundingClientRect().top + el.getBoundingClientRect().height / 2);
    }
    return -1;
  }, ziel.text);
  const n = await lage();
  const nachTeile = await teile();
  if (JSON.stringify(vorTeile) !== JSON.stringify(nachTeile)) {
    console.log("      Aufbau der Einbettung VORHER:");
    for (const z of vorTeile) console.log("        " + z);
    console.log("      NACHHER:");
    for (const z of nachTeile) console.log("        " + z);
  }
  console.log(`      geklickte Zeile: y ${vorY} → ${nachY}`
    + (nachY >= 0 && Math.abs(nachY - vorY) > 12 ? `   <== VERSCHIEBT SICH um ${nachY - vorY}px` : ""));
  const d = n.top - v.top;
  console.log(`    ${was.padEnd(30)} ${JSON.stringify(ziel.text)}`);
  console.log(`      scrollTop ${v.top} → ${n.top} (${d >= 0 ? "+" : ""}${d})`
    + `   Inhalt ${v.hoehe} → ${n.hoehe}   Einbettung ${v.emb} → ${n.emb}`
    + (Math.abs(d) > 8 ? "   <== SPRINGT" : ""));
};

const bisMittig = async (muster) => {
  for (let i = 0; i < 40 && !(await finden(muster)); i++) {
    await page.evaluate(() => {
      const sc = document.querySelector(".cm-scroller");
      sc.scrollTop += 200;
      sc.dispatchEvent(new Event("scroll"));
    });
    await page.waitForTimeout(160);
  }
  await page.waitForTimeout(500);
};

const oeffnen = async (pfad) => {
  await page.goto(`${BASIS}/note/${encodeURI(pfad)}`, { waitUntil: "networkidle" });
  await page.waitForSelector(".cm-content");
  await page.waitForTimeout(3500);
  console.log(`\n=== ${pfad}`);
  const s = await lage();
  console.log(`    geladen: top=${s.top} Inhalt=${s.hoehe} Einbettung=${s.emb}`);
};

try {
  await oeffnen(A);
  await bisMittig("Wichtig");
  await klickMittig("1) erster Klick, Aufgaben", "Wichtig");
  await bisMittig("Vostura");
  await klickMittig("2) danach in die Termine", "Vostura");
  await klickMittig("3) nochmal Termine", "Vostura");

  await oeffnen(B);
  await bisMittig("Wichtig");
  await klickMittig("4) andere Notiz, Aufgaben", "Wichtig");
  await bisMittig("Vostura");
  await klickMittig("5) andere Notiz, Termine", "Vostura");
} finally {
  await browser.close();
}
