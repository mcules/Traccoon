// A dataviewjs block runs, and a task ticked off in a query result lands in the
// note it lives in — both over the ported routes.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const DIR = process.env.ORDNER || "99 Temp/Aufgabenprobe";
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
page.on("pageerror", (e) => errors.push(String(e).slice(0, 200)));
page.on("response", (r) => {
  const u = r.url().replace(BASIS, "");
  if (/dataview\/(script|task)/.test(u)) calls.push(`${r.status()} ${u.slice(0, 72)}`);
});

try {
  await page.goto(`${BASIS}/notes`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2500);

  const out = await page.evaluate(async (dir) => {
    const t = localStorage.getItem("traccoon_token");
    const call = async (path, opts = {}) => {
      const r = await fetch(`/api/notes-native${path}`, {
        ...opts,
        headers: { "content-type": "application/json", authorization: `Bearer ${t}`,
                   ...(opts.headers ?? {}) },
      });
      return { status: r.status, body: await r.json().catch(() => ({})) };
    };
    const note = `${dir}/Aufgaben.md`;
    const steps = {};
    await call("/files/folder", { method: "POST", body: JSON.stringify({ path: dir }) });
    await call("/files/content", { method: "PUT", body: JSON.stringify({
      path: note,
      content: "- [ ] Erste Aufgabe\n- [ ] Wiederkehrend 🔁 every week 📅 2026-09-03\n",
    })});

    // A block of the query language: registered, then imported back.
    const reg = await call("/dataview/script", { method: "POST",
      body: JSON.stringify({ code: "return 40 + 2;" }) });
    const mod = await import(`/api/notes-native/dataview/script/${reg.body.id}.mjs`);
    steps.script = { id: !!reg.body.id, value: await mod.default(null, null, null, null) };

    // Tick the first task off, from a result rather than from the editor.
    steps.tick = await call("/dataview/task", { method: "POST", body: JSON.stringify({
      path: note, line: 0, text: "Erste Aufgabe", checked: true, mode: "tasks" })});
    // And the recurring one, which has to come back.
    steps.again = await call("/dataview/task", { method: "POST", body: JSON.stringify({
      path: note, line: 1, text: "Wiederkehrend 🔁 every week 📅 2026-09-03",
      checked: true, mode: "tasks" })});
    steps.after = await call(`/files/content?path=${encodeURIComponent(note)}`);
    // A result that is out of date must not write.
    steps.stale = await call("/dataview/task", { method: "POST", body: JSON.stringify({
      path: note, line: 0, text: "Etwas ganz anderes", checked: false, mode: "tasks" })});

    for (const p of [note, dir]) {
      const gone = await call(`/files/?path=${encodeURIComponent(p)}`, { method: "DELETE" });
      if (gone.body.trashed)
        await call(`/files/trash/item?path=${encodeURIComponent(gone.body.trashed)}`,
                   { method: "DELETE" });
    }
    return steps;
  }, DIR);

  ok("a block is registered and imports back", out.script.id && out.script.value === 42,
     JSON.stringify(out.script));
  ok("a tick lands in the note", out.tick.status === 200, `HTTP ${out.tick.status}`);
  ok("a recurring task comes back", out.again.body.recurred === true,
     JSON.stringify(out.again.body));
  const text = out.after.body.content ?? "";
  ok("the note says what happened",
     text.includes("- [x] Erste Aufgabe ✅") && text.includes("📅 2026-09-10"),
     JSON.stringify(text).slice(0, 150));
  ok("an out-of-date result does not write", out.stale.status === 409,
     `HTTP ${out.stale.status}`);
} catch (e) {
  ok("run through", false, String(e).slice(0, 220));
} finally {
  console.log("\ncalls:", [...new Set(calls)].join("\n       "));
  ok("no error in the page", errors.length === 0, errors.join(" | "));
  await browser.close();
}
