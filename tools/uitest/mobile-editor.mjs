// Can a flow be edited on a phone? Without dragging, without three columns side by side.
//
// The editor simply could not be operated there: blocks reached the canvas only by HTML5
// dragging (an event a touchscreen does not have), and palette, canvas and settings together
// needed 528 px of width. This probe walks the path a thumb actually takes, and saves
// nothing: whatever it changes is gone after a reload.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const WF = process.env.WF || "44";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();
const ok = (what, good, detail = "") =>
  console.log(`${good ? "OK  " : "FAIL"} ${what}${detail ? " — " + detail : ""}`);

const browser = await chromium.launch({
  executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome",
});
const ctx = await browser.newContext({
  viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true,
});
await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e).slice(0, 160)));

const nodes = () => page.locator(".react-flow__node").count();

try {
  await page.goto(`${BASIS}/workflows/${WF}`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2500);

  ok("the canvas fills the screen", (await nodes()) > 0, `${await nodes()} blocks`);
  ok("the toggle between canvas and block is there",
     await page.getByRole("button", { name: "Block", exact: true }).isVisible());

  // Tap a block: its settings have to open on their own.
  await page.locator(".react-flow__node").first().tap();
  await page.waitForTimeout(1000);
  const configOpen = await page.getByText("Blocks", { exact: true }).isVisible().catch(() => false);
  ok("tapping a block opens its settings", configOpen);
  await page.screenshot({ path: "/w/31-mobile-editor-block.png" });

  // Change the label, the header has to report unsaved work.
  const field = page.locator('input[type="text"], input:not([type])').first();
  await field.fill("A probe on the phone");
  await field.blur();
  await page.waitForTimeout(800);
  const dirty = await page.getByText(/unsaved/).isVisible().catch(() => false);
  ok("changes arrive", dirty);

  // Attach a new block by tapping, since dragging does not exist here.
  const before = await nodes();
  await page.getByRole("button", { name: "⏱ Wait", exact: true }).first().tap();
  await page.waitForTimeout(1200);
  await page.getByRole("button", { name: "Canvas", exact: true }).tap();
  await page.waitForTimeout(1200);
  const after = await nodes();
  ok("a block can be tapped instead of dragged", after === before + 1,
     `${before} → ${after} blocks`);
  await page.screenshot({ path: "/w/32-mobile-editor-canvas.png" });

  // Nothing stands past the edge.
  const over = await page.evaluate(
    () => document.scrollingElement.scrollWidth - window.innerWidth);
  ok("nothing stands past the edge sideways", over <= 2, `${over} px`);

  ok("no JavaScript errors", errors.length === 0, errors.slice(0, 1).join(""));
} finally {
  await browser.close();
}
