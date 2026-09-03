// The calendars as a place of their own: a login per server, and under each
// login the calendars on it. Listed, editable, added through a dialog.
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
page.on("pageerror", (e) => errors.push(String(e).slice(0, 200)));
const shut = async () => {
  await page.locator("[role=dialog] button").filter({ hasText: /abbrechen|cancel/i })
    .first().click();
  await page.waitForTimeout(500);
};

try {
  await page.goto(`${BASIS}/account/calendar`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2500);

  const tabs = await page.locator("nav a, aside a, [role=tab]").allInnerTexts();
  ok("the calendar has a place of its own",
     tabs.some((t) => /^Kalender$/m.test(t.trim())), tabs.join(" | ").slice(0, 120));

  const body = await page.locator("main, body").first().innerText();
  ok("the login is named above its calendars", /CalDAV/.test(body),
     body.split("\n").filter((l) => l.trim()).slice(0, 4).join(" | ").slice(0, 120));
  const rows = await page.locator("[class*='px-3']").filter({ hasText: "://" }).count();
  ok("the calendars are listed", rows > 0, `${rows} rows`);
  await page.screenshot({ path: "/w/70-calendar.png", fullPage: true });

  // Editing a login: the pencil on the login row, not on a calendar.
  const pens = page.locator('button[title="bearbeiten"], button[title="Edit"]');
  await pens.first().click();
  await page.waitForTimeout(900);
  let filled = await page.evaluate(() =>
    [...document.querySelectorAll("[role=dialog] input")].map((i) => i.value).filter(Boolean));
  ok("a login can be edited and comes up filled in", filled.length >= 2,
     filled.join(" | ").slice(0, 110));
  const blank = await page.evaluate(() =>
    document.querySelector("[role=dialog] input[type=password]")?.value);
  ok("the stored password is not handed back out", blank === "");
  await page.screenshot({ path: "/w/71-server-edit.png" });
  await shut();

  // Editing a calendar: the login it sits on is shown, not typed.
  await pens.nth(1).click();
  await page.waitForTimeout(900);
  const picked = await page.evaluate(() =>
    document.querySelector("[role=dialog] select")?.selectedOptions[0]?.text);
  ok("a calendar says which login it sits on", !!picked, picked);
  await page.screenshot({ path: "/w/72-calendar-edit.png" });
  await shut();

  // Adding a calendar under a login: a button, and only then a dialog, and in
  // it what the server itself says it has.
  const beforeFields = await page.locator("input[type=text], input:not([type])").count();
  await page.locator("button").filter({ hasText: /Kalender hier hinzufügen/i }).first().click();
  await page.waitForTimeout(3500);
  ok("the button opens a dialog", await page.locator("[role=dialog]").count() > 0);
  const empty = await page.evaluate(() =>
    [...document.querySelectorAll("[role=dialog] input[type=text], [role=dialog] input:not([type])")]
      .every((i) => i.value === ""));
  ok("the dialog starts empty", empty);
  ok("no form stood open beside the list", beforeFields <= 1, `${beforeFields} fields before`);
  const offer = page.locator("[role=dialog] .max-h-52 button");
  const offered = await offer.count();
  ok("the server is asked what it has", offered > 0, `${offered} collections offered`);
  await page.screenshot({ path: "/w/73-calendar-add.png" });
  // Picking one fills the name and the address in.
  await offer.first().click();
  await page.waitForTimeout(400);
  filled = await page.evaluate(() =>
    [...document.querySelectorAll("[role=dialog] input")].map((i) => i.value).filter(Boolean));
  ok("picking one fills name and address in", filled.length >= 2, filled.join(" | ").slice(0, 110));
  // Picking a collection makes the permission askable — and it starts at
  // nobody, because a calendar that turns out to be writable is not thereby one
  // that is written to.
  const perm = page.locator("[role=dialog] select").last();
  const states = await perm.locator("option").allInnerTexts();
  ok("who may write can be set per calendar", states.length === 3, states.join(" | "));
  ok("and starts at nobody", (await perm.inputValue()) === "none");
  await page.screenshot({ path: "/w/74-calendar-picked.png" });
  await shut();

  // And a subscription, which belongs to no login and offers no collections.
  await page.locator("button").filter({ hasText: /Abonnement hinzufügen/i }).first().click();
  await page.waitForTimeout(900);
  const none = await page.evaluate(() =>
    document.querySelector("[role=dialog] select")?.selectedOptions[0]?.text);
  ok("a subscription starts without a login", none === "Ohne Zugang", none);
  await page.screenshot({ path: "/w/75-subscription-add.png" });
  await shut();
} catch (e) {
  ok("run through", false, String(e).slice(0, 220));
} finally {
  ok("no error in the page", errors.length === 0, errors.join(" | "));
  await browser.close();
}
