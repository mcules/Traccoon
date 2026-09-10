import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";
const HOST = process.env.HOST || "traccoon.afu.tools";
const TRAEFIK = process.env.TRAEFIK || "172.23.0.2";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();
const browser = await chromium.launch({
  executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome",
  args: [`--host-resolver-rules=MAP ${HOST} ${TRAEFIK}`] });
const page = await (await browser.newContext({ viewport: { width: 760, height: 640 } })).newPage();
try {
  await page.goto(`https://${HOST}/login`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: "/w/passkey-login.png" });
  await page.evaluate((t) => localStorage.setItem("traccoon_token", t), TOKEN);
  await page.goto(`https://${HOST}/account/person`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);
  const panel = page.locator("section").filter({ hasText: /Passkeys/ }).first();
  await panel.screenshot({ path: "/w/passkey-panel.png" });
  console.log("Bilder geschrieben");
} finally { await browser.close(); }
