// End to end probe of the flow interface. No replacement for the unit tests: this is about
// what only shows in a browser, whether the blocks are really there, whether the dropdowns
// get filled, and whether closing takes you back where you came from.
import { chromium } from "playwright-core";
import { readFileSync, writeFileSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();
const befund = [];
const ok = (was, gut, detail = "") => {
  befund.push(`${gut ? "OK  " : "FEHL"} ${was}${detail ? " — " + detail : ""}`);
  console.log(`${gut ? "OK  " : "FEHL"} ${was}${detail ? " — " + detail : ""}`);
};

// The browser is in the image, the npm package only brings the driver.
const browser = await chromium.launch({
  executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome",
});
const ctx = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
// Sign in through the token, the same value the frontend stores after a login.
await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const page = await ctx.newPage();
const fehlerImLog = [];
page.on("console", (m) => m.type() === "error" && fehlerImLog.push(m.text().slice(0, 200)));
page.on("pageerror", (e) => fehlerImLog.push("pageerror: " + String(e).slice(0, 200)));

try {
  // 1) Process page: "own" is the first tab
  await page.goto(`${BASIS}/processes`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1200);
  const eigeneSichtbar = await page.getByText("Eigene Prozesse").first().isVisible().catch(() => false);
  ok("Prozesse öffnen sich beim Reiter „Eigene\"", eigeneSichtbar);
  await page.screenshot({ path: "/w/01-prozesse.png" });

  // 2) Ablauf anlegen
  const stempel = Date.now().toString().slice(-6);
  const key = `uitest${stempel}`;
  const felder = page.locator("input");
  await felder.nth(0).fill(`${key}v`);
  await felder.nth(1).fill(`UI-Vorlage ${stempel}`);
  // Template picker: the first select in the create area.
  const vorlagenAuswahl = page.locator("select").first();
  const vorlagenZahl = await vorlagenAuswahl.locator("option").count().catch(() => 0);
  ok("Vorlagen stehen zur Auswahl", vorlagenZahl >= 5, `${vorlagenZahl} Einträge (mit Gerüst)`);
  await vorlagenAuswahl.selectOption("liste-abarbeiten").catch(() => {});
  await page.waitForTimeout(500);
  const hinweis = await page.getByText(/Schleife den Pfad zur Liste/i).first()
    .isVisible().catch(() => false);
  ok("Vorlage erklärt, was man anpassen muss", hinweis);
  await page.getByRole("button", { name: /anlegen|hinzufügen|\+/i }).first().click();
  await page.waitForURL(/\/workflows\/\d+/, { timeout: 15000 });
  await page.waitForTimeout(2000);
  const knotenAusVorlage = await page.locator(".react-flow__node").count();
  ok("Vorlage bringt einen fertigen Ablauf mit", knotenAusVorlage >= 5,
     `${knotenAusVorlage} Knoten`);
  await page.screenshot({ path: "/w/09-vorlage.png" });
  // Publish: only a published flow can be called as a subflow later, and that is exactly
  // what gets checked further down.
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: /Veröffentlichen/i }).click().catch(() => {});
  await page.waitForTimeout(2500);
  const veroeffentlicht = !(await page.getByText(/Validierungsfehler/i).first()
    .isVisible().catch(() => false));
  ok("Vorlage lässt sich ohne Nacharbeit veröffentlichen", veroeffentlicht);
  await page.getByRole("button", { name: /Zurück zu den Prozessen/i }).click();
  await page.waitForTimeout(1500);

  // And now the empty one, which the rest of the probe builds on.
  const felder2 = page.locator("input");
  await felder2.nth(0).fill(key);
  await felder2.nth(1).fill(`UI-Probe ${stempel}`);
  await page.getByRole("button", { name: /anlegen|hinzufügen|\+/i }).first().click();
  await page.waitForURL(/\/workflows\/\d+/, { timeout: 15000 });
  ok("Anlegen springt in den Editor", true, page.url().split("/").slice(-1)[0]);
  await page.waitForTimeout(1500);

  // 3) Blocks: the new ones have to be in the palette
  for (const name of ["Für jedes", "Warten", "Aktion"]) {
    const da = await page.getByText(name, { exact: false }).first().isVisible().catch(() => false);
    ok(`Baustein „${name}\" in der Palette`, da);
  }
  await page.screenshot({ path: "/w/02-editor.png" });

  // 4) Start-Knoten anklicken → Webhook-Adresse erzeugen
  await page.locator(".react-flow__node").first().click();
  await page.waitForTimeout(600);

  // Trigger kind: with "event" an incoming address has no business being there.
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

  // 5) Tool node: does the tool picker appear?
  const palette = page.getByText("Aktion", { exact: false }).first();
  const flaeche = page.locator(".react-flow__pane");
  // Drag onto the line between trigger and end, the block belongs in between.
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
    // Pick by value, the label carries the "(MCP)" suffix.
    await auswahl.selectOption("tool_call").catch((e) => console.log("     Auswahl:", String(e).slice(0, 80)));
    await page.waitForTimeout(2500);
    const gewaehlt = await auswahl.inputValue().catch(() => "");
    ok("Aktion Werkzeug-aufrufen laesst sich waehlen", gewaehlt === "tool_call", gewaehlt);
    // The tool field is the select right below the action picker.
    const werkzeugSelect = page.locator("select").nth(1);
    const anzahl = await werkzeugSelect.locator("option").count().catch(() => 0);
    ok("Werkzeug-Auswahl ist mit MCP-Werkzeugen gefüllt", anzahl > 50, `${anzahl} Einträge`);
    for (const i of [1, 60, 200]) {
      const t = await werkzeugSelect.locator("option").nth(i).textContent().catch(() => "");
      if (t) console.log(`     Beispiel ${i}:`, t.trim().slice(0, 70));
    }
  }
  await page.screenshot({ path: "/w/04-werkzeug.png" });

  // 6b) Dry run, as long as the graph is sound
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


  // 6d) Describe instead of build. This really calls the model, hence the generous timeout
  // and the single question whether a sentence turns into a graph on the canvas.
  const baumeisterAuf = page.getByRole("button", { name: /Beschreiben statt bauen/i });
  if (await baumeisterAuf.isVisible().catch(() => false)) {
    await baumeisterAuf.click();
    await page.waitForTimeout(500);
    const feld = page.locator("textarea").last();
    await feld.fill("Rufe ein Ziel auf und schicke mir eine Nachricht, wenn es fehlschlägt");
    // Building on what is there is not the point here, it should draw anew.
    const aufBestand = page.getByText(/Auf dem bauen, was auf der Fläche liegt/i).first();
    const box2 = await aufBestand.locator("xpath=preceding-sibling::input").first()
      .isChecked().catch(() => false);
    if (box2) await aufBestand.click().catch(() => {});
    const vorherKnoten = await page.locator(".react-flow__node").count();
    await page.getByRole("button", { name: /Zeichnen lassen/i }).click();
    await page.waitForTimeout(90000);
    const nachherKnoten = await page.locator(".react-flow__node").count();
    ok("Baumeister zeichnet einen Ablauf auf die Fläche", nachherKnoten !== vorherKnoten,
       `${vorherKnoten} → ${nachherKnoten} Knoten`);
    const zurueckDa = await page.getByRole("button", { name: /Zurück zum vorherigen Stand/i })
      .isVisible().catch(() => false);
    ok("Der vorherige Stand lässt sich wiederholen", zurueckDa);
    await page.screenshot({ path: "/w/11-baumeister.png" });
    if (zurueckDa) {
      await page.getByRole("button", { name: /Zurück zum vorherigen Stand/i }).click();
      await page.waitForTimeout(1200);
      const wieder = await page.locator(".react-flow__node").count();
      ok("Zurück stellt den alten Stand wirklich her", wieder === vorherKnoten,
         `${wieder} Knoten`);
    }
  } else {
    ok("Baumeister ist im Editor erreichbar", false);
  }

  // 6c) "Other flow": besides the fixed slots your own flows have to be selectable.
  const anderer = page.getByText("Anderer Ablauf", { exact: false }).first();
  await anderer.dragTo(flaeche, { targetPosition: { x: 640, y: 520 } });
  await page.waitForTimeout(900);
  const knoten3 = page.locator(".react-flow__node");
  await knoten3.nth(await knoten3.count() - 1).click();
  await page.waitForTimeout(1200);
  const ablaufAuswahl = page.locator("select").first();
  const gruppen = await ablaufAuswahl.locator("optgroup").count().catch(() => 0);
  const eigeneOpt = await ablaufAuswahl
    .locator('optgroup[label*="Eigene"] option').count().catch(() => 0);
  ok("Unterablauf trennt feste Slots von eigenen Abläufen", gruppen >= 1, `${gruppen} Gruppen`);
  ok("Eigene, veröffentlichte Abläufe stehen zur Wahl", eigeneOpt > 0, `${eigeneOpt} Abläufe`);
  await page.screenshot({ path: "/w/10-unterablauf.png" });

  // 7) Close leads back to the list (the spot that used to end in the settings)
  await page.getByRole("button", { name: /Zurück zu den Prozessen/i }).click();
  await page.waitForTimeout(1500);
  ok("Schließen führt zurück zu den eigenen Prozessen",
     page.url().includes("/processes"), page.url());
  await page.screenshot({ path: "/w/06-zurueck.png" });

  // 8) Operations: the history of a real run, what came back per step?
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
