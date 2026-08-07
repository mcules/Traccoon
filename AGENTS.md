# Hausordnung für Agenten in diesem Repository

Diese Datei liest jeder Agentenlauf zu Beginn (`runtime._read_conventions`). Sie ist kurz
gehalten: was hier steht, kostet in jedem Lauf Kontext, also steht hier nur, was nicht aus
dem Code selbst hervorgeht.

## Umfang: nur dein Ticket

Ändere ausschließlich, was dein Ticket und dein freigegebener Plan verlangen. Fällt dir
unterwegs ein anderer Fehler auf: **schreib ihn in dein Ergebnis, behebe ihn nicht.** Ein
Ticket, das nebenbei fremde Dateien anfasst, ist beim Merge ein Konflikt ohne Gewinner —
und die eigentliche Arbeit wird mit ihm zurückgehalten.

Am 2026-08-07 ist genau das passiert: ein Ticket über einen fehlschlagenden Job kam mit
einer umgebauten Provider-Fehlerbehandlung zurück, weil der Agent eine Fehlermeldung aus
dem Kommentarverlauf für seinen Auftrag hielt. Solche Meldungen (Worker-Neustart, Deadlock,
abgeschnittene Modell-Antwort) sind **Pannen der Infrastruktur, nie deine Aufgabe.**

## Dokumentation gehört in den Vault, nicht ins Repository

Projekt- und Stackwissen wird in Obsidian gepflegt (`02 Projekte/…`, `03 Bereiche/…`).
Lege **keine** Notizen, Umsetzungsstände oder Pfade wie `02 Projekte/...` im Repository an —
sie driften gegen die Vault-Fassung ab, und niemand weiß mehr, welche gilt. Im Repository
gehören: Code, Tests, `README.md`, Kommentare am Code.

## Kommentare erklären das Warum

Der Code sagt, was passiert. Ein Kommentar sagt, warum es so und nicht anders ist —
bevorzugt mit dem Fall, der zu dieser Lösung geführt hat. Kommentare, die die nächste Zeile
nacherzählen, werden beim Review beanstandet.

## Bauen und Prüfen

- `check` läuft im Worktree und muss grün sein, bevor du fertig meldest.
- Kein Deploy von Hand: dieses Projekt hat kein eigenes Stack-Verzeichnis, der Deployer
  lehnt den Auftrag ab. Live geht die Änderung über Abnahme und Merge.
- Tests gehören zur Änderung, nicht in ein Folgeticket.

## Wenn du nicht weiterkommst

Frag (`ask_human`), statt zu raten — aber erst, wenn die Frage wirklich nur ein Mensch
beantworten kann. Reicht dein Budget nicht, übergib sauber (`continue_later`): was du
gelernt hast, was erledigt ist, was als Nächstes ansteht.
