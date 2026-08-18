// Zeigt zu einer Seite die innersten Elemente, deren Inhalt breiter ist als sie selbst.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();
const b = await chromium.launch({ executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome" });
const c = await b.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
await c.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const p = await c.newPage();
for (const pfad of process.argv.slice(2)) {
  await p.goto("http://frontend" + pfad, { waitUntil: "networkidle" }).catch(() => {});
  await p.waitForTimeout(1500);
  const treffer = await p.evaluate(() => {
    const raus = [];
    for (const el of document.querySelectorAll("body *")) {
      const s = getComputedStyle(el);
      if (s.overflowX === "auto" || s.overflowX === "scroll" || s.textOverflow === "ellipsis") continue;
      if (el.scrollWidth <= el.clientWidth + 24) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 40 || r.height < 12) continue;
      raus.push(el);
    }
    return raus.filter((el) => !raus.some((w) => w !== el && el.contains(w)))
      .slice(0, 4).map((el) => el.outerHTML.slice(0, 300));
  });
  console.log("==", pfad);
  treffer.forEach((t) => console.log("  ", t.replace(/\s+/g, " ")));
}
await b.close();
