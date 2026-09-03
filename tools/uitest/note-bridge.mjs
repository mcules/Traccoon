// Does the note area still ask the bridge anything at all?
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
const bridge = [];
const native = new Set();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e).slice(0, 200)));
page.on("response", (r) => {
  const u = r.url().replace(BASIS, "");
  if (/^\/api\/notes\//.test(u)) bridge.push(`${r.status()} ${u.slice(0, 80)}`);
  if (/^\/api\/notes-native\//.test(u)) native.add(u.split("?")[0]);
});

try {
  await page.goto(`${BASIS}/notes`, { waitUntil: "networkidle" });
  await page.waitForTimeout(4000);
  // Walk through the panels so as much as possible is asked for.
  for (const title of ["Suche", "Search", "Tags", "Kalender", "Calendar", "Graph"]) {
    const btn = page.locator(`[title="${title}"]`).first();
    if (await btn.count()) {
      await btn.click().catch(() => {});
      await page.waitForTimeout(1200);
    }
  }
  await page.waitForTimeout(2000);
  ok("nothing goes to the bridge any more", bridge.length === 0,
     [...new Set(bridge)].join(" | ").slice(0, 200));
  ok("and plenty goes to the house", native.size > 5, `${native.size} routes`);
  console.log("   " + [...native].sort().join("\n   "));
} catch (e) {
  ok("run through", false, String(e).slice(0, 200));
} finally {
  ok("no error in the page", errors.length === 0, errors.join(" | "));
}

// The assistant panel, which now talks to the house directly.
{
  const p2 = await ctx.newPage();
  const calls = [];
  p2.on("response", (r) => {
    const u = r.url().replace(BASIS, "");
    if (/assistant/.test(u)) calls.push(`${r.status()} ${u.slice(0, 70)}`);
  });
  // A note has to be open: the right sidebar's tabs belong to a note, not to
  // the graph.
  await p2.goto(`${BASIS}/notes/n/${"05 Daily Notes/2026/09/2026-09-03.md"
    .split("/").map(encodeURIComponent).join("/")}`, { waitUntil: "networkidle" });
  await p2.waitForTimeout(3000);
  const opener = p2.locator(".tab-new.tab-ctl").last();
  if (await opener.count()) await opener.click().catch(() => {});
  await p2.waitForTimeout(1500);
  for (const title of ["Assistent", "Assistant"]) {
    const btn = p2.locator(`[title="${title}"]`).first();
    if (await btn.count()) {
      await btn.click().catch(() => {});
      await p2.waitForTimeout(3500);
      break;
    }
  }
  const own = calls.filter((c) => /\/api\/assistant\/(sessions|chat)/.test(c));
  ok("the assistant panel asks the house itself", own.length > 0,
     [...new Set(own)].join(" | ").slice(0, 180) || "no session or chat call seen");
  ok("and nothing of it goes through the bridge",
     !calls.some((c) => c.includes("/api/notes/assistant")), "");
  await p2.screenshot({ path: "/w/50-assistant.png" });
  await browser.close();
}
