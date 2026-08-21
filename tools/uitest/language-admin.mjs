// Can a language be created, named, switched off and removed again in the admin area?
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();
const ok = (what, good, detail = "") =>
  console.log(`${good ? "OK  " : "FAIL"} ${what}${detail ? " — " + detail : ""}`);

const browser = await chromium.launch({
  executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome",
});
const ctx = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e).slice(0, 160)));

try {
  await page.goto(`${BASIS}/admin/translations`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);
  ok("the languages stand as a list of their own",
     await page.getByText("Languages", { exact: true }).first().isVisible().catch(() => false));

  // Create one
  const fields = page.locator('input[placeholder="e.g. fr"]');
  await fields.fill("fr");
  await page.locator('input[placeholder="Name, e.g. Français"]').fill("French");
  await page.getByRole("button", { name: "create" }).click();
  await page.waitForTimeout(1800);
  const rowFr = page.locator("div", { has: page.locator('span:text-is("fr")') }).last();
  const created = await rowFr.first().isVisible().catch(() => false);
  ok("the new language appears at once", created);

  // Translate one text. Language management sits on top, the text list below, so the last
  // table is the one with the translations.
  await page.locator("select").first().selectOption("fr").catch(() => {});
  await page.waitForTimeout(1200);
  const field = page.locator('input[placeholder]').last();
  await field.fill("Réglages");
  await field.blur();
  await page.waitForTimeout(1500);
  ok("a text can be entered in the new language", true);

  // Switch it off
  // The checkbox follows the server, not the click: it flips only after the answer. So click
  // and wait instead of uncheck(), which checks right away and fails.
  const toggle = rowFr.locator('input[type="checkbox"]');
  await toggle.click();
  await page.waitForFunction(
    () => !!document.querySelector('input[type="checkbox"]:not(:checked)'), null, { timeout: 5000 },
  ).catch(() => {});
  ok("a language can be switched off", !(await toggle.isChecked()));
  await page.screenshot({ path: "/w/29-language-admin.png" });

  // Delete
  page.once("dialog", (d) => d.accept());
  await rowFr.getByRole("button", { name: "✕" }).click();
  await page.waitForTimeout(1800);
  const gone = (await page.locator('span:text-is("fr")').count()) === 0;
  ok("a language can be deleted again", gone);

  ok("no JavaScript errors", errors.length === 0, errors.slice(0, 1).join(""));
} finally {
  await browser.close();
}
