// Do the requests a browser makes on its own still work now that they go to the
// ported routes: a picture in a note (`<img src>`) and a template (`import()`)?
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
const errors = [];
const seen = [];
page.on("pageerror", (e) => errors.push(String(e).slice(0, 200)));
page.on("response", (r) => {
  const u = r.url().replace(BASIS, "");
  if (/notes-native\/(files\/content|templates)/.test(u)) seen.push(`${r.status()} ${u.slice(0, 90)}`);
});

try {
  await page.goto(`${BASIS}/notes`, { waitUntil: "networkidle" });
  await page.waitForTimeout(3000);

  // A picture: the note area asks for its bytes without any header of ours.
  // A picture in a note is fetched with no header of ours at all — the ticket
  // in the cookie is the whole credential.
  const picture = await page.evaluate(async () => {
    const tree = await (await fetch("/api/notes-native/files/", {
      headers: { authorization: "Bearer " + localStorage.getItem("traccoon_token") },
    })).json();
    const find = (node) => {
      if (node.type === "file" && /\.(png|jpe?g)$/i.test(node.path ?? "")) return node.path;
      for (const c of node.children ?? []) {
        const hit = find(c);
        if (hit) return hit;
      }
      return null;
    };
    const path = find(tree);
    if (!path) return { error: "no picture in the vault" };
    const r = await fetch("/api/notes-native/files/content?path=" + encodeURIComponent(path),
                          { headers: {} });
    return { path, status: r.status, bytes: (await r.blob()).size };
  });
  ok("a picture comes back without a header of ours",
     picture.status === 200 && picture.bytes > 100, JSON.stringify(picture).slice(0, 120));

  const folder = await page.evaluate(async () =>
    (await fetch("/api/notes-native/files/content?path=" + encodeURIComponent("07 Anhänge"),
                 { headers: {} })).status);
  ok("a folder asked for as a note is not an error of ours", folder === 404, `HTTP ${folder}`);

  // A template: compile, then import the module by URL.
  const tpl = await page.evaluate(async () => {
    const head = { authorization: "Bearer " + localStorage.getItem("traccoon_token") };
    const list = await (await fetch("/api/notes-native/templates", { headers: head })).json();
    const first = list.templates?.[0];
    if (!first) return { error: "no templates" };
    const c = await (await fetch("/api/notes-native/templates/compile", {
      method: "POST", headers: { ...head, "content-type": "application/json" },
      body: JSON.stringify({ path: first.path }),
    })).json();
    if (!c.id) return { error: JSON.stringify(c).slice(0, 120) };
    const mod = await import(`/api/notes-native/templates/module/${c.id}.mjs`);
    return { name: first.name, kind: typeof mod.default, interactive: c.interactive };
  });
  ok("a template compiles and imports back", tpl.kind === "function",
     JSON.stringify(tpl).slice(0, 140));
} catch (e) {
  ok("run through", false, String(e).slice(0, 220));
} finally {
  console.log("\ncalls:", [...new Set(seen)].join("\n       "));
  ok("no error in the page", errors.length === 0, errors.join(" | "));
  await browser.close();
}
