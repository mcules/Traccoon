// End-zu-End-Probe der neuen Ablauf-Oberfläche. Kein Ersatz für die Unit-Tests — hier geht
// es um das, was nur im Browser auffällt: ob die Bausteine wirklich da sind, ob die
// Auswahlen gefüllt werden und ob das Schließen dorthin zurückführt, wo man herkam.
import { chromium } from "playwright-core";
import { readFileSync, writeFileSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();
const befund = [];
const ok = (was, gut, detail = "") => {
  befund.push(`${gut ? "OK  " : "FEHL"} ${was}${detail ? " — " + detail : ""}`);
  console.log(`${gut ? "OK  " : "FEHL"} ${was}${detail ? " — " + detail : ""}`);
};

// Browser liegt im Image, das npm-Paket bringt nur die Steuerung mit.
const browser = await chromium.launch({
  executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome",
});
const ctx = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
// Anmeldung über den Token — dasselbe, was das Frontend nach dem Login ablegt.
await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const page = await ctx.newPage();
const fehlerImLog = [];
page.on("console", (m) => m.type() === "error" && fehlerImLog.push(m.text().slice(0, 200)));
page.on("pageerror", (e) => fehlerImLog.push("pageerror: " + String(e).slice(0, 200)));

try {
  // 1) Prozess-Seite: „Eigene" ist der Startreiter
  await page.goto(`${BASIS}/processes`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1200);
  const eigeneSichtbar = await page.getByText("Eigene Prozesse").first().isVisible().catch(() => false);
  ok("Prozesse öffnen sich beim Reiter „Eigene\"", eigeneSichtbar);
  await page.screenshot({ path: "/w/01-prozesse.png" });

  // 2) Ablauf anlegen
  const stempel = Date.now().toString().slice(-6);
  const key = `uitest${stempel}`;
  const felder = page.locator("input");
  await felder.nth(0).fill(key);
  await felder.nth(1).fill(`UI-Probe ${stempel}`);
  await page.getByRole("button", { name: /anlegen|hinzufügen|\+/i }).first().click();
  await page.waitForURL(/\/workflows\/\d+/, { timeout: 15000 });
  ok("Anlegen springt in den Editor", true, page.url().split("/").slice(-1)[0]);
  await page.waitForTimeout(1500);

  // 3) Bausteine: die neuen müssen in der Palette stehen
  for (const name of ["Für jedes", "Warten", "Aktion"]) {
    const da = await page.getByText(name, { exact: false }).first().isVisible().catch(() => false);
    ok(`Baustein „${name}\" in der Palette`, da);
  }
  await page.screenshot({ path: "/w/02-editor.png" });

  // 4) Start-Knoten anklicken → Webhook-Adresse erzeugen
  await page.locator(".react-flow__node").first().click();
  await page.waitForTimeout(600);

  // Auslöser-Art: bei „Ereignis" hat eine eingehende Adresse nichts zu suchen.
  const artAuswahl = page.locator("select").first();
  await artAuswahl.selectOption("ereignis").catch(() => {});
  await page.waitForTimeout(600);
  const adresseBeiEreignis = await page.getByText(/Eingehende Adresse/i).first()
    .isVisible().catch(() => false);
  ok("Ereignis-Auslöser zeigt KEINE eingehende Adresse", !adresseBeiEreignis);
  await artAuswahl.selectOption("webhook").catch(() => {});
  await page.waitForTimeout(600);
  const adresseBeiWebhook = await page.getByText(/Eingehende Adresse/i).first()
    .isVisible().catch(() => false);
  ok("Webhook-Auslöser zeigt die eingehende Adresse", adresseBeiWebhook);

  const knopf = page.getByRole("button", { name: /Adresse erzeugen/i });
  if (await knopf.isVisible().catch(() => false)) {
    await knopf.click();
    await page.waitForTimeout(1500);
    const url = await page.getByText(/\/api\/hooks\//).first().textContent().catch(() => "");
    ok("Webhook-Adresse wird erzeugt und angezeigt", !!url, (url || "").trim().slice(0, 60));
  } else {
    ok("Webhook-Adresse wird erzeugt und angezeigt", false, "Knopf nicht gefunden");
  }
  // Beispiel-Nutzlast eintragen — daraus sollen Kontextfelder entstehen
  const probe = page.locator("textarea").first();
  if (await probe.isVisible().catch(() => false)) {
    await probe.fill('{"vorgang": {"titel": "Störung", "id": 42}, "posten": [1,2,3]}');
    await page.waitForTimeout(800);
    ok("Beispiel-Nutzlast lässt sich eintragen", true);
  }
  await page.screenshot({ path: "/w/03-start-webhook.png" });

  // 5) Werkzeug-Knoten: kommt die MCP-Auswahl?
  const palette = page.getByText("Aktion", { exact: false }).first();
  const flaeche = page.locator(".react-flow__pane");
  // Auf die Linie zwischen Auslöser und Ende ziehen — der Baustein gehört dazwischen.
  const linie = page.locator(".react-flow__edge").first();
  const box = await linie.boundingBox();
  await palette.dragTo(flaeche, {
    targetPosition: box
      ? { x: box.x + box.width / 2 - 220, y: box.y + box.height / 2 - 60 }
      : { x: 420, y: 380 },
  });
  await page.waitForTimeout(900);
  const kantenNachEinfuegen = await page.locator(".react-flow__edge").count();
  ok("Baustein auf der Linie wird dazwischen gehaengt", kantenNachEinfuegen >= 2,
     `${kantenNachEinfuegen} Verbindungen`);
  const fehlerhinweis = await page.getByText(/Validierungsfehler/i).first()
    .isVisible().catch(() => false);
  ok("Kein Knoten haengt in der Luft", !fehlerhinweis);
  const knoten = page.locator(".react-flow__node");
  await knoten.nth(await knoten.count() - 1).click();
  await page.waitForTimeout(600);
  const auswahl = page.locator("select").first();
  if (await auswahl.isVisible().catch(() => false)) {
    // Über den Wert wählen — die Beschriftung trägt den Zusatz „(MCP)".
    await auswahl.selectOption("tool_call").catch((e) => console.log("     Auswahl:", String(e).slice(0, 80)));
    await page.waitForTimeout(2500);
    const gewaehlt = await auswahl.inputValue().catch(() => "");
    ok("Aktion Werkzeug-aufrufen laesst sich waehlen", gewaehlt === "tool_call", gewaehlt);
    // Das Werkzeug-Feld ist das Select direkt unter der Aktionsauswahl.
    const werkzeugSelect = page.locator("select").nth(1);
    const anzahl = await werkzeugSelect.locator("option").count().catch(() => 0);
    ok("Werkzeug-Auswahl ist mit MCP-Werkzeugen gefüllt", anzahl > 50, `${anzahl} Einträge`);
    for (const i of [1, 60, 200]) {
      const t = await werkzeugSelect.locator("option").nth(i).textContent().catch(() => "");
      if (t) console.log(`     Beispiel ${i}:`, t.trim().slice(0, 70));
    }
  }
  await page.screenshot({ path: "/w/04-werkzeug.png" });

  // 6b) Probelauf — solange der Graph schlüssig ist
  const probeKnopf = page.getByRole("button", { name: /Probelauf/i });
  if (await probeKnopf.isVisible().catch(() => false)) {
    await probeKnopf.click();
    await page.waitForTimeout(3500);
    const panel = page.getByText(/Probelauf — \d+ Schritt/i).first();
    const sichtbar = await panel.isVisible().catch(() => false);
    ok("Probelauf zeigt seine Schritte", sichtbar,
       (await panel.textContent().catch(() => "") || "").trim());
    const wuerde = await page.getByText(/würde ausführen/i).first().textContent().catch(() => "");
    ok("Probelauf meldet, was er taete", !!wuerde, (wuerde || "").trim().slice(0, 70));
    await page.screenshot({ path: "/w/07-probelauf.png" });
    await page.getByTitle("schließen").first().click().catch(() => {});
    await page.waitForTimeout(400);
  } else {
    ok("Probelauf-Knopf vorhanden", false);
  }

  // 6) Verzweigung: Kontextfelder + Filter-Hilfe
  const verzweigung = page.getByText("Verzweigung", { exact: false }).first();
  const vorher = await page.locator(".react-flow__edge").count();
  await verzweigung.dragTo(flaeche, { targetPosition: { x: 860, y: 300 } });
  await page.waitForTimeout(900);
  const nachher = await page.locator(".react-flow__edge").count();
  ok("Neuer Baustein haengt sich an den ausgewaehlten Knoten", nachher > vorher,
     `${vorher} → ${nachher} Verbindungen`);
  const knoten2 = page.locator(".react-flow__node");
  await knoten2.nth(await knoten2.count() - 1).click();
  await page.waitForTimeout(700);
  const felderHilfe = page.getByText(/Verfügbare Kontext-Felder/i).first();
  const filterHilfe = page.getByText(/Vorlagen-Filter/i).first();
  ok("Verzweigung zeigt verfügbare Kontext-Felder",
     await felderHilfe.isVisible().catch(() => false),
     (await felderHilfe.textContent().catch(() => "") || "").trim());
  ok("Verzweigung zeigt die Vorlagen-Filter",
     await filterHilfe.isVisible().catch(() => false),
     (await filterHilfe.textContent().catch(() => "") || "").trim());
  if (await felderHilfe.isVisible().catch(() => false)) {
    await felderHilfe.click();
    await page.waitForTimeout(500);
    const zeigtProbe = await page.getByText("vorgang.titel").first().isVisible().catch(() => false);
    ok("Felder aus der Beispiel-Nutzlast stehen zur Auswahl", zeigtProbe);
  }
  await page.screenshot({ path: "/w/05-verzweigung.png" });


  // 7) Schließen → zurück zur Liste (der Punkt, der vorher in die Einstellungen führte)
  await page.getByRole("button", { name: /Zurück zu den Prozessen/i }).click();
  await page.waitForTimeout(1500);
  ok("Schließen führt zurück zu den eigenen Prozessen",
     page.url().includes("/processes"), page.url());
  await page.screenshot({ path: "/w/06-zurueck.png" });

  // 8) Betrieb: der Verlauf eines echten Laufs — was kam je Schritt zurück?
  await page.goto(`${BASIS}/processes/betrieb`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);
  const verlaufKnopf = page.getByRole("button", { name: /^Verlauf$/ }).first();
  if (await verlaufKnopf.isVisible().catch(() => false)) {
    await verlaufKnopf.click();
    await page.waitForTimeout(3000);
    const zeilen = await page.locator("li code").count().catch(() => 0);
    ok("Betrieb klappt den Verlauf eines Laufs auf", zeilen > 0, `${zeilen} Schritt-Zeilen`);
    const ersteZeile = await page.locator("li code").first().textContent().catch(() => "");
    ok("Verlauf nennt den Knoten je Schritt", !!ersteZeile, (ersteZeile || "").trim());
    const graphAn = await page.getByText(/Ablauf als Graph/i).first().isVisible().catch(() => false);
    ok("Graph steckt im Aufklapper, nicht vor dem Protokoll", graphAn);
    await page.screenshot({ path: "/w/08-verlauf.png" });
  } else {
    ok("Betrieb zeigt einen Verlauf-Knopf", false, "kein Lauf sichtbar");
  }

  ok("Keine JavaScript-Fehler in der Konsole", fehlerImLog.length === 0,
     fehlerImLog.slice(0, 2).join(" | "));
  writeFileSync("/w/befund.txt", befund.join("\n") + "\n");
} finally {
  await browser.close();
}
