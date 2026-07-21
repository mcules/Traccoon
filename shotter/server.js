"use strict";
// Traccoon Screenshot-Sidecar: rendert die (ggf. eingeloggte) Projekt-App und liefert ein PNG.
// POST /shot {base_url, target, waitMs} → image/png. Optionaler Login via SHOT_LOGIN_* gegen
// <base_url>/api/auth/login → Token in localStorage[SHOT_TOKEN_KEY], danach Deep-Link per Hash.
const http = require("http");
const { chromium } = require("playwright");

const PORT = parseInt(process.env.SHOTTER_PORT || "8700", 10);
const DEFAULT_BASE = process.env.SHOT_BASE_URL || "";
const LOGIN_EMAIL = process.env.SHOT_LOGIN_EMAIL || "";
const LOGIN_PASSWORD = process.env.SHOT_LOGIN_PASSWORD || "";
const TOKEN_KEY = process.env.SHOT_TOKEN_KEY || "traccoon_token";

let browser = null;
async function getBrowser() {
  if (!browser) browser = await chromium.launch({ args: ["--no-sandbox"] });
  return browser;
}

async function shot({ base_url, target, waitMs }) {
  const base = (base_url || DEFAULT_BASE).replace(/\/+$/, "");
  if (!base) throw new Error("kein base_url (weder Request noch SHOT_BASE_URL)");
  const b = await getBrowser();
  const ctx = await b.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  try {
    // optionaler Login → Token in localStorage VOR App-Start
    if (LOGIN_EMAIL) {
      try {
        const r = await page.request.post(`${base}/api/auth/login`, {
          data: { email: LOGIN_EMAIL, password: LOGIN_PASSWORD },
        });
        if (r.ok()) {
          const j = await r.json();
          const tok = j.access_token || j.token || "";
          if (tok) {
            await page.addInitScript(([k, v]) => localStorage.setItem(k, v), [TOKEN_KEY, tok]);
          }
        }
      } catch (_) { /* Login optional */ }
    }
    const url = target ? `${base}/#${target.replace(/^#/, "")}` : base + "/";
    await page.goto(url, { waitUntil: "networkidle", timeout: 30000 }).catch(() => {});
    await page.waitForTimeout(Math.min(parseInt(waitMs || "1200", 10), 8000));
    return await page.screenshot({ fullPage: false, type: "png" });
  } finally {
    await ctx.close();
  }
}

const server = http.createServer((req, res) => {
  if (req.method === "POST" && req.url === "/shot") {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", async () => {
      let args = {};
      try { args = JSON.parse(body || "{}"); } catch (_) {}
      try {
        const png = await shot(args);
        res.writeHead(200, { "Content-Type": "image/png" });
        res.end(png);
      } catch (e) {
        res.writeHead(500, { "Content-Type": "text/plain" });
        res.end("Screenshot-Fehler: " + e.message);
      }
    });
    return;
  }
  if (req.url === "/healthz") { res.writeHead(200); res.end("ok"); return; }
  res.writeHead(404); res.end("not found");
});

server.listen(PORT, () => console.log(`[shotter] http://0.0.0.0:${PORT}`));
