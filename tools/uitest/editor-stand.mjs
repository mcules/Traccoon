// Zeigt der Editor, ob etwas ungespeichert ist und welche Fassung draußen gilt?
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();
const ok = (was, gut, detail = "") =>
  console.log(`${gut ? "OK  " : "FEHL"} ${was}${detail ? " — " + detail : ""}`);

const browser = await chromium.launch({
  executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome",
});
const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 } });
await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const page = await ctx.newPage();
const fehler = [];
page.on("pageerror", (e) => fehler.push(String(e).slice(0, 160)));

try {
  // Ablauf 44 ist veröffentlicht und unverändert.
  await page.goto(`${BASIS}/workflows/44`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2500);
  const frisch = await page.getByText(/^gespeichert$/).first().isVisible().catch(() => false);
  ok("Frisch geöffnet gilt als gespeichert", frisch);
  const live = await page.getByText(/veröffentlicht \(v\d+\)/).first().textContent().catch(() => "");
  ok("Veröffentlichte Fassung wird benannt", !!live, (live || "").trim());
  await page.screenshot({ path: "/w/23-editor-sauber.png" });

  // Eine Karte verschieben — Positionen werden mitgespeichert, also ist das eine Änderung.
  const knoten = page.locator(".react-flow__node").first();
  const box = await knoten.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 60, box.y + box.height / 2 + 40, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(900);

  const offen = await page.getByText(/● ungespeichert/).first().isVisible().catch(() => false);
  ok("Verschieben zählt als Änderung", offen);
  const jetztAlt = await page.getByText(/weicht von v\d+ ab/).first()
    .textContent().catch(() => "");
  ok("Der Unterschied zur laufenden Fassung wird benannt", !!jetztAlt,
     (jetztAlt || "").trim());
  await page.screenshot({ path: "/w/24-editor-geaendert.png" });

  // Zurückgehen fragt nach — und bleibt beim Abbrechen.
  page.once("dialog", (d) => d.dismiss());
  await page.getByRole("button", { name: /Zurück zu den Prozessen/i }).click();
  await page.waitForTimeout(800);
  ok("Zurück fragt bei ungespeicherter Arbeit nach", page.url().includes("/workflows/44"),
     page.url());

  ok("Keine JavaScript-Fehler", fehler.length === 0, fehler.slice(0, 1).join(""));
} finally {
  await browser.close();
}
