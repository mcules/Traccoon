# Traccoon

Vereintes **Agenten-Ticketsystem**: ein vollwertiges Jira-artiges Projekt-/Ticketsystem
(inkl. Hardware-Beschaffung) **plus** darübergelegte Multi-Agent-Entwicklung. Vereint die
Konzepte von DevTeam Agents, Nexus und dem früheren Traccoon-Test-Stack.

Bauvorlage / Spezifikation: Obsidian-Vault `02 Projekte/Traccoon/Pflichtenheft 2026-07-17.md`.

## Kernprinzip

Ein Agent bearbeitet ein Ticket **nur bei expliziter Zuweisung**. Zuweisen darf nur ein
Nutzer mit dem KI-Recht `ai_assign` (pro Projekt-Mitgliedschaft, orthogonal zur Rolle).
Ohne dieses Recht ist die gesamte KI-Oberfläche unsichtbar — das Projekt ist ein reines
Ticketsystem. Die Zuweisung geht i. d. R. an den **PM**, der Besetzung + Split entscheidet
und an Ausführungs-Agenten delegiert. Menschenhoheit bleibt bei Plan-Freigabe und Abnahme.

## Stack

| Dienst | Technik |
|---|---|
| backend | Python 3.12 · FastAPI · SQLAlchemy-async · asyncpg |
| db | PostgreSQL 16 |
| redis | Redis 7 (Queue/PubSub/Flags) |
| runner | Node 22 (Redis-Consumer; Mock-Modus, Claude/Codex-CLI-Pfad vorgesehen) |
| frontend | React 18 · Vite · TypeScript · Tailwind · TanStack Query (nginx) |

## Starten

```bash
cp .env.example .env          # Secrets setzen (JWT_SECRET etc.)
docker compose up -d --build
```

- Frontend: `http://localhost:${FRONTEND_PORT:-8080}` (aktuell 8087)
- Backend-API: `http://localhost:${BACKEND_PORT:-8800}/api`
- Erststart legt Bootstrap-Admin aus `.env` an (`BOOTSTRAP_ADMIN_*`).

## Container (voller Stack)

`backend` (FastAPI: API+WS+Dispatcher+Scheduler+Event-Bridge+PM-Orchestrator) · `worker`
(Python-Tool-Loop, `python -m app.worker`) · `deployer` (docker.sock, Build/Health/Rollback +
Preview-Server :8661) · `shotter` (Playwright :8700) · `telegram-bot` (aiogram) · `db` (PG16) ·
`redis` · `frontend` (nginx).

## Funktionsumfang

- **Auth/Multi-User:** Argon2id, JWT, Session-Invalidierung, Freischaltung, **Secret-Tresor** (Fernet).
- **Rollen + KI-Recht:** owner/maintainer/member/viewer + `ai_assign` (serverseitig durchgesetzt).
- **Tickets/Board:** hierarchische Projekte, konfigurierbare Typen/Status, Kanban, Sprints,
  Kommentare (agent/internal, Kommentar-Trigger), Tags, Board-Move, LexoRank.
- **Hardware:** Katalog → Exemplar → Lagerort-Baum, Beschaffungs-Workflow mit Personen-Übergabe.
- **Echtes Agenten-Gehirn:** eigener Python-Tool-Loop (Nexus-Stil) gegen Anthropic-OAuth + Codex,
  Provider-Router (Cooldown/Circuit-Breaker), Tools fs_read/write/edit/check/deploy/screenshot/
  ask_human/submit_plan/continue_later, **Permission-Laufzeit-Gate** (allow/ask/deny, permreq/grant),
  **Git-Worktree-Engine** (prepare/commit/accept, Pre-Merge-Gate, Konflikt-an-Agent), Build-Gate,
  Continuation/Stall-Erkennung, Kosten-Tracking. Dispatcher verarbeitet **nur zugewiesene** Tickets.
- **PM-Chat:** WS-Orchestrierung (`<tickets>`-Ops → legt Tickets an + delegiert), Menschenhoheit bei Freigabe/Abnahme.
- **Deployer:** Auto-Deploy bei Abnahme, zweigleisig (Self-Deploy + Rollback / generisch), check-Build, Testenvs pro Ticket.
- **Nexus:** Webhooks (HMAC, task/notify, Idempotenz), Job-Scheduler (cron/interval/once, prompt/script, /digest),
  **Telegram-Bot** (Notifier, Reply→Kommentar, Buttons), In-App-Notifications, **Skills** (versioniert),
  **MCP-Registry**, **Plugin-System** (Zip-in-DB, Table-CRUD, SSRF-Fetch-Proxy).
- **Frontend:** Board, Ticket-Drawer (Lifecycle), PM-Chat, Secret-Tresor, Notifications-Glocke, Admin (Nutzer/Kosten/Nexus), Hardware.
- **Alembic:** Baseline-Migration vorhanden; Dev nutzt `DEV_CREATE_ALL=true` (create_all).

## Echte Läufe aktivieren

Standardmäßig laufen die Agenten erst, wenn ein Token hinterlegt ist:
**Einstellungen → Secret-Tresor → Claude-Token** (`claude setup-token`). Danach: Ticket einem Agenten
zuweisen (bzw. an den PM) → Planung → Freigabe → Ausführung → Abnahme. Ohne Token scheitern Läufe
sauber mit „kein Setup-Token".

## Bekannte Grenzen

- Volle E2E-Verifikation der Agenten-Codeausführung (Provider→Git-Worktree→Deploy) braucht einen gültigen
  Token + ein deploybares Projekt mit `compose.preview.yml`; die Infrastruktur ist verdrahtet und einzeln verifiziert.
- Telegram-Bot ist ohne `TELEGRAM_BOT_TOKEN` im Ruhemodus (stabil), aktiviert sich mit Token.
- Modellpreise/-IDs im Katalog sind Defaults (per UI/`/providers` anpassbar).
