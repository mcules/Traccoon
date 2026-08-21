// Can a step be switched off, and does the graph show it?
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
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
page.on("pageerror", (e) => errors.push(String(e).slice(0, 160)));

try {
  await page.goto(`${BASIS}/workflows/44`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2500);

  // Click an action node
  await page.getByText("Record the battery level").first().click();
  await page.waitForTimeout(800);
  const toggle = page.getByText(/Switch this step off/i).first();
  ok("the switch stands in the configuration",
     await toggle.isVisible().catch(() => false));

  await toggle.click();
  await page.waitForTimeout(600);
  const choice = await page.getByText(/What should happen then\?/i).first()
    .isVisible().catch(() => false);
  ok("one chooses between skipping and aborting", choice);
  const hint = await page.getByText(/continues through the normal outlet/i).first()
    .isVisible().catch(() => false);
  ok("the chosen case is explained", hint);

  const mark = await page.locator(".react-flow__node", { hasText: "Record the battery level" })
    .getByText("off").count();
  ok("the node shows in the graph that it is off", mark > 0);
  await page.screenshot({ path: "/w/25-killswitch.png" });

  // Switch to stop mode, the hint has to change
  await page.locator("select").filter({ hasText: "skip and continue" }).first()
    .selectOption("abort").catch(() => {});
  await page.waitForTimeout(500);
  const hint2 = await page.getByText(/end here as cancelled/i).first()
    .isVisible().catch(() => false);
  ok("the abort case is explained separately", hint2);

  ok("no JavaScript errors", errors.length === 0, errors.slice(0, 1).join(""));
} finally {
  await browser.close();
}
