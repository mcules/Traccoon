// Was der Assistent tut, waehrend er es tut: sechs Zeilen offen, der Rest
// scrollt, und am Ende steht die Antwort an der Stelle der Sprechblase.
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

// Die Schritte kommen aus der API. Hier wird eine laufende Nachricht
// vorgetaeuscht, damit die Sonde keinen echten Lauf braucht und trotzdem misst,
// was auf dem Bildschirm passiert.
const SCHRITTE = [];
for (let i = 1; i <= 20; i++) {
  SCHRITTE.push({ seq: i * 2 - 1, kind: "tool_start", tool: `vault__notes_read`,
                  label: `03 Bereiche/Notiz ${i}.md`, text: "", ok: null, ms: null });
  SCHRITTE.push({ seq: i * 2, kind: "tool_result", tool: `vault__notes_read`,
                  label: "", text: "", ok: i !== 7, ms: 40 + i });
}

try {
  await ctx.route("**/api/assistant/chat?*", (route) =>
    route.fulfill({ json: { messages: [{
      id: 99, text: "Was steht heute an?", session_id: 1, status: "running",
      result: null, error: null, run_id: 1, pending_tool: null,
      created_at: new Date(Date.now() - 90_000).toISOString(), finished_at: null }], more: false } }));
  // Wie die echte Route: `after` schneidet ab, was der Aufrufer schon hat.
  // Einmal absichtlich NICHT, um zu messen, dass die Anzeige nach Nummer
  // zusammenfuehrt statt blind anzuhaengen.
  let ueberlappen = true;
  await ctx.route("**/api/assistant/chat/99/progress*", (route) => {
    const after = Number(new URL(route.request().url()).searchParams.get("after") || 0);
    const teil = ueberlappen ? SCHRITTE : SCHRITTE.filter((s) => s.seq > after);
    ueberlappen = false;
    route.fulfill({ json: { run_id: 1, running: true, steps: teil } });
  });

  // `networkidle` kommt hier nie: die Notizansicht haelt einen Socket offen.
  await page.goto(`${BASIS}/notes`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1500);
  // Die rechte Leiste auf den Assistenten stellen.
  const knopf = page.locator('[title*="ssistent"], button:has-text("Assistent")').first();
  if (await knopf.count()) await knopf.click();
  await page.waitForTimeout(3500);

  const kasten = page.locator(".assistant-steps").first();
  ok("die Schritte stehen in der Sprechblase", await kasten.count() > 0);
  if (await kasten.count()) {
    const zeilen = await kasten.locator(".assistant-step").count();
    ok("alle Schritte sind da, nicht nur die sichtbaren", zeilen === 20, `${zeilen} Zeilen`);
    const mass = await kasten.evaluate((el) => ({
      sichtbar: el.clientHeight, gesamt: el.scrollHeight,
      unten: el.scrollHeight - el.scrollTop - el.clientHeight,
      zeile: el.querySelector(".assistant-step")?.getBoundingClientRect().height ?? 0,
    }));
    const passen = mass.zeile ? mass.sichtbar / mass.zeile : 0;
    ok("etwa sechs Zeilen stehen offen", passen > 5.2 && passen < 6.8, passen.toFixed(2));
    ok("der Rest laesst sich scrollen", mass.gesamt > mass.sichtbar + 20,
       `${mass.gesamt} > ${mass.sichtbar}`);
    ok("die neueste Zeile steht im Blick", mass.unten < 40, `${mass.unten}px vom Ende`);
    const fehl = await kasten.locator(".assistant-step-mark.failed").count();
    ok("ein misslungener Schritt ist zu erkennen", fehl === 1, `${fehl} markiert`);
  }
  await page.screenshot({ path: "/w/80-assistant-steps.png" });

  // Fertig: die Sprechblase weicht der Antwort.
  await ctx.route("**/api/assistant/chat?*", (route) =>
    route.fulfill({ json: { messages: [{
      id: 99, text: "Was steht heute an?", session_id: 1, status: "done",
      result: "Heute steht **nichts** an.", error: null, run_id: 1, pending_tool: null,
      created_at: new Date(Date.now() - 90_000).toISOString(),
      finished_at: new Date().toISOString() }], more: false } }));
  await page.waitForTimeout(4000);
  ok("nach dem Ende ist die Sprechblase weg", await page.locator(".assistant-steps").count() === 0);
  const antwort = await page.locator(".assistant-msg.theirs").first().innerText().catch(() => "");
  ok("und die Antwort steht an ihrer Stelle", /nichts an/.test(antwort), antwort.slice(0, 60));
  await page.screenshot({ path: "/w/81-assistant-answer.png" });

  // Seiten wie in einem Messenger: meine Blase rechts, seine links, und keine
  // von beiden ueber die ganze Breite — sonst hat sie keine Seite mehr.
  const mass = await page.evaluate(() => {
    const log = document.querySelector(".assistant-log");
    const box = (el) => el.getBoundingClientRect();
    const l = box(log);
    const mein = document.querySelector(".assistant-msg.mine");
    const seins = document.querySelector(".assistant-msg.theirs");
    const farbe = (el) => getComputedStyle(el).backgroundColor;
    return {
      meinRechts: Math.round(l.right - box(mein).right),
      meinLinks: Math.round(box(mein).left - l.left),
      seinLinks: Math.round(box(seins).left - l.left),
      seinRechts: Math.round(l.right - box(seins).right),
      meinBreit: box(mein).width / l.width,
      farbenGleich: farbe(mein) === farbe(seins),
    };
  });
  ok("meine Blase steht rechts", mass.meinRechts < mass.meinLinks,
     `${mass.meinRechts}px vom rechten, ${mass.meinLinks}px vom linken Rand`);
  ok("seine Blase steht links", mass.seinLinks < mass.seinRechts,
     `${mass.seinLinks}px vom linken, ${mass.seinRechts}px vom rechten Rand`);
  ok("keine Blase nimmt die ganze Breite", mass.meinBreit < 0.9,
     `${Math.round(mass.meinBreit * 100)} %`);
  ok("die beiden sind farblich zu unterscheiden", !mass.farbenGleich);
  await page.screenshot({ path: "/w/82-assistant-seiten.png" });
} catch (e) {
  ok("durchgelaufen", false, String(e).slice(0, 220));
} finally {
  ok("kein Fehler auf der Seite", errors.length === 0, errors.join(" | "));
  await browser.close();
}
