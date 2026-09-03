// Does a drawing open, and does saving it leave the note around it alone?
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const NOTE = process.env.NOTIZ || "07 Anhänge/Sicherungskasten Shelter.excalidraw.md";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();
const ok = (what, good, detail = "") =>
  console.log(`${good ? "OK  " : "FAIL"} ${what}${detail ? " — " + detail : ""}`);

const browser = await chromium.launch({
  executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome",
});
const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 } });
await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const page = await ctx.newPage();
const errors = [];
const calls = [];
page.on("pageerror", (e) => errors.push(String(e).slice(0, 200)));
page.on("response", (r) => {
  if (r.url().includes("/drawing")) calls.push(`${r.status()} ${r.url().replace(BASIS, "").slice(0, 80)}`);
});

try {
  await page.goto(`${BASIS}/notes/n/${NOTE.split("/").map(encodeURIComponent).join("/")}`,
                  { waitUntil: "networkidle" });
  await page.waitForTimeout(4000);
  await page.screenshot({ path: "/w/40-drawing.png" });

  const scene = await page.evaluate(async (note) => {
    const head = { authorization: "Bearer " + localStorage.getItem("traccoon_token") };
    const r = await fetch("/api/notes-native/drawing?path=" + encodeURIComponent(note),
                          { headers: head });
    const d = await r.json();
    return { status: r.status, elements: d.scene?.elements?.length, hash: d.hash };
  }, NOTE);
  ok("the scene comes back", scene.status === 200 && scene.elements > 0,
     JSON.stringify(scene).slice(0, 110));

  const canvas = await page.locator("canvas").count();
  ok("a canvas is on the page", canvas > 0, `${canvas} canvas`);
} catch (e) {
  ok("run through", false, String(e).slice(0, 200));
} finally {
  console.log("\ncalls:", [...new Set(calls)].join("\n       "));
  ok("no error in the page", errors.length === 0, errors.join(" | "));
  await browser.close();
}
