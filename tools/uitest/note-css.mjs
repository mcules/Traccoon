// Two directions at once: does the note area's stylesheet change the rest of
// the house, and does the house's reset break what a note renders to?
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const NOTE = process.env.NOTIZ || "05 Daily Notes/2026/09/2026-09-03.md";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();
const ok = (what, good, detail = "") =>
  console.log(`${good ? "OK  " : "FAIL"} ${what}${detail ? " — " + detail : ""}`);

const browser = await chromium.launch({
  executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome",
});
const ctx = await browser.newContext({ viewport: { width: 1500, height: 950 } });
await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const page = await ctx.newPage();

/** What a page looks like, in the few values a stray rule would move. */
const measure = () => page.evaluate(() => {
  const probe = (html) => {
    const host = document.createElement("div");
    host.style.position = "fixed";
    host.style.left = "-9999px";
    host.innerHTML = html;
    document.body.appendChild(host);
    const out = {};
    const link = host.querySelector("a");
    const li = host.querySelector("li");
    const h2 = host.querySelector("h2");
    const table = host.querySelector("table");
    out.link = getComputedStyle(link).color;
    out.list = getComputedStyle(li.parentElement).listStyleType;
    out.heading = getComputedStyle(h2).fontWeight + "/" + getComputedStyle(h2).fontSize;
    out.table = getComputedStyle(table).borderCollapse;
    host.remove();
    return out;
  };
  return {
    body: getComputedStyle(document.body).backgroundColor,
    plain: probe("<a href='#'>x</a><ul><li>y</li></ul><h2>z</h2><table><tr><td>t</td></tr></table>"),
  };
});

try {
  // A page of the house, before the note area has ever been opened.
  await page.goto(`${BASIS}/projects`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);
  const before = await measure();

  // Open the notes, which loads their stylesheet into the document…
  await page.goto(`${BASIS}/notes/n/${NOTE.split("/").map(encodeURIComponent).join("/")}`,
                  { waitUntil: "networkidle" });
  await page.waitForTimeout(3500);
  const sheets = await page.evaluate(() =>
    [...document.styleSheets].filter((s) => {
      try { return [...s.cssRules].some((r) => (r.selectorText ?? "").includes(".markdown-preview")); }
      catch { return false; }
    }).length);
  ok("the note stylesheet is in the document", sheets > 0, `${sheets} sheet(s)`);

  // …and what a note renders to must still look like markdown. The reading
  // view, not the editor: in the editor the bullet is part of the text and the
  // list has no marker of its own, which is right there and would be wrong here.
  // Into the reading view, which is where markdown is rendered rather than
  // written. The editor shows its own bullets as text and would answer the
  // wrong question.
  const reading = page.locator('button:text-is("Reading")').first();
  if (await reading.count()) await reading.click().catch(() => {});
  await page.waitForTimeout(3000);
  const rendered = await page.evaluate(() => {
    // The note itself, not the assistant's answers beside it.
    // Any rendered markdown will do for this question — the note's reading view
    // if it is open, otherwise the assistant's answers, which use the same
    // container and are subject to the same reset.
    const all = [...document.querySelectorAll(".markdown-preview")];
    const root = all.find((el) => el.querySelector("li:not(.task-list-item)")) ?? all[0];
    if (!root) return { error: "nothing rendered" };
    const li = root.querySelector("li:not(.task-list-item)");
    const h = root.querySelector("h1, h2, h3");
    return {
      where: root.className.slice(0, 40),
      list: li ? getComputedStyle(li.parentElement).listStyleType : "(no list)",
      marker: li ? getComputedStyle(li).display : "(no list)",
      heading: h ? getComputedStyle(h).fontWeight + "/" + getComputedStyle(h).fontSize : "(none)",
    };
  });
  ok("a list in a note still has its bullets", rendered.list !== "none",
     JSON.stringify(rendered));
  ok("a heading in a note is still a heading",
     rendered.heading === "(none)" || parseInt(rendered.heading) >= 600,
     rendered.heading);

  // Back to the house page: nothing there may have moved.
  await page.goto(`${BASIS}/projects`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);
  const stillThere = await page.evaluate(() =>
    [...document.styleSheets].filter((s) => {
      try { return [...s.cssRules].some((r) => (r.selectorText ?? "").includes(".markdown-preview")); }
      catch { return false; }
    }).length);
  console.log(`     (the note stylesheet on a house page: ${stillThere} sheet(s))`);
  const after = await measure();
  for (const key of ["body"]) {
    ok(`the house's ${key} is unchanged`, before[key] === after[key],
       `${before[key]} → ${after[key]}`);
  }
  for (const key of ["link", "list", "heading", "table"]) {
    ok(`a plain ${key} in the house is unchanged`,
       before.plain[key] === after.plain[key],
       `${before.plain[key]} → ${after.plain[key]}`);
  }

  // And the same question for printing, which is where the loudest rule lives.
  await page.emulateMedia({ media: "print" });
  const printed = await measure();
  await page.emulateMedia({ media: "screen" });
  ok("printing a page of the house is unchanged too",
     printed.body === after.body, `${after.body} → ${printed.body}`);
} catch (e) {
  ok("run through", false, String(e).slice(0, 220));
} finally {
  await browser.close();
}
