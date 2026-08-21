// Does the editor show whether something is unsaved and which version is live?
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
  // Flow 44 is published and unchanged.
  await page.goto(`${BASIS}/workflows/44`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2500);
  const fresh = await page.getByText(/^saved$/).first().isVisible().catch(() => false);
  ok("freshly opened counts as saved", fresh);
  const live = await page.getByText(/published \(v\d+\)/).first().textContent().catch(() => "");
  ok("the published version is named", !!live, (live || "").trim());
  await page.screenshot({ path: "/w/23-editor-clean.png" });

  // Move a card: positions are saved along, so that counts as a change.
  const node = page.locator(".react-flow__node").first();
  const box = await node.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 60, box.y + box.height / 2 + 40, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(900);

  const open = await page.getByText(/● unsaved/).first().isVisible().catch(() => false);
  ok("moving counts as a change", open);
  const nowOld = await page.getByText(/deviates from v\d+/).first()
    .textContent().catch(() => "");
  ok("the difference to the running version is named", !!nowOld,
     (nowOld || "").trim());
  await page.screenshot({ path: "/w/24-editor-changed.png" });

  // Going back asks first, and stays put when you cancel.
  page.once("dialog", (d) => d.dismiss());
  await page.getByRole("button", { name: /Back to the flows/i }).click();
  await page.waitForTimeout(800);
  ok("going back asks about unsaved work", page.url().includes("/workflows/44"),
     page.url());

  ok("no JavaScript errors", errors.length === 0, errors.slice(0, 1).join(""));
} finally {
  await browser.close();
}
