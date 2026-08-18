# Traccoon

**Ein Ticketsystem, in dem KI-Agenten mitarbeiten — aber nur, wenn ein Mensch sie ruft.**

Traccoon ist ein selbst gehostetes Projekt- und Ticketsystem (Kanban, Sprints, Hardware-
Verwaltung) mit zwei Erweiterungen, die es von üblichen Werkzeugen unterscheiden:

1. **Agenten arbeiten am Ticket.** Ein KI-Agent plant, schreibt Code in einem eigenen
   Git-Worktree, prüft den Build und stellt zur Abnahme — ausgelöst ausschließlich durch
   eine ausdrückliche Zuweisung eines Menschen.
2. **Alles, was abläuft, ist ein Graph.** Vom KI-Lebenszyklus über die Beschaffung bis zum
   Maileingang: Abläufe werden gezeichnet, nicht programmiert. Wer ein fremdes System
   anbinden will, klickt es zusammen.

Der Code ist auf Deutsch kommentiert und benannt — Traccoon ist aus dem täglichen Betrieb
einer kleinen Infrastruktur entstanden, und die Erklärungen im Code richten sich an den,
der sie später wieder liest.

> **Stand:** läuft produktiv bei uns, aber es gibt noch keine Release-Versionen, keine
> Upgrade-Garantien und keine Mandantentrennung im Sinne von SaaS. Wer es ausprobiert,
> sollte Docker und Postgres kennen. Siehe [Stand & Grenzen](#stand--grenzen).

---

## Das Kernprinzip

Ein Agent rührt ein Ticket **nur bei ausdrücklicher Zuweisung** an. Zuweisen darf, wer das
KI-Recht `ai_assign` in diesem Projekt hat — ein eigenes Recht neben der Rolle. Ohne dieses
Recht ist die gesamte KI-Oberfläche unsichtbar, und Traccoon ist ein reines Ticketsystem.

Die Zuweisung geht in der Regel an den **Projektmanager-Agenten**: er entscheidet über
Besetzung und Aufteilung und delegiert an Ausführungs-Agenten. Zwei Stellen bleiben immer
beim Menschen — die **Plan-Freigabe** und die **Abnahme**.

---

## Funktionsumfang

### Projekte, Tickets, Hardware

- Hierarchische Projekte mit vererbbaren Mitgliedschaften, Rollen (owner/maintainer/
  member/viewer) und dem KI-Recht `ai_assign`
- Konfigurierbare Vorgangsarten und Zustände, Kanban-Board mit stabiler Sortierung, Sprints,
  gespeicherte Filter, Tags, Verknüpfungen, Anhänge
- Kommentare mit Sichtbarkeit (öffentlich/intern/Agent) — ein Kommentar kann einen
  wartenden Ablauf weiterschalten
- **Artefakte** als gemeinsames Modell: Ticket, Hardware-Exemplar und eigene Typen teilen
  Felder, Zustände und Prozesse. Projekte erweitern ihre Artefakte selbst, ohne Code
- **Hardware:** Katalog → Exemplar → Lagerort-Baum, Beschaffungskette mit Übergabe an
  Personen, granulare Freigaben je Standort/Gerät

### KI-Agenten

- Eigener Tool-Loop statt eines fremden Agenten-Rahmenwerks; spricht Abo-Zugänge ebenso an
  wie selbst gehostete Modelle über chat-kompatible HTTP-Endpunkte, mit Anbieter-Router,
  Cooldown und Ausweich-Anbieter
- Werkzeuge: Datei lesen/schreiben/ändern, Build prüfen, deployen, Screenshot, Rückfrage an
  den Menschen, Plan einreichen, später weitermachen
- **Berechtigungs-Gate zur Laufzeit** (erlauben/fragen/verweigern) mit Einmal-Freigaben —
  Freigabe kommt aus der Oberfläche oder per Nachricht
- **Git-Worktree je Ticket**, Vorab-Merge-Prüfung, Konflikte gehen an den Agenten zurück,
  Build-Gate vor der Abnahme, Testumgebung je Ticket
- Kosten-Erfassung je Lauf, Runaway-Bremsen, Nacht-/Feierabendfenster, Wanduhr-Grenze,
  Fortsetzung nach Iterations- oder Zeitlimit mit Übergabe statt Gedächtnisverlust
- **PM-Chat:** Gespräch mit dem Projektmanager, der daraus Tickets anlegt und verteilt
- **Büro:** eine Pixel-Ansicht aller laufenden Agenten — wer arbeitet gerade woran, und
  ein Feierabendfilm des Tages als GIF

### Prozess-Engine (das Herzstück)

Jeder Ablauf ist ein gerichteter Graph, gezeichnet im Browser:

| Baustein | wofür |
|---|---|
| Start | manuell, Ereignis in Traccoon oder eingehender Webhook |
| Aufgabe / Freigabe | Mensch macht etwas bzw. gibt frei (mit Formularfeldern) |
| Verzweigung | Bedingungen über den Lauf-Kontext (JSONLogic, mit Feldauswahl im Editor) |
| Aktion | Zustand setzen, Ticket anlegen, Kommentar, Ziel aufrufen, **Werkzeug rufen**, Messwert schreiben, benachrichtigen … |
| KI-Agent | Agentenlauf als Schritt im Ablauf |
| Warten auf Ereignis / Zeit | Kommentar, Antwort — oder schlicht die Uhr |
| Für jedes … | Liste Element für Element abarbeiten |
| Anderer Ablauf | einen zweiten Ablauf als Unterprozess starten |

Dazu:

- **Vorlagen** — vier fertige Abläufe als Startpunkt (Meldung von außen, geplante Prüfung
  mit Freigabe, Liste abarbeiten, Aufruf mit Wiederholung)
- **Beschreiben statt bauen** — ein Satz genügt, ein Modell zeichnet den Graphen; der
  Entwurf landet auf der Fläche, gespeichert wird von Hand
- **Probelauf** — der Ablauf läuft vollständig durch, jede Aktion meldet nur, was sie täte
- **Verlauf je Lauf** — was jeder Schritt zurückgab und welchen Zweig er nahm
- **Ausdrücke** `{{ pfad | filter:argument }}` mit 19 Filtern (kürzen, runden, Datum,
  Vorgabe …); unzitierte Filter-Argumente dürfen selbst Kontextpfade sein
- **Prozess-Sätze:** ein ausgelieferter Standard, persönliche und projekteigene Kopien
  (copy-on-write, jederzeit zurücksetzbar) für die fest benannten Abläufe
  (KI-Lebenszyklus, Abnahme, Beschaffung, Ticket-Eingang, Mail-Eingang)
- Eigene, projektlose Abläufe darf **jeder** anlegen — sie wirken nur auf Artefakte, an
  denen der Eigentümer selbst Rechte hat
- Wiederholungen mit Abstand, Fehlerausgang je Aktion, Schachtelungs- und Schrittbremsen

### Anbinden ohne Programmieren

- **Eingehend:** Webhooks mit GUID-Adresse, HMAC (optional), Filter auf Kopfzeile **oder**
  Nutzlast (`payload:event.type`), Idempotenz über ein beliebig tiefes Feld, Modi
  Ticket / Nachricht / Ereignis / Ablauf starten
- **Ausgehend:** **Ziele** — Basis-URL plus Anmeldung (basic, bearer, api-key, HMAC,
  OAuth2-Client-Credentials) zentral hinterlegt, aufrufbar aus Abläufen, Jobs und (nach
  Freigabe) von Agenten
- **Werkzeug-Server (MCP):** eigene Registry, Self-Service je Nutzer; jedes registrierte
  Werkzeug steht im Ablauf-Editor als Aktion zur Auswahl
- **Jobs:** cron / Intervall / einmalig, Art Prompt, Skript, HTTP-Aufruf oder Ablauf; der
  Parametersatz eines Jobs wird zum Startkontext des Ablaufs
- **Plugins:** Zip in die Datenbank, Tabellen-CRUD, Fetch-Proxy mit SSRF-Schutz
- **Skills:** versionierte Anleitungen, die Agenten mitbekommen

### Assistent, Mail und Spam

- Persönlicher Assistent über allen Projekten (projektlos), mit eigener Inbox
- Maileingang als Prozess: einordnen → beurteilen → nachfragen → wegräumen oder dem
  Assistenten geben
- **Spam-Erkennung aus drei Stimmen:** Regeln (SPF/DKIM/DMARC, Fassaden-Muster,
  Rollenadressen), ein **lokal** laufendes Modell (nichts Rohes verlässt das Haus) und ein
  lernendes Gedächtnis aus den eigenen Entscheidungen; Rückfrage als Nachricht mit Knöpfen,
  Messung gegen die eigene Trefferquote

### Messreihen

Abläufe schreiben Zahlen mit (Akkustand, Füllstand, Speicherplatz …). Daraus entsteht die
Frage, die man wirklich hat: **wohin läuft das, und wann muss ich handeln?**

- Ausgleichsgerade über ein wählbares Fenster: Änderung pro Tag, Resttage, Datum, Güte
- Vorwarnung X Tage vorher — genau einmal je Auffüllung, nicht täglich
- **Stille-Wächter:** meldet, wenn eine Reihe verstummt — auch dann, wenn die Gegenstelle
  ausgefallen ist und deshalb nicht einmal mehr ihre eigene Störung melden kann
- Plausibilitätsgrenzen (Geräte melden Unsinn, wenn sie etwas nicht wissen), Ansicht mit
  Verlauf, gestrichelter Prognose und einzeln löschbaren Werten

### Benachrichtigungen

- Jeder Mensch verwaltet im Profil seine Wege (Chat, E-Mail) und welcher gilt, wenn der
  Absender keinen nennt — der Normalfall, denn ein Ablauf kennt seinen Empfänger oft erst
  zur Laufzeit
- **Drossel:** „höchstens alle N Minuten dasselbe", mit frei wählbarem Schlüssel. Gedrosselt
  wird die Nachricht, nicht die Verarbeitung
- **Chat-Anbindung:** Meldungen landen im Messenger, Antworten werden zu Kommentaren,
  Freigaben als Knopf; Sprachnachrichten werden **lokal** transkribiert — kein Cloud-Aufruf

### Betrieb

- Deployer mit Zugriff auf den Docker-Socket: Build, Health-Prüfung, Rollback,
  Testumgebungen je Ticket, Self-Deploy-Absicherung
- Admin-Bereich: Nutzer, Kosten, Modellkatalog, Mail, globale Einstellungen
- Start-Dashboard, Prozess-Betriebssicht (was läuft, was hängt), Kostenauswertung

---

## Architektur

| Dienst | Technik | Aufgabe |
|---|---|---|
| `backend` | Python 3.12, asynchron | API, WebSockets, Prozess-Engine, Scheduler, PM-Orchestrator |
| `worker` | Python | Agenten-Tool-Loop in einem eigenen Prozess, über eine Warteschlange gespeist |
| `frontend` | TypeScript, React | Oberfläche samt Ablauf-Editor |
| `db` | PostgreSQL 16 | Datenhaltung |
| `redis` | Redis 7 | Warteschlange, Ereignisverteilung, Schalter |
| `deployer` | Python, Zugriff auf den Docker-Socket | Bauen, Deployen, Rollback, Testumgebungen |
| `chat-bot` | Python | Meldungen und Rückfragen im Messenger |
| `shotter` | Node, Headless-Browser | Screenshots für Agenten |
| `whisper` / `asr-gpu` | Python | lokale Spracherkennung (CPU bzw. GPU) |
| `filmer` | Node | Feierabendfilm des Büros als GIF |

Rund 66 000 Zeilen Code, 877 automatische Tests.

---

## Loslegen

```bash
git clone https://github.com/mcules/Traccoon.git
cd Traccoon
cp .env.example .env        # JWT_SECRET, Postgres-Zugang, Bootstrap-Admin setzen
docker compose up -d --build
```

- Oberfläche: `http://localhost:${FRONTEND_PORT:-8080}`
- API: `http://localhost:${BACKEND_PORT:-8800}/api`
- Beim ersten Start entsteht der Admin aus `BOOTSTRAP_ADMIN_*`.

Ohne weitere Konfiguration ist Traccoon ein vollständiges Ticketsystem. Alles Weitere ist
zuschaltbar:

| Wofür | Was hinterlegen |
|---|---|
| Agentenläufe | Anbieter-Token im **Secret-Tresor** |
| Nachrichten im Chat | Bot-Token in der `.env` (ohne Token schläft die Anbindung) |
| E-Mail | SMTP unter Administration → Mail |
| Werkzeuge für Abläufe | Werkzeug-Server unter Einstellungen → MCP |
| Sprachnachrichten | Container `whisper` (CPU) oder `asr-gpu` |

---

## Sicherheit

- Passwörter mit Argon2id, JWT mit Sitzungs-Ungültigkeit, Freischaltung neuer Konten
- **Secret-Tresor** (Fernet-verschlüsselt) für Tokens und Zugangsdaten; Werte werden nie
  zurückgegeben, nur benutzt
- Rechte serverseitig durchgesetzt — Projektrollen, KI-Recht, granulare Freigaben auf
  Standorte und Geräte
- Agenten laufen hinter einem Berechtigungs-Gate; jeder Werkzeugaufruf ist nachvollziehbar
- Eingehende Webhooks optional mit HMAC; ausgehende Aufrufe nur an hinterlegte Ziele
- Plugins holen fremde Inhalte nur über einen Proxy mit SSRF-Schutz

---

## Stand & Grenzen

- **Keine Releases, keine Migrationsgarantie.** Schemaänderungen laufen im Betrieb additiv
  beim Start (`DEV_CREATE_ALL`), Alembic-Revisionen liegen daneben. Wer produktiv einsteigt,
  sollte Sicherungen fahren.
- **Mehrbenutzerbetrieb ja, Mandantentrennung nein.** Rechte greifen je Projekt und
  Eigentümer; ein Admin sieht alles.
- **Die Oberfläche ist auf Deutsch.** Eine Übersetzung gibt es nicht.
- Der Agentenpfad braucht ein deploybares Projekt mit `compose.preview.yml`, damit
  Testumgebungen und Abnahme vollständig durchlaufen.
- Modellpreise und -kennungen im Katalog sind Vorgaben und per Oberfläche anpassbar.

## Mitmachen

Fehlerberichte und Anregungen gern als Issue. Der Code trägt seine Begründungen im
Kommentar: wer etwas ändert, sollte dort auch erklären, *warum* — das ist hier Hausstil und
nicht Zierde.

Eine Lizenz ist noch nicht gewählt; bis dahin gelten alle Rechte als vorbehalten.
