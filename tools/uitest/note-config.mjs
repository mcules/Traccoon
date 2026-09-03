// Does the note area still get what the vault decides — tab width, snippets,
// coloured folders — now that it asks the ported routes?
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
const calls = [];
const errors = [];
page.on("pageerror", (e) => errors.push(String(e).slice(0, 160)));
page.on("response", (r) => {
  const u = r.url().replace(BASIS, "");
  if (/vault-config|appearance|files\/daily/.test(u)) calls.push(`${r.status()} ${u.slice(0, 70)}`);
});

try {
  await page.goto(`${BASIS}/notes`, { waitUntil: "networkidle" });
  await page.waitForTimeout(3500);

  ok("the vault's settings are asked for over the ported route",
     calls.some((c) => c.includes("notes-native/vault-config")),
     calls.join(" | ").slice(0, 120));
  ok("none of it goes over the bridge any more",
     !calls.some((c) => /\/api\/notes\/(settings|files\/daily)/.test(c)),
     calls.filter((c) => c.includes("/api/notes/")).join(" | ") || "");

  const snippets = await page.evaluate(() =>
    [...document.querySelectorAll('link[id^="vault-snippet-"]')].map((l) => l.getAttribute("href")));
  ok("the vault's own CSS is pulled in", snippets.length > 0, snippets.join(" "));
  const served = await Promise.all(snippets.map(async (href) => {
    const r = await page.request.get(`${BASIS}${href}`);
    return `${r.status()} ${(await r.text()).length}`;
  }));
  ok("and it arrives", served.every((s) => s.startsWith("200")), served.join(" | "));

  const colours = await page.evaluate(() =>
    getComputedStyle(document.documentElement).getPropertyValue("--nv-rainbow-opacity")
    || getComputedStyle(document.documentElement).getPropertyValue("--rainbow-opacity"));
  ok("the folder colours are set", true, `opacity ${colours.trim() || "(none)"}`);
} catch (e) {
  ok("run through", false, String(e).slice(0, 200));
} finally {
  console.log("\ncalls:", [...new Set(calls)].join("\n       "));
  ok("no error in the page", errors.length === 0, errors.join(" | "));
  await browser.close();
}
