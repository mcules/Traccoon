// Does a server error arrive in the language of the person reading it?
//
// The API answers in English and names the key of its sentence; the browser looks the key up
// in its catalog. Nothing needs a login for this: a failed login is already such an error.
import { chromium } from "playwright-core";

const BASIS = process.env.BASIS || "http://frontend";
const ok = (was, gut, detail = "") =>
  console.log(`${gut ? "OK  " : "FEHL"} ${was}${detail ? " — " + detail : ""}`);

const browser = await chromium.launch({
  executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome",
});

/** Try to log in with nonsense and return what the interface says about it. */
async function meldung(locale) {
  const ctx = await browser.newContext({ locale });
  const page = await ctx.newPage();
  await page.goto(`${BASIS}/login`, { waitUntil: "networkidle" });
  await page.getByPlaceholder("E-Mail").first().fill("gibt.es.nicht@example.invalid");
  await page.locator('input[type="password"]').first().fill("falsch");
  await page.getByRole("button", { name: /Anmelden|Sign in/ }).first().click();
  await page.waitForTimeout(1200);
  const text = await page.locator("body").innerText();
  await ctx.close();
  return text;
}

try {
  const de = await meldung("de-DE");
  ok("Deutsch sieht den deutschen Satz", de.includes("Ungültige Anmeldedaten"));
  ok("und nicht den englischen daneben", !de.includes("Invalid credentials"));

  const en = await meldung("en-US");
  ok("Englisch sieht den englischen Satz", en.includes("Invalid credentials"));
  ok("und nicht den deutschen daneben", !en.includes("Ungültige Anmeldedaten"));
} finally {
  await browser.close();
}
