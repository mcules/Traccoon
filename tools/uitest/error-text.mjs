// Does a server error arrive in the language of the person reading it?
//
// The API answers in English and names the key of its sentence; the browser looks the key up
// in its catalog. Nothing needs a login for this: a failed login is already such an error.
import { chromium } from "playwright-core";

const BASIS = process.env.BASIS || "http://frontend";
const ok = (what, good, detail = "") =>
  console.log(`${good ? "OK  " : "FAIL"} ${what}${detail ? " — " + detail : ""}`);

const browser = await chromium.launch({
  executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome",
});

/** Try to log in with nonsense and return what the interface says about it. */
async function message(locale) {
  const ctx = await browser.newContext({ locale });
  const page = await ctx.newPage();
  await page.goto(`${BASIS}/login`, { waitUntil: "networkidle" });
  await page.getByPlaceholder(/E-Mail|Email/).first().fill("does.not.exist@example.invalid");
  await page.locator('input[type="password"]').first().fill("wrong");
  await page.getByRole("button", { name: /Sign in|Anmelden/ }).first().click();
  await page.waitForTimeout(1200);
  const text = await page.locator("body").innerText();
  await ctx.close();
  return text;
}

try {
  const de = await message("de-DE");
  ok("a German browser sees the German sentence", de.includes("Ungültige Anmeldedaten"));
  ok("and not the English one beside it", !de.includes("Invalid credentials"));

  const en = await message("en-US");
  ok("an English browser sees the English sentence", en.includes("Invalid credentials"));
  ok("and not the German one beside it", !en.includes("Ungültige Anmeldedaten"));
} finally {
  await browser.close();
}
