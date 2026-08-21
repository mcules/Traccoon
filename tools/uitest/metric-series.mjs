// Probe of the measurement series view: overview, detail with a period, the forecast line,
// the value table, and dropping a single outlier.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();
const ok = (what, good, detail = "") =>
  console.log(`${good ? "OK  " : "FAIL"} ${what}${detail ? " — " + detail : ""}`);

const browser = await chromium.launch({
  executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome",
});
const ctx = await browser.newContext({ viewport: { width: 1400, height: 1100 } });
await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e).slice(0, 160)));

try {
  await page.goto(`${BASIS}/processes/metrics`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);
  ok("the series stand in the overview",
     await page.getByText("akku.shelter").first().isVisible().catch(() => false));

  // Do not pin this to one test series: the test data is cleaned up now and then, and the
  // first series that exists does the job just as well.
  await page.locator('button:has-text("▸")').first().click();
  await page.waitForTimeout(1500);
  ok("a period can be chosen",
     await page.getByRole("button", { name: "30 days" }).isVisible().catch(() => false));
  const enoughRows = (await page.locator("table tbody tr").count()) >= 3;
  const dashed = await page.locator("svg line[stroke-dasharray='6 5']").count();
  if (enoughRows) ok("the forecast is drawn as a dashed continuation", dashed > 0);
  else console.log("--   forecast — skipped, the series holds too few points");
  const explanation = await page.getByText(/continuation of these points/i).first()
    .textContent().catch(() => "");
  if (enoughRows) ok("the line is explained in words", !!explanation,
     (explanation || "").replace(/\s+/g, " ").trim().slice(0, 110));
  const rows = await page.locator("table tbody tr").count();
  ok("the table of values stands below it", rows >= 1, `${rows} rows`);
  await page.screenshot({ path: "/w/21-detail.png" });

  // Drop an outlier, the forecast has to change visibly. That only works with a series that
  // holds several values: test series get cleaned up now and then, and a real series with a
  // single value is not a failure.
  if (rows >= 3) {
    const before = await page.getByText(/quality/).first().textContent().catch(() => "");
    page.once("dialog", (d) => d.accept());
    await page.locator("table tbody tr").first().locator("button").click();
    await page.waitForTimeout(2000);
    const after = await page.getByText(/quality/).first().textContent().catch(() => "");
    ok("a single value can be removed", before !== after,
       `${(before || "").trim()} → ${(after || "").trim()}`);
  } else {
    console.log(`--   a single value can be removed — skipped, only ${rows} values in the series`);
  }

  if (enoughRows) {
    await page.getByRole("button", { name: "7 days" }).click();
    await page.waitForTimeout(1500);
    const few = await page.locator("table tbody tr").count();
    ok("a shorter period shows fewer values", few < rows, `${few} rows`);
  } else {
    console.log("--   shorter period — skipped, too few values");
  }
  await page.screenshot({ path: "/w/22-after-deleting.png" });

  ok("no JavaScript errors", errors.length === 0, errors.slice(0, 1).join(""));
} finally {
  await browser.close();
}
