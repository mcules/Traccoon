// Does clicking in a daily note scroll it?
//
// Reported five times, guessed at five times, every guess wrong. This watches
// instead. The note is scrolled the way a reader scrolls it — the header embed
// alone is taller than the window, so the appointments are not even in the DOM
// until it is — and then the click happens and everything that could move is
// measured around it.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();
const NOTIZ = process.env.NOTIZ || "05 Daily Notes/2026/09/2026-09-09.md";

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
    const sel = document.querySelector(".cm-content")?.__cmView;
    return {
      top: Math.round(sc?.scrollTop ?? -1),
      hoehe: Math.round(box?.getBoundingClientRect().height ?? -1),
      embHoehe: emb ? Math.round(emb.getBoundingClientRect().height) : -1,
      embText: emb ? (emb.textContent || "").trim().slice(0, 18) : "(keine)",
      zeilen: document.querySelectorAll(".cm-content > .cm-line").length,
      caret: window.__caretLine ?? -1,
      spruenge: (window.__scroll ?? []).splice(0),
    };
  });

const zeigen = (name, s) =>
  console.log(
    `    ${String(name).padEnd(22)} top=${String(s.top).padStart(5)} ` +
      `inhalt=${String(s.hoehe).padStart(5)} embed=${String(s.embHoehe).padStart(5)} ` +
      `zeilen=${String(s.zeilen).padStart(3)} caret=${s.caret} embed-text=${JSON.stringify(s.embText)}`,
  );

try {
  await page.goto(`${BASIS}/note/${encodeURI(NOTIZ)}`, { waitUntil: "networkidle" });
  await page.waitForSelector(".cm-content");
  await page.waitForTimeout(4000);

  // Watch the scroller, and keep track of which line the caret is on.
  await page.evaluate(() => {
    const sc = document.querySelector(".cm-scroller");
    window.__scroll = [];
    let last = sc.scrollTop;
    sc.addEventListener("scroll", () => {
      const now = sc.scrollTop;
      if (Math.abs(now - last) < 2) return;
      window.__scroll.push(`${last} → ${now}`);
      last = now;
    });
    document.addEventListener("selectionchange", () => {
      const s = getSelection();
      const line = s?.anchorNode
        ? (s.anchorNode.nodeType === 1 ? s.anchorNode : s.anchorNode.parentElement)?.closest(".cm-line")
        : null;
      window.__caretLine = line
        ? [...document.querySelectorAll(".cm-content > .cm-line")].indexOf(line) + 1
        : -1;
    });
  });

  zeigen("nach dem Laden", await lage());

  // Scroll the way a reader does, until the appointments are in the document.
  const mittigDa = () =>
    page.evaluate(() => {
      const sc = document.querySelector(".cm-scroller").getBoundingClientRect();
      return [...document.querySelectorAll(".cm-content > .cm-line")].some((l) => {
        const r = l.getBoundingClientRect();
        return /·/.test(l.textContent || "") && r.top > sc.top + 60 && r.bottom < sc.bottom - 60;
      });
    });
  for (let i = 0; i < 40 && !(await mittigDa()); i++) {
    await page.evaluate(() => {
      const sc = document.querySelector(".cm-scroller");
      sc.scrollTop += 200;
      sc.dispatchEvent(new Event("scroll"));
    });
    await page.waitForTimeout(180);
  }
  await page.waitForTimeout(800);
  const vorher = await lage();
  zeigen("bis zu den Terminen", vorher);

  // A line that is genuinely in the middle of the window. Clicking one that is
  // half off the edge makes Playwright scroll it in first, and then the probe
  // measures its own doing instead of the app's.
  const treffer = await page.evaluate(() => {
    const sc = document.querySelector(".cm-scroller").getBoundingClientRect();
    const mitte = sc.top + sc.height / 2;
    const zeilen = [...document.querySelectorAll(".cm-content > .cm-line")];
    let best = null;
    for (const l of zeilen) {
      const r = l.getBoundingClientRect();
      if (r.top < sc.top + 60 || r.bottom > sc.bottom - 60) continue;
      const text = (l.textContent || "").trim();
      if (!/·/.test(text)) continue;                 // an appointment line
      const abstand = Math.abs((r.top + r.bottom) / 2 - mitte);
      if (!best || abstand < best.abstand)
        best = { abstand, x: Math.round(r.left + 60), y: Math.round(r.top + r.height / 2),
                 text: text.slice(0, 46) };
    }
    return best;
  });

  if (!treffer) {
    console.log("    keine Termin-Zeile mittig im Fenster");
  } else {
    console.log(`    klicke mittig auf ${JSON.stringify(treffer.text)} bei y=${treffer.y}`);
    await page.mouse.click(treffer.x, treffer.y);
    for (const ms of [150, 400, 1200, 2500]) {
      await page.waitForTimeout(ms === 150 ? 150 : ms - 150);
      zeigen(`+${ms}ms nach dem Klick`, await lage());
    }
    const nach = await lage();
    console.log(`\n    ERGEBNIS: scrollTop ${vorher.top} → ${nach.top} ` +
      `(${nach.top - vorher.top >= 0 ? "+" : ""}${nach.top - vorher.top}), ` +
      `Einbettung ${vorher.embHoehe} → ${nach.embHoehe}, Inhalt ${vorher.hoehe} → ${nach.hoehe}`);
    if (nach.spruenge.length) console.log(`    Sprünge: ${nach.spruenge.join(", ")}`);
  }
  await page.screenshot({ path: "/w/scroll-01.png" });
} finally {
  await browser.close();
}
