// End to end probe of the flow interface. No replacement for the unit tests: this is about
// what only shows in a browser, whether the blocks are really there, whether the dropdowns
// get filled, and whether closing takes you back where you came from.
import { chromium } from "playwright-core";
import { readFileSync, writeFileSync } from "node:fs";

const BASIS = process.env.BASIS || "http://frontend";
const TOKEN = readFileSync("/w/tok.txt", "utf8").trim();
const findings = [];
const ok = (what, good, detail = "") => {
  findings.push(`${good ? "OK  " : "FAIL"} ${what}${detail ? " — " + detail : ""}`);
  console.log(`${good ? "OK  " : "FAIL"} ${what}${detail ? " — " + detail : ""}`);
};

// The browser is in the image, the npm package only brings the driver.
const browser = await chromium.launch({
  executablePath: "/ms-playwright/chromium-1194/chrome-linux/chrome",
});
const ctx = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
// Sign in through the token, the same value the frontend stores after a login.
await ctx.addInitScript((t) => localStorage.setItem("traccoon_token", t), TOKEN);
const page = await ctx.newPage();
const consoleErrors = [];
page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text().slice(0, 200)));
page.on("pageerror", (e) => consoleErrors.push("pageerror: " + String(e).slice(0, 200)));

try {
  // 1) Process page: "own" is the first tab
  await page.goto(`${BASIS}/processes`, { waitUntil: "networkidle" });
  await page.waitForTimeout(1200);
  const ownVisible = await page.getByText("Your own flows").first().isVisible().catch(() => false);
  ok("the flows page opens on the tab \"Own\"", ownVisible);
  await page.screenshot({ path: "/w/01-flows.png" });

  // 2) Create a flow
  const stamp = Date.now().toString().slice(-6);
  const key = `uitest${stamp}`;
  const fields = page.locator("input");
  await fields.nth(0).fill(`${key}v`);
  await fields.nth(1).fill(`UI template ${stamp}`);
  // Template picker: the first select in the create area.
  const templatePicker = page.locator("select").first();
  const templateCount = await templatePicker.locator("option").count().catch(() => 0);
  ok("templates are on offer", templateCount >= 5, `${templateCount} entries (scaffold included)`);
  await templatePicker.selectOption("process-a-list").catch(() => {});
  await page.waitForTimeout(500);
  const hint = await page.getByText(/path to the list/i).first()
    .isVisible().catch(() => false);
  ok("the template explains what has to be adjusted", hint);
  await page.getByRole("button", { name: /create|add|\+/i }).first().click();
  await page.waitForURL(/\/workflows\/\d+/, { timeout: 15000 });
  await page.waitForTimeout(2000);
  const nodesFromTemplate = await page.locator(".react-flow__node").count();
  ok("the template brings a finished flow with it", nodesFromTemplate >= 5,
     `${nodesFromTemplate} nodes`);
  await page.screenshot({ path: "/w/09-template.png" });
  // Publish: only a published flow can be called as a subflow later, and that is exactly
  // what gets checked further down.
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: /^Publish$/i }).click().catch(() => {});
  await page.waitForTimeout(2500);
  const published = !(await page.getByText(/validation error/i).first()
    .isVisible().catch(() => false));
  ok("the template can be published without rework", published);
  await page.getByRole("button", { name: /Back to the flows/i }).click();
  await page.waitForTimeout(1500);

  // And now the empty one, which the rest of the probe builds on.
  const fields2 = page.locator("input");
  await fields2.nth(0).fill(key);
  await fields2.nth(1).fill(`UI probe ${stamp}`);
  await page.getByRole("button", { name: /create|add|\+/i }).first().click();
  await page.waitForURL(/\/workflows\/\d+/, { timeout: 15000 });
  ok("creating jumps into the editor", true, page.url().split("/").slice(-1)[0]);
  await page.waitForTimeout(1500);

  // 3) Blocks: the new ones have to be in the palette
  for (const name of ["For each", "Wait", "Action"]) {
    const there = await page.getByText(name, { exact: false }).first().isVisible().catch(() => false);
    ok(`the block "${name}" is in the palette`, there);
  }
  await page.screenshot({ path: "/w/02-editor.png" });

  // 4) Click the start node → create a webhook address
  await page.locator(".react-flow__node").first().click();
  await page.waitForTimeout(600);

  // Trigger kind: with "event" an incoming address has no business being there.
  const kindPicker = page.locator("select").first();
  await kindPicker.selectOption("ereignis").catch(() => {});
  await page.waitForTimeout(600);
  const addressOnEvent = await page.getByText(/Incoming address/i).first()
    .isVisible().catch(() => false);
  ok("an event trigger shows NO incoming address", !addressOnEvent);
  await kindPicker.selectOption("webhook").catch(() => {});
  await page.waitForTimeout(600);
  const addressOnWebhook = await page.getByText(/Incoming address/i).first()
    .isVisible().catch(() => false);
  ok("a webhook trigger shows the incoming address", addressOnWebhook);

  const button = page.getByRole("button", { name: /Create an address/i });
  if (await button.isVisible().catch(() => false)) {
    await button.click();
    await page.waitForTimeout(1500);
    const url = await page.getByText(/\/api\/hooks\//).first().textContent().catch(() => "");
    ok("the webhook address is created and shown", !!url, (url || "").trim().slice(0, 60));
  } else {
    ok("the webhook address is created and shown", false, "the button was not found");
  }
  // Enter a sample payload — the context fields should grow out of it
  const sample = page.locator("textarea").first();
  if (await sample.isVisible().catch(() => false)) {
    await sample.fill('{"matter": {"title": "A fault", "id": 42}, "items": [1,2,3]}');
    await page.waitForTimeout(800);
    ok("a sample payload can be entered", true);
  }
  await page.screenshot({ path: "/w/03-start-webhook.png" });

  // 5) Tool node: does the tool picker appear?
  const palette = page.getByText("Action", { exact: false }).first();
  const canvas = page.locator(".react-flow__pane");
  // Drag onto the line between trigger and end, the block belongs in between.
  const line = page.locator(".react-flow__edge").first();
  const box = await line.boundingBox();
  await palette.dragTo(canvas, {
    targetPosition: box
      ? { x: box.x + box.width / 2 - 220, y: box.y + box.height / 2 - 60 }
      : { x: 420, y: 380 },
  });
  await page.waitForTimeout(900);
  const edgesAfterInsert = await page.locator(".react-flow__edge").count();
  ok("a block dropped on the line is hung in between", edgesAfterInsert >= 2,
     `${edgesAfterInsert} connections`);
  const errorHint = await page.getByText(/validation error/i).first()
    .isVisible().catch(() => false);
  ok("no node hangs in the air", !errorHint);
  const nodes = page.locator(".react-flow__node");
  await nodes.nth(await nodes.count() - 1).click();
  await page.waitForTimeout(600);
  const picker = page.locator("select").first();
  if (await picker.isVisible().catch(() => false)) {
    // Pick by value, the label carries the "(MCP)" suffix.
    await picker.selectOption("tool_call").catch((e) => console.log("     picker:", String(e).slice(0, 80)));
    await page.waitForTimeout(2500);
    const chosen = await picker.inputValue().catch(() => "");
    ok("the action \"call a tool\" can be chosen", chosen === "tool_call", chosen);
    // The tool field is the select right below the action picker.
    const toolSelect = page.locator("select").nth(1);
    const count = await toolSelect.locator("option").count().catch(() => 0);
    ok("the tool picker is filled with MCP tools", count > 50, `${count} entries`);
    for (const i of [1, 60, 200]) {
      const t = await toolSelect.locator("option").nth(i).textContent().catch(() => "");
      if (t) console.log(`     example ${i}:`, t.trim().slice(0, 70));
    }
  }
  await page.screenshot({ path: "/w/04-tool.png" });

  // 6b) Dry run, as long as the graph is sound
  const dryRunButton = page.getByRole("button", { name: /Dry run/i });
  if (await dryRunButton.isVisible().catch(() => false)) {
    await dryRunButton.click();
    await page.waitForTimeout(3500);
    const panel = page.getByText(/Dry run — \d+ step/i).first();
    const visible = await panel.isVisible().catch(() => false);
    ok("the dry run shows its steps", visible,
       (await panel.textContent().catch(() => "") || "").trim());
    const would = await page.getByText(/would run/i).first().textContent().catch(() => "");
    ok("the dry run reports what it would do", !!would, (would || "").trim().slice(0, 70));
    await page.screenshot({ path: "/w/07-dry-run.png" });
    await page.getByTitle("close").first().click().catch(() => {});
    await page.waitForTimeout(400);
  } else {
    ok("the dry run button is there", false);
  }

  // 6) Decision: context fields plus the filter help
  const decision = page.getByText("Decision", { exact: false }).first();
  const before = await page.locator(".react-flow__edge").count();
  await decision.dragTo(canvas, { targetPosition: { x: 860, y: 300 } });
  await page.waitForTimeout(900);
  const after = await page.locator(".react-flow__edge").count();
  ok("a new block hangs itself onto the selected node", after > before,
     `${before} → ${after} connections`);
  const nodes2 = page.locator(".react-flow__node");
  await nodes2.nth(await nodes2.count() - 1).click();
  await page.waitForTimeout(700);
  const fieldHelp = page.getByText(/Available context fields/i).first();
  const filterHelp = page.getByText(/Template filters/i).first();
  ok("the decision shows the available context fields",
     await fieldHelp.isVisible().catch(() => false),
     (await fieldHelp.textContent().catch(() => "") || "").trim());
  ok("the decision shows the template filters",
     await filterHelp.isVisible().catch(() => false),
     (await filterHelp.textContent().catch(() => "") || "").trim());
  if (await fieldHelp.isVisible().catch(() => false)) {
    await fieldHelp.click();
    await page.waitForTimeout(500);
    const showsSample = await page.getByText("matter.title").first().isVisible().catch(() => false);
    ok("the fields from the sample payload are on offer", showsSample);
  }
  await page.screenshot({ path: "/w/05-decision.png" });


  // 6d) Describe instead of build. This really calls the model, hence the generous timeout
  // and the single question whether a sentence turns into a graph on the canvas.
  const builderOpen = page.getByRole("button", { name: /Describe instead of building/i });
  if (await builderOpen.isVisible().catch(() => false)) {
    await builderOpen.click();
    await page.waitForTimeout(500);
    const field = page.locator("textarea").last();
    await field.fill("Call a destination and send me a message when it fails");
    // Building on what is there is not the point here, it should draw anew.
    const onExisting = page.getByText(/Build on what is on the canvas/i).first();
    const box2 = await onExisting.locator("xpath=preceding-sibling::input").first()
      .isChecked().catch(() => false);
    if (box2) await onExisting.click().catch(() => {});
    const nodesBefore = await page.locator(".react-flow__node").count();
    await page.getByRole("button", { name: /Draw it/i }).click();
    await page.waitForTimeout(90000);
    const nodesAfter = await page.locator(".react-flow__node").count();
    ok("the builder draws a flow onto the canvas", nodesAfter !== nodesBefore,
       `${nodesBefore} → ${nodesAfter} nodes`);
    const backThere = await page.getByRole("button", { name: /Back to the previous state/i })
      .isVisible().catch(() => false);
    ok("the previous state can be brought back", backThere);
    await page.screenshot({ path: "/w/11-builder.png" });
    if (backThere) {
      await page.getByRole("button", { name: /Back to the previous state/i }).click();
      await page.waitForTimeout(1200);
      const again = await page.locator(".react-flow__node").count();
      ok("going back really restores the old state", again === nodesBefore,
         `${again} nodes`);
    }
  } else {
    ok("the builder is reachable in the editor", false);
  }

  // 6c) "Other flow": besides the fixed slots your own flows have to be selectable.
  const other = page.getByText("Other flow", { exact: false }).first();
  await other.dragTo(canvas, { targetPosition: { x: 640, y: 520 } });
  await page.waitForTimeout(900);
  const nodes3 = page.locator(".react-flow__node");
  await nodes3.nth(await nodes3.count() - 1).click();
  await page.waitForTimeout(1200);
  const flowPicker = page.locator("select").first();
  const groups = await flowPicker.locator("optgroup").count().catch(() => 0);
  const ownOptions = await flowPicker
    .locator('optgroup[label*="Own flows"] option').count().catch(() => 0);
  ok("the subflow separates the fixed slots from your own flows", groups >= 1, `${groups} groups`);
  ok("your own published flows are on offer", ownOptions > 0, `${ownOptions} flows`);
  await page.screenshot({ path: "/w/10-subflow.png" });

  // 7) Close leads back to the list (the spot that used to end in the settings)
  await page.getByRole("button", { name: /Back to the flows/i }).click();
  await page.waitForTimeout(1500);
  ok("closing leads back to your own flows",
     page.url().includes("/processes"), page.url());
  await page.screenshot({ path: "/w/06-back.png" });

  // 8) Operations: the history of a real run, what came back per step?
  await page.goto(`${BASIS}/processes/operations`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2000);
  const historyButton = page.getByRole("button", { name: /^History$/ }).first();
  if (await historyButton.isVisible().catch(() => false)) {
    await historyButton.click();
    await page.waitForTimeout(3000);
    const rows = await page.locator("li code").count().catch(() => 0);
    ok("operations unfolds the history of a run", rows > 0, `${rows} step rows`);
    const firstRow = await page.locator("li code").first().textContent().catch(() => "");
    ok("the history names the node of every step", !!firstRow, (firstRow || "").trim());
    const graphThere = await page.getByText(/Flow as a graph/i).first().isVisible().catch(() => false);
    ok("the graph sits in the disclosure, not in front of the log", graphThere);
    await page.screenshot({ path: "/w/08-history.png" });
  } else {
    ok("operations shows a history button", false, "no run is visible");
  }

  ok("no JavaScript errors in the console", consoleErrors.length === 0,
     consoleErrors.slice(0, 2).join(" | "));
  writeFileSync("/w/findings.txt", findings.join("\n") + "\n");
} finally {
  await browser.close();
}
