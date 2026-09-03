// The whole writing path through the ported routes, on a throwaway folder:
// create, save, rename, copy, versions, trash, restore, delete for good.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const DIR = process.env.ORDNER || "99 Temp/Portierungsprobe";
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
page.on("response", (r) => {
  const u = r.url().replace(BASIS, "");
  if (/^\/api\/notes\/files/.test(u)) bridge.push(`${r.status()} ${u.slice(0, 70)}`);
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
      const body = await r.json().catch(() => ({}));
      return { status: r.status, body };
    };
    const note = `${dir}/Probe.md`;
    const steps = {};
    steps.folder = await call("/files/folder", { method: "POST", body: JSON.stringify({ path: dir }) });
    steps.write = await call("/files/content", { method: "PUT",
      body: JSON.stringify({ path: note, content: "erste Fassung\n" }) });
    steps.again = await call("/files/content", { method: "PUT",
      body: JSON.stringify({ path: note, content: "zweite Fassung\n",
                             baseHash: steps.write.body.hash }) });
    steps.stale = await call("/files/content", { method: "PUT",
      body: JSON.stringify({ path: note, content: "dritte", baseHash: "falsch" }) });
    steps.read = await call(`/files/content?path=${encodeURIComponent(note)}`);
    steps.versions = await call(`/files/recovery?path=${encodeURIComponent(note)}`);
    steps.copy = await call("/files/copy", { method: "POST",
      body: JSON.stringify({ from: note, to: `${dir}/Kopie.md` }) });
    steps.rename = await call("/files/rename", { method: "PATCH",
      body: JSON.stringify({ from: `${dir}/Kopie.md`, to: `${dir}/Umbenannt.md` }) });
    steps.trash = await call(`/files/?path=${encodeURIComponent(`${dir}/Umbenannt.md`)}`,
      { method: "DELETE" });
    steps.restore = await call("/files/trash/restore", { method: "POST",
      body: JSON.stringify({ path: steps.trash.body.trashed }) });
    // Clean up: both notes and the folder go, for good.
    for (const p of [`${dir}/Umbenannt.md`, note]) {
      const gone = await call(`/files/?path=${encodeURIComponent(p)}`, { method: "DELETE" });
      if (gone.body.trashed)
        await call(`/files/trash/item?path=${encodeURIComponent(gone.body.trashed)}`,
                   { method: "DELETE" });
    }
    steps.cleanup = await call(`/files/?path=${encodeURIComponent(dir)}`, { method: "DELETE" });
    if (steps.cleanup.body.trashed)
      await call(`/files/trash/item?path=${encodeURIComponent(steps.cleanup.body.trashed)}`,
                 { method: "DELETE" });
    steps.left = await call(`/files/content?path=${encodeURIComponent(note)}`);
    return steps;
  }, DIR);

  ok("a folder is made", out.folder.status === 200, `HTTP ${out.folder.status}`);
  ok("a note is written", out.write.status === 200 && !!out.write.body.hash);
  ok("a second save with the right version goes through", out.again.status === 200);
  ok("a save on a stale version is refused", out.stale.status === 409,
     `HTTP ${out.stale.status}`);
  ok("what was read back is the second version",
     out.read.body.content === "zweite Fassung\n", JSON.stringify(out.read.body.content));
  ok("the replaced text was kept", (out.versions.body.snapshots ?? []).length > 0,
     `${(out.versions.body.snapshots ?? []).length} kept`);
  ok("a copy is made", out.copy.status === 200);
  ok("a rename goes through", out.rename.status === 200);
  ok("deleting means the trash", !!out.trash.body.trashed, out.trash.body.trashed ?? "");
  ok("restoring puts it back", out.restore.status === 200 && !!out.restore.body.restored);
  ok("nothing of the probe is left", out.left.status === 404, `HTTP ${out.left.status}`);
} catch (e) {
  ok("run through", false, String(e).slice(0, 220));
} finally {
  console.log("\nstill over the bridge:", bridge.length ? [...new Set(bridge)].join("\n   ") : "none");
}

// And once through the editor itself: type into a note and let it save.
{
  const page2 = await ctx.newPage();
  const note = `${DIR}/Tippprobe.md`;
  const seen = [];
  page2.on("response", (r) => {
    const u = r.url().replace(BASIS, "");
    if (/files\/content/.test(u) && r.request().method() === "PUT")
      seen.push(`${r.status()} ${u.slice(0, 60)}`);
  });
  const t = TOKEN;
  await page2.goto(`${BASIS}/notes`, { waitUntil: "networkidle" });
  await page2.evaluate(async ([n, tok]) => {
    await fetch("/api/notes-native/files/content", {
      method: "PUT",
      headers: { "content-type": "application/json", authorization: `Bearer ${tok}` },
      body: JSON.stringify({ path: n, content: "Anfang\n" }),
    });
  }, [note, t]);
  await page2.goto(`${BASIS}/notes/n/${note.split("/").map(encodeURIComponent).join("/")}`,
                   { waitUntil: "networkidle" });
  await page2.waitForTimeout(3000);
  await page2.locator(".cm-content").first().click();
  await page2.keyboard.press("End");
  await page2.keyboard.type(" — im Editor getippt");
  await page2.waitForTimeout(4000);
  const onDisk = await page2.evaluate(async ([n, tok]) => {
    const r = await fetch("/api/notes-native/files/content?path=" + encodeURIComponent(n),
                          { headers: { authorization: `Bearer ${tok}` } });
    return (await r.json()).content;
  }, [note, t]);
  ok("what was typed is on disk", (onDisk ?? "").includes("im Editor getippt"),
     JSON.stringify(onDisk ?? "").slice(0, 80));
  ok("the save went to the ported route", seen.some((s) => s.includes("notes-native")),
     seen.join(" | ") || "no save seen");
  await page2.evaluate(async ([n, tok]) => {
    const head = { authorization: `Bearer ${tok}` };
    const gone = await (await fetch("/api/notes-native/files/?path=" + encodeURIComponent(n),
                                    { method: "DELETE", headers: head })).json();
    if (gone.trashed)
      await fetch("/api/notes-native/files/trash/item?path=" + encodeURIComponent(gone.trashed),
                  { method: "DELETE", headers: head });
    const d = n.split("/").slice(0, -1).join("/");
    const dg = await (await fetch("/api/notes-native/files/?path=" + encodeURIComponent(d),
                                  { method: "DELETE", headers: head })).json();
    if (dg.trashed)
      await fetch("/api/notes-native/files/trash/item?path=" + encodeURIComponent(dg.trashed),
                  { method: "DELETE", headers: head });
  }, [note, t]);
  await browser.close();
}
