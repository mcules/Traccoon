// The kept versions of a note: does the panel list them, show one, and does the
// status bar say how fresh the backup is?
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const NOTE = process.env.NOTIZ || "05 Daily Notes/2026/09/2026-09-02.md";
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
const calls = [];
page.on("pageerror", (e) => errors.push(String(e).slice(0, 160)));
page.on("response", (r) => {
  const u = r.url();
  if (u.includes("/api/notes")) calls.push(`${r.status()} ${u.replace(BASIS, "")}`);
});

try {
  await page.goto(`${BASIS}/notes/n/${NOTE.split("/").map(encodeURIComponent).join("/")}`,
                  { waitUntil: "networkidle" });
  await page.waitForTimeout(2500);

  const bar = await page.locator(".status-bar").innerText().catch(() => "");
  ok("the status bar names the backup", /Sicherung|Backup/i.test(bar), bar.replace(/\n/g, " · "));
  ok("no sync button left", !(await page.locator(".status-bar .clickable[title*='abgleich']")
      .count()), "");

  // The version panel opens from the note's own "more" menu.
  // The panel opens from the note's own "more" menu. Which button that is
  // depends on the view the note was last left in, so it is found by its title.
  const more = page.locator('button[title="Mehr"], button[title="More"]').first();
  await more.waitFor({ timeout: 15000 });
  await more.click({ force: true });
  await page.waitForTimeout(500);
  await page.screenshot({ path: "/w/30-versions-menu.png" });
  await page.getByText(/Versionsverlauf|version history/i).first().click();
  await page.waitForTimeout(2500);

  ok("the version panel opens", (await page.locator(".vh-head").count()) > 0);
  const rows = await page.locator(".vh-item").count();
  ok("versions are listed", rows > 0, `${rows} entries`);
  const meta = await page.locator(".vh-item-meta").first().innerText().catch(() => "");
  ok("a version says when and by whom", /\d/.test(meta), meta.replace(/\n/g, " "));
  await page.screenshot({ path: "/w/31-versions.png" });

  const preview = await page.locator(".vh-preview pre").first()
    .innerText().catch(() => "");
  ok("one version is shown", preview.trim().length > 50,
     `${preview.trim().length} characters`);

  // The second entry must be a different text, not the same note again.
  const second = page.locator(".vh-item").nth(1);
  if (await second.count()) {
    await second.click();
    await page.waitForTimeout(1500);
    const other = await page.locator(".vh-preview pre").first().innerText().catch(() => "");
    ok("an older version differs from the newest", other.trim() !== preview.trim(),
       `${other.trim().length} characters`);
    await page.screenshot({ path: "/w/32-versions-older.png" });
  }
} catch (e) {
  ok("run through", false, String(e).slice(0, 200));
} finally {
  console.log("\ncalls:", [...new Set(calls)].join("\n       "));
  ok("no error in the page", errors.length === 0, errors.join(" | "));
  await browser.close();
}
