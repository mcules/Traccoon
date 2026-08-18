# Browser probe for the flow editor

What only shows up in a browser: whether the building blocks are really in the palette,
whether the dropdowns get filled (269 MCP tools), whether the context fields grow out of the
graph, and whether "close" takes you back to where you came from.

Unit tests cover behaviour, not operation. This probe found two things no test would have
seen: a freshly created flow was a **completely empty canvas** (no start node, nothing to
click), and the origin column of the context fields ran out of its panel.

## Running it

```bash
# Login token, no password needed. Same value the frontend stores after login.
docker compose exec -T backend sh -lc \
  'cd /app && python -c "from app.core.security import create_access_token; print(create_access_token(13))"' \
  > tools/uitest/tok.txt

# The image ships the browsers, the npm package has to sit next to the script once.
docker run --rm -v "$PWD/tools/uitest":/w -w /w \
  mcr.microsoft.com/playwright:v1.56.0-noble npm i playwright-core@1.56.0

docker run --rm --network traccoon_default -v "$PWD/tools/uitest":/w -w /w \
  -e BASIS=http://frontend mcr.microsoft.com/playwright:v1.56.0-noble node /w/ablauf-editor.mjs
```

Screenshots land next to it (`01-…png` through `11-…png`), the log in `befund.txt`.

The other probes start the same way:

| Probe | What it covers |
|---|---|
| `bedienbarkeit.mjs` | overflow, touch targets, font sizes and hidden content across 29 screens at 390 and 1400 px. Writes `befund-bedienbarkeit.json` and compares against the previous run |
| `handy-editor.mjs` | the flow editor on a phone: tap a block, change it, attach a new one. Saves nothing |
| `messreihen.mjs` | the measurement series view |
| `editor-stand.mjs` | the unsaved and published markers in the editor |
| `abschalter.mjs` | switching a step off, skip or stop |
| `sprache.mjs`, `sprachverwaltung.mjs` | switching language, and creating, renaming, disabling and deleting one |
| `schuss.mjs` | screenshots only, `SEITEN=name:/pfad,...` and `BREIT=1` for the desktop width |

**Clean up afterwards**, the probe creates real flows:

```sql
delete from workflow_instances where definition_id in
  (select id from workflow_definitions where key like 'uitest%');
delete from webhook_subs where workflow_definition_id in
  (select id from workflow_definitions where key like 'uitest%');
update workflow_definitions set current_version_id = null where key like 'uitest%';
delete from workflow_versions where definition_id in
  (select id from workflow_definitions where key like 'uitest%');
delete from workflow_definitions where key like 'uitest%';
```

The "describe instead of build" step really calls the model (about a minute). It only checks
that a sentence turns into a graph on the canvas and that "back" restores the previous
state, not what was drawn.
