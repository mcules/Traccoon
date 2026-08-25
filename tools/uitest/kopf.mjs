// Kopfzeilen-Popup und die aufklappbare Anhangsliste. Beides nur ansehen, nichts anfassen.
import { chromium } from "playwright-core";
import { readFileSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const token = readFileSync("/w/tok.txt", "utf8").trim();
const browser = await chromium.launch();
const seite = await browser.newPage({ viewport: { width: 1600, height: 900 } });
await seite.addInitScript((t) => localStorage.setItem("traccoon_token", t), token);
await seite.goto(`${BASIS}/mail`, { waitUntil: "networkidle" });
await seite.waitForTimeout(2500);

// Die Mail mit den zehn eingebetteten Bildern.
const mit = seite.getByText("Ein Betreff").first();
if (await mit.count()) {
  await mit.click({ force: true });
  await seite.waitForTimeout(2500);
  const anhaenge = await seite.getByText(/\d+ Anhänge/).count();
  const zeilen = await seite.locator("main .divide-y > div").count();
  const text = await seite.locator("main").innerText();
  console.log("ANHAENGE", JSON.stringify({
    kopfzeileDa: anhaenge > 0,
    stehtNochMailingassets: text.includes("mailingassets"),
  }));
  await seite.screenshot({ path: "/w/kopf-01-anhaenge.png" });
}

const knopf = seite.getByText("Kopfzeilen", { exact: true }).first();
if (await knopf.count()) {
  await knopf.click();
  await seite.waitForTimeout(1500);
  const inhalt = await seite.locator("[role=dialog] pre").innerText().catch(() => "");
  console.log("KOPFZEILEN", JSON.stringify({
    zeilen: inhalt.split("\n").length,
    hat: ["Received", "From", "Authentication-Results", "DKIM"]
      .filter((n) => inhalt.includes(n)),
  }));
  await seite.screenshot({ path: "/w/kopf-02-popup.png" });
} else {
  console.log("KOPFZEILEN kein Knopf");
}
await browser.close();
