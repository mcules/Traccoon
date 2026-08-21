// Does the interface carry a second language? Switching in the profile, editing in the admin.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();
const ok = (what, good, detail = "") =>
  console.log(`${good ? "OK  " : "FAIL"} ${what}${detail ? " — " + detail : ""}`);

const browser = await chromium.launch({
  executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome",
});
const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e).slice(0, 160)));

try {
  await page.goto(`${BASIS}/account`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);
  ok("the language picker stands in the account",
     await page.getByText("Language", { exact: true }).first().isVisible().catch(() => false));

  // Switch to German. English is the source language, so this is the way that has to work.
  await page.locator("section", { hasText: "Language" }).first().locator("select")
    .selectOption("de");
  await page.getByRole("button", { name: "Save" }).first().click();
  await page.waitForTimeout(2000);
  const german = await page.getByText("Sprache", { exact: true }).first()
    .isVisible().catch(() => false);
  ok("the interface switches to German", german);
  await page.screenshot({ path: "/w/26-german.png" });

  // Check the navigation and a second page
  await page.goto(`${BASIS}/processes/metrics`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);
  const series = await page.getByText("Reihen", { exact: false }).first()
    .isVisible().catch(() => false);
  ok("other pages are translated as well", series);
  await page.screenshot({ path: "/w/27-german-series.png" });

  // The admin translation view
  await page.goto(`${BASIS}/admin/translations`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);
  const rows = await page.locator('input[placeholder]').count();
  ok("the admin view lists the keys", rows > 0, `${rows} rows visible`);
  const counter = await page.getByText(/\d+ (of|von) \d+ (open|offen)/).first()
    .textContent().catch(() => "");
  ok("it shows how much is still missing", !!counter, (counter || "").trim());
  await page.screenshot({ path: "/w/28-admin.png" });

  // Back to English so the account is left as it was found
  await page.goto(`${BASIS}/account`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1200);
  await page.locator("section", { hasText: "Sprache" }).first().locator("select")
    .selectOption("en");
  await page.getByRole("button", { name: "Speichern" }).first().click();
  await page.waitForTimeout(1500);
  ok("back to English",
     await page.getByText("Language", { exact: true }).first().isVisible().catch(() => false));

  ok("no JavaScript errors", errors.length === 0, errors.slice(0, 1).join(""));
} finally {
  await browser.close();
}
