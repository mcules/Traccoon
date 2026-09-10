// Wird ein Nutzer ohne Passkey gefragt — und einmal, nicht immer?
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";
const HOST = process.env.HOST || "traccoon.afu.tools";
const TRAEFIK = process.env.TRAEFIK || "172.23.0.2";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();

const browser = await chromium.launch({
  executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome",
  args: [`--host-resolver-rules=MAP ${HOST} ${TRAEFIK}`] });
const ctx = await browser.newContext({ viewport: { width: 1100, height: 700 } });
const page = await ctx.newPage();
page.on("pageerror", (e) => console.log("  SEITENFEHLER:", String(e).slice(0, 200)));
const cdp = await ctx.newCDPSession(page);
await cdp.send("WebAuthn.enable");
await cdp.send("WebAuthn.addVirtualAuthenticator", { options: {
  protocol: "ctap2", transport: "internal", hasResidentKey: true,
  hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true } });

const banner = () => page.evaluate(() => {
  const b = [...document.querySelectorAll("div")].find(
    (d) => /Passkey einrichten und ohne Passwort|Set up a passkey and sign in/.test(d.textContent || "")
      && d.className.includes("border-brand"));
  return b ? (b.textContent || "").trim().slice(0, 90) : "(kein Angebot)";
});

try {
  await page.goto(`https://${HOST}/login`, { waitUntil: "networkidle" });
  await page.evaluate((t) => localStorage.setItem("traccoon_token", t), TOKEN);
  await page.goto(`https://${HOST}/`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2500);
  console.log("ohne Passkey:", await banner());
  await page.screenshot({ path: "/w/passkey-angebot.png" });

  // „Einrichten" muss den Schlüssel anlegen und das Angebot damit erledigen.
  await page.getByRole("button", { name: /^Einrichten$|^Set it up$/ }).click();
  await page.waitForTimeout(3500);
  console.log("nach dem Einrichten:", await banner());

  // Schlüssel wieder weg -> das Angebot muss wiederkommen (nichts wurde abgelehnt).
  const keys = await page.evaluate(async () => {
    const r = await fetch("/api/me/passkeys", {
      headers: { Authorization: "Bearer " + localStorage.getItem("traccoon_token") } });
    return (await r.json()).keys;
  });
  console.log("angelegte Schlüssel:", keys.map((k) => k.label));
  for (const k of keys) {
    await page.evaluate(async (id) => {
      await fetch(`/api/me/passkeys/${id}`, { method: "DELETE",
        headers: { Authorization: "Bearer " + localStorage.getItem("traccoon_token") } });
    }, k.id);
  }
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForTimeout(2500);
  console.log("nach dem Entfernen:", await banner());

  // „Nein, danke" muss es dauerhaft beenden — auch nach dem Neuladen.
  await page.getByRole("button", { name: /Nein, danke|No thanks/ }).click();
  await page.waitForTimeout(1500);
  console.log("nach „Nein, danke“:", await banner());
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForTimeout(2500);
  console.log("nach dem Neuladen:  ", await banner());
} finally {
  await browser.close();
}
