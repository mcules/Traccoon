// Der ganze Passkey-Ablauf, mit einem virtuellen Schlüssel.
//
// Chromium kann per CDP einen Authenticator in Software stellen (`WebAuthn.*`). Damit läuft
// die echte Zeremonie: Schlüssel anlegen, abmelden, mit Benutzername + Schlüssel wieder
// hinein. Ohne das bliebe von einem Passkey-Login nur die Behauptung, es gehe.
//
// Die Seite MUSS unter der echten Domain laufen — ein Passkey hängt am Ursprung. Deshalb
// zeigt der Resolver `traccoon.afu.tools` auf Traefik im proxy-Netz statt ins Internet.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";

const HOST = process.env.HOST || "traccoon.afu.tools";
const TRAEFIK = process.env.TRAEFIK || "172.23.0.2";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();

const browser = await chromium.launch({
  executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome",
  args: [`--host-resolver-rules=MAP ${HOST} ${TRAEFIK}`],
});
const ctx = await browser.newContext({ viewport: { width: 900, height: 800 } });
const page = await ctx.newPage();
page.on("pageerror", (e) => console.log("  SEITENFEHLER:", String(e).slice(0, 200)));

// Ein Authenticator in Software, der sich wie ein Handy verhält.
const cdp = await ctx.newCDPSession(page);
await cdp.send("WebAuthn.enable");
const { authenticatorId } = await cdp.send("WebAuthn.addVirtualAuthenticator", {
  options: { protocol: "ctap2", transport: "internal", hasResidentKey: true,
             hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true },
});
console.log("virtueller Schlüssel:", authenticatorId);

try {
  // 1) Angemeldet: einen Passkey anlegen. Der Token wird EINMAL gesetzt, nicht per
  // `addInitScript` — das läuft bei jeder Navigation wieder und macht das Abmelden weiter
  // unten unmöglich.
  await page.goto(`https://${HOST}/login`, { waitUntil: "networkidle" });
  await page.evaluate((t) => localStorage.setItem("traccoon_token", t), TOKEN);
  await page.goto(`https://${HOST}/account/person`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2500);
  const feld = page.getByPlaceholder(/Name, z\. B\.|A name, e\.g\./).first();
  console.log("Passkey-Bereich da:", await feld.count());
  await feld.fill("Sonde");
  await page.getByRole("button", { name: /Passkey anlegen|Add a passkey/ }).click();
  await page.waitForTimeout(3000);
  const liste = await page.evaluate(() =>
    [...document.querySelectorAll("div")].map((d) => (d.textContent || "").trim())
      .filter((t) => t.startsWith("Sonde"))[0] || "(nicht in der Liste)");
  console.log("nach dem Anlegen:", liste.slice(0, 80));
  console.log("Schlüssel im Authenticator:",
    (await cdp.send("WebAuthn.getCredentials", { authenticatorId })).credentials.length);

  // 2) Abmelden und mit Benutzername + Passkey wieder hinein
  await page.evaluate(() => localStorage.removeItem("traccoon_token"));
  await page.goto(`https://${HOST}/login`, { waitUntil: "networkidle" });
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForTimeout(2500);
  console.log("auf der Seite:", await page.evaluate(() => ({
    pfad: location.pathname,
    felder: [...document.querySelectorAll("input")].map((i) => i.placeholder || i.type),
    knoepfe: [...document.querySelectorAll("button")].map((b) => (b.textContent||"").trim()),
  })));
  await page.locator('input[autocomplete="username"]').fill("McUles");
  await page.getByRole("button", { name: /Mit Passkey anmelden|Sign in with a passkey/ }).click();
  await page.waitForTimeout(4000);
  const drin = await page.evaluate(() => ({
    pfad: location.pathname,
    token: !!localStorage.getItem("traccoon_token"),
  }));
  console.log("nach dem Passkey-Login:", JSON.stringify(drin));
  await page.screenshot({ path: "/w/passkey.png" });
} finally {
  await browser.close();
}
