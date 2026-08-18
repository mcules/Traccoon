# Traccoon

**A ticket system where AI agents do the work, but only when a human calls them in.**

Traccoon is a self-hosted project and ticket system (kanban, sprints, hardware inventory)
with two additions that set it apart:

1. **Agents work on the ticket.** An agent plans, writes code in its own git worktree,
   checks the build and hands the result over for review. Nothing starts without an
   explicit assignment by a person.
2. **Everything that runs is a graph.** The AI lifecycle, procurement, mail intake: flows
   are drawn, not programmed. Connecting a foreign system is a matter of clicking it
   together.

The code carries its reasons in the comments. If you change something, explain why, not
what.

> **Status:** in production here, but there are no releases, no upgrade guarantees and no
> tenant isolation. You should be comfortable with Docker and PostgreSQL. See
> [Status and limits](#status-and-limits).

## The core rule

An agent touches a ticket **only on explicit assignment**. Assigning requires the AI
permission `ai_assign` in that project, a separate permission next to the role. Without it
the entire AI surface stays hidden and Traccoon is a plain ticket system.

Assignment usually goes to the **project manager agent**, which decides staffing and
splitting and delegates to execution agents. Two points always stay with a human: plan
approval and final review.

## What it does

### Projects, tickets, hardware

- Nested projects with inherited membership, roles (owner, maintainer, member, viewer) and
  the `ai_assign` permission
- Configurable issue types and states, kanban board with stable ordering, sprints, saved
  filters, tags, links, attachments
- Comments with visibility (public, internal, agent). A comment can advance a waiting flow
- **Artifacts** as a shared model: tickets, hardware items and your own types share fields,
  states and processes. Projects extend their artifacts without code
- **Hardware:** catalog, item, location tree, procurement chain with handover to people,
  fine grained grants per location or device

### AI agents

- Own tool loop instead of a third party agent framework. It talks to subscription accounts
  as well as self-hosted models over chat compatible HTTP endpoints, with provider routing,
  cooldown and fallback
- Tools: read, write and edit files, run the build check, deploy, take screenshots, ask a
  human, submit a plan, continue later
- **Runtime permission gate** (allow, ask, deny) with one-shot grants. Approval comes from
  the UI or from a message
- **One git worktree per ticket**, pre-merge check, conflicts go back to the agent, build
  gate before review, test environment per ticket
- Cost tracking per run, runaway brakes, night and after-hours windows, wall clock limit,
  continuation after an iteration or time limit with a proper handover instead of memory
  loss
- **PM chat:** talk to the project manager, which turns the conversation into tickets
- **Office:** a pixel view of all running agents plus an end-of-day film as a GIF

### Process engine

Every flow is a directed graph, drawn in the browser:

| Building block | What it does |
|---|---|
| Start | manual, an event inside Traccoon, or an incoming webhook |
| Task, approval | a person does something or approves it (with form fields) |
| Decision | conditions over the run context (JSONLogic, with field picker in the editor) |
| Action | set a state, create a ticket, comment, call a destination, **call a tool**, record a measurement, notify |
| AI agent | an agent run as a step inside the flow |
| Wait for event, wait for time | a comment, a reply, or simply the clock |
| For each | walk a list item by item |
| Other flow | start a second flow as a subprocess |

On top of that:

- **Templates:** four ready-made flows as a starting point (incoming report, scheduled check
  with approval, work through a list, call with retry)
- **Describe instead of build:** one sentence is enough, a model draws the graph. The draft
  lands on the canvas, saving stays manual
- **Dry run:** the flow runs end to end, every action only reports what it would do
- **Step log per run:** what each step returned and which branch it took
- **Expressions** `{{ path | filter:argument }}` with 19 filters (shorten, round, format
  dates, fall back). Unquoted filter arguments may themselves be context paths
- **Process sets:** one shipped default plus personal and project copies (copy on write,
  resettable) for the named flows: AI lifecycle, review, procurement, ticket intake, mail
  intake
- Anyone may create free-standing flows. They act only on artifacts the owner has rights to
- Retries with delay, an error outlet per action, nesting and step limits

### Connecting systems without code

- **Inbound:** webhooks with a GUID address, optional HMAC, filters on a header **or** on
  the payload (`payload:event.type`), idempotency over an arbitrarily deep field, modes for
  ticket, message, event and starting a flow
- **Outbound:** **destinations** hold base URL and authentication (basic, bearer, api key,
  HMAC, OAuth2 client credentials) in one place, callable from flows, jobs and, once
  granted, from agents
- **Tool servers (MCP):** own registry, self service per user. Every registered tool shows
  up in the flow editor as an action
- **Jobs:** cron, interval or one-shot, running a prompt, a script, an HTTP call or a flow.
  A job's parameter set becomes the flow's start context
- **Plugins:** a zip in the database, table CRUD, fetch proxy with SSRF protection
- **Skills:** versioned instructions that agents receive

### Assistant, mail and spam

- Personal assistant above all projects, with its own inbox
- Mail intake as a process: classify, judge, ask back, file away or hand to the assistant
- **Spam detection from three voices:** rules (SPF, DKIM, DMARC, facade patterns, role
  addresses), a **local** model so nothing raw leaves the house, and a memory that learns
  from your own decisions. Questions come back as a message with buttons, and the result is
  measured against your own hit rate

### Measurement series

Flows record numbers (battery level, fill level, disk space). That answers the question you
actually have: where is this heading, and when do I have to act?

- Least squares line over a selectable window: change per day, days left, date, quality
- Early warning N days ahead, exactly once per refill instead of daily
- **Silence watchdog:** reports when a series goes quiet, including the case where the far
  side is down and can no longer report its own failure
- Plausibility bounds, because devices report nonsense when they do not know a value. The
  view shows the history, a dashed forecast and lets you drop single outliers

### Notifications

- Everyone manages their own channels in the profile (chat, email) and which one applies
  when the sender names none. That is the normal case: a flow often learns its recipient
  only at runtime
- **Throttle:** at most one message per key per N minutes. The message is throttled, the
  processing is not
- **Chat integration:** notifications land in the messenger, replies become comments,
  approvals are buttons, voice messages are transcribed **locally**

### Operations

- Deployer with access to the docker socket: build, health check, rollback, test
  environments per ticket, self-deploy safeguards
- Admin area: users, costs, model catalog, mail, global settings
- Start dashboard, process operations view (what runs, what is stuck), cost reports

## Architecture

| Service | Technology | Role |
|---|---|---|
| `backend` | Python 3.12, async | API, WebSockets, process engine, scheduler, PM orchestrator |
| `worker` | Python | agent tool loop in its own process, fed through a queue |
| `frontend` | TypeScript, React | UI including the flow editor |
| `db` | PostgreSQL 16 | storage |
| `redis` | Redis 7 | queue, event fan-out, flags |
| `deployer` | Python, docker socket | build, deploy, rollback, test environments |
| `chat-bot` | Python | notifications and questions in the messenger |
| `shotter` | Node, headless browser | screenshots for agents |
| `whisper`, `asr-gpu` | Python | local speech recognition (CPU or GPU) |
| `filmer` | Node | end-of-day office film as a GIF |

About 81,000 lines of code and 877 automated tests.

## Getting started

```bash
git clone https://github.com/mcules/Traccoon.git
cd Traccoon
cp .env.example .env        # set JWT_SECRET, database access, bootstrap admin
docker compose up -d --build
```

- UI: `http://localhost:${FRONTEND_PORT:-8080}`
- API: `http://localhost:${BACKEND_PORT:-8800}/api`
- The first start creates the admin from `BOOTSTRAP_ADMIN_*`.

Out of the box Traccoon is a complete ticket system. Everything else is opt-in:

| For | What to configure |
|---|---|
| Agent runs | provider token in the **secret vault** |
| Chat notifications | bot token in `.env` (without it the integration stays asleep) |
| Email | SMTP under Administration, Mail |
| Tools in flows | tool servers under Settings, MCP |
| Voice messages | container `whisper` (CPU) or `asr-gpu` |

## Security

- Argon2id password hashing, JWT with session invalidation, manual account activation
- **Secret vault** (Fernet encrypted) for tokens and credentials. Values are never returned,
  only used
- Permissions are enforced server side: project roles, the AI permission, grants on
  locations and devices
- Agents run behind a permission gate, every tool call is traceable
- Inbound webhooks support HMAC, outbound calls go only to configured destinations
- Plugins fetch foreign content through a proxy with SSRF protection

## Status and limits

- **No releases, no migration guarantee.** Schema changes are applied additively at startup
  (`DEV_CREATE_ALL`), with Alembic revisions kept alongside. Take backups before you run
  this on anything you care about.
- **Multi-user yes, tenant isolation no.** Permissions apply per project and owner, an admin
  sees everything.
- **The UI is German.** There is no translation.
- The agent path needs a deployable project with `compose.preview.yml` for test
  environments and review to run end to end.
- Model prices and identifiers in the catalog are defaults and editable in the UI.

## Contributing

Bug reports and ideas are welcome as issues. When you change something, explain the reason
in the comment. That is the house style here, not decoration.

## License

MIT, see [LICENSE](LICENSE).
