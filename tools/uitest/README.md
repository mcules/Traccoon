# Browser-Probe der Ablauf-Oberfläche

Was nur im Browser auffällt: ob die Bausteine wirklich in der Palette stehen, ob die
Auswahlen gefüllt werden (269 MCP-Werkzeuge!), ob die Kontextfelder aus dem Graphen
entstehen — und ob „Schließen" dorthin zurückführt, wo man hergekommen ist.

Die Unit-Tests decken das Verhalten ab, nicht die Bedienung. Diese Probe hat zwei Dinge
gefunden, die kein Test gesehen hätte: ein neu angelegter Ablauf war eine **völlig leere
Fläche** (kein Start-Knoten, nichts zum Anklicken), und die Herkunft-Spalte der
Kontextfelder lief aus dem Panel.

## Ausführen

```bash
# Token für die Anmeldung (ohne Passwort — dasselbe, was das Frontend nach dem Login ablegt)
docker compose exec -T backend sh -lc \
  'cd /app && python -c "from app.core.security import create_access_token; print(create_access_token(13))"' \
  > tools/uitest/tok.txt

# Playwright bringt die Browser mit, das npm-Paket muss einmal daneben
docker run --rm -v "$PWD/tools/uitest":/w -w /w \
  mcr.microsoft.com/playwright:v1.56.0-noble npm i playwright-core@1.56.0

docker run --rm --network traccoon_default -v "$PWD/tools/uitest":/w -w /w \
  -e BASIS=http://frontend mcr.microsoft.com/playwright:v1.56.0-noble node /w/ablauf-editor.mjs
```

Screenshots landen daneben (`01-…png` bis `11-…png`), das Protokoll in `befund.txt`.

**Hinterher aufräumen** — die Probe legt echte Abläufe an:

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

Der Baumeister-Schritt ruft **wirklich** das Modell (rund eine Minute) — er prüft nur,
ob aus dem Satz ein Graph auf der Fläche wird und ob „Zurück" den alten Stand
wiederherstellt, nicht was gezeichnet wurde.
