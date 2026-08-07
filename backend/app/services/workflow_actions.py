"""Auto-Action-Handler für auto_action-Knoten der Workflow-Engine.

Ein auto_action-Knoten trägt in `config` einen `action`-Typ + Parameter. `run_action`
führt den Seiteneffekt aus und gibt ein Ergebnis-dict zurück (wird im StepRun.result
persistiert). Nach außen wirkt nur, was ausdrücklich dafür da ist: `http_request` und
`webhook` rufen fremde Systeme, alles andere bleibt in Traccoon.

Unterstützte Aktionen:
  set_context         {set:{key:val,...}}     — Variablen in instance.context schreiben
  set_status          {status, reason?, notify?} — Zustand des gebundenen Artefakts setzen
                                                (Ticket: + Board-Spalte, Meldung, Ereignis)
  set_field           {field, values, mode?}    — freies Feld des Artefakts setzen
                                                (mode: set | add | remove)
  set_board_status    {status|category}       — Board-Spalte des gebundenen Tickets setzen
  create_ticket       {summary, ...}          — neues Ticket anlegen (analog Inbound-Webhook)
  http_request        {destination, method, path, query, headers, body} — Aufruf über ein Ziel
  webhook             {url, method, headers, payload, secret} — ausgehender Aufruf an freie URL
  comment             {text}                  — System-Kommentar am gebundenen Issue
  notify              {to:{mode,...}, title, text} — In-App/Telegram-Benachrichtigung
  noop                (Default)               — nichts (Platzhalter)

Ticket-Lebenszyklus (Etappe 5 — der Graph ist die Wahrheit, `agent_status` die Projektion):
  refresh_facts       {}                      — Projekt-/Ticket-Fakten in den Kontext (für Guards)
  assign_agent        {agent}                 — Agenten zuweisen (setzt assigned_by/at)
  set_cap_baseline    {}                      — Cap-Fenster auf „ab jetzt" setzen
  start_testenv       {}                      — Testumgebung des Tickets starten
  stop_testenv        {}                      — Testumgebung abräumen (vor dem Merge!)
  accept_merge        {timeout_sec?}          — Branch mergen/PR öffnen (asynchron, Worker)
  deploy              {force?}                — Deployment einreihen
  split_tickets       {}                      — <subtickets> aus dem Plan als Kinder anlegen
  stop_agent          {}                      — laufenden Agentenlauf abbrechen

Aktionen unterstützen beide Config-Formen (Editor verschachtelt {action:{action,params}} und
flach {action:"name",...}) via _normalize_action. Text-/Wert-Felder unterstützen einfaches
{{var.pfad}}-Templating aus dem Kontext.
"""
from __future__ import annotations

import os
import re

from ..models.workflow import WorkflowInstance

_VAR_RE = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


def _config(node: dict) -> dict:
    data = node.get("data") or {}
    cfg = data.get("config")
    if isinstance(cfg, dict):
        return cfg
    return node.get("config") if isinstance(node.get("config"), dict) else {}


def _dig(data, path: str):
    cur = data
    for part in str(path).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _interp(value, ctx: dict):
    """Ersetzt {{pfad}} in Strings durch Kontext-Werte. Nicht-Strings bleiben unverändert."""
    if not isinstance(value, str):
        return value
    def repl(m):
        v = _dig(ctx, m.group(1))
        return "" if v is None else str(v)
    return _VAR_RE.sub(repl, value)


def _normalize_action(cfg: dict) -> tuple[str, dict]:
    """Vereinheitlicht beide Config-Formen:
      - verschachtelt (Editor):  {"action": {"action": "name", "params": {...}}}
      - flach (Seed/Handschrift): {"action": "name", <param>: <wert>, ...}
    Liefert (action_name, params)."""
    raw = cfg.get("action")
    if isinstance(raw, dict):
        return (raw.get("action") or raw.get("kind") or "noop"), dict(raw.get("params") or {})
    action = raw or cfg.get("kind") or "noop"
    params = {k: v for k, v in cfg.items() if k not in ("action", "kind", "label")}
    return action, params


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _create_ticket(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Legt ein Ticket an (analog Inbound-Webhook mode=task). Das erzeugte Ticket wird unter
    context[context_key] (Default 'created_ticket') = {id,key} abgelegt, für Folge-Knoten."""
    from sqlalchemy import select

    from ..models.enums import TicketAgentStatus
    from ..models.project import Project
    from ..models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
    from ..models.user import SYSTEM_USER_ID

    pid = _as_int(_interp(params.get("project_id"), ctx)) if params.get("project_id") is not None else None
    pid = pid or inst.project_id
    if pid is None:
        raise ValueError("create_ticket: kein project_id (weder Parameter noch Instanz-Projekt)")
    t = (await db.execute(select(IssueType).where(IssueType.project_id == pid)
                          .order_by(IssueType.order))).scalars().first()
    s = (await db.execute(select(WorkflowStatus).where(WorkflowStatus.project_id == pid)
                          .order_by(WorkflowStatus.order))).scalars().first()
    project = await db.get(Project, pid)
    counter = (await db.execute(select(IssueCounter).where(IssueCounter.project_id == pid)
                                .with_for_update())).scalar_one_or_none()
    if not (t and s and project and counter):
        raise ValueError("create_ticket: Zielprojekt ohne Typ/Status/Zähler")
    counter.last_number += 1
    n = counter.last_number
    reporter = inst.started_by or SYSTEM_USER_ID
    summary = (_interp(params.get("summary") or params.get("summary_tpl") or "", ctx)
               or f"Workflow #{inst.id}")[:500]
    description = _interp(params.get("description") or params.get("body_tpl") or "", ctx)
    issue = Issue(
        project_id=pid, number=n, key=f"{project.key}-{n}"[:50], type_id=t.id, status_id=s.id,
        summary=summary, description=description, reporter_id=reporter, rank=f"{n:08d}",
        source=f"workflow:{inst.id}",
    )
    agent = params.get("assigned_agent") or params.get("agent")
    if agent:
        import datetime as _dt
        issue.assigned_agent = str(agent)
        issue.assigned_by_user_id = reporter
        issue.assigned_at = _dt.datetime.now(tz=_dt.timezone.utc)
        sa = str(params.get("start_agent_status") or "planning")
        try:
            issue.agent_status = TicketAgentStatus(sa)
        except ValueError:
            issue.agent_status = TicketAgentStatus.planning
    db.add(issue)
    await db.flush()
    # Frisches Ticket ist sofort ein Artefakt — nicht erst beim nächsten Abgleich.
    from .artifacts import ensure_for_issue
    await ensure_for_issue(db, issue)
    key_out = params.get("context_key") or "created_ticket"
    inst.context = {**ctx, key_out: {"id": issue.id, "key": issue.key}}
    return {"action": "create_ticket", "issue_id": issue.id, "issue_key": issue.key}


async def _set_board_status(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Setzt die Board-Spalte (status_id) des gebundenen Tickets. Param `status` (Spaltenname)
    oder `category` (todo|in_progress|done)."""
    if inst.issue_id is None:
        return {"action": "set_board_status", "applied": False, "reason": "keine Ticket-Bindung"}
    from sqlalchemy import select

    from ..models.ticket import Issue, WorkflowStatus

    issue = await db.get(Issue, inst.issue_id)
    if issue is None:
        return {"action": "set_board_status", "applied": False, "reason": "Ticket fehlt"}
    name = _interp(params.get("status") or params.get("name") or "", ctx).strip()
    category = str(params.get("category") or "").strip()
    rows = (await db.execute(select(WorkflowStatus).where(WorkflowStatus.project_id == issue.project_id)
                             .order_by(WorkflowStatus.order))).scalars().all()
    target = None
    if name:
        target = next((w for w in rows if (w.name or "").lower() == name.lower()), None)
    if target is None and category:
        target = next((w for w in rows if getattr(w.category, "value", str(w.category)) == category), None)
    if target is None:
        raise ValueError(f"set_board_status: kein Status '{name or category}' im Projekt")
    issue.status_id = target.id
    return {"action": "set_board_status", "status_id": target.id, "status": target.name}


def _interp_deep(value, ctx: dict):
    """Rekursives {{var}}-Templating über Strings in dicts/Listen; Nicht-Strings bleiben."""
    if isinstance(value, str):
        return _interp(value, ctx)
    if isinstance(value, dict):
        return {k: _interp_deep(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_interp_deep(v, ctx) for v in value]
    return value


async def _webhook(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Ausgehender HTTP-Aufruf. Params:
      url (Pflicht), method (GET|POST|PUT|PATCH|DELETE, Default POST), headers {..}, payload {..}/text,
      secret (Tresor-Name → als {{secret}} in url/headers/payload verfügbar, nie geloggt), timeout_sec.
    Definitionen sind von Projekt-Maintainern autorisiert (wie die bestehende Job-/Webhook-Infra).
    """
    import httpx

    from ..worker.secrets import resolve_ref

    # Secret auflösen und NUR fürs Templating verfügbar machen (nicht in Kontext/Ergebnis).
    tctx = dict(ctx)
    sref = params.get("secret") or params.get("secret_ref")
    if sref:
        ref = sref if str(sref).startswith("secret:") else f"secret:{sref}"
        tctx["secret"] = await resolve_ref(db, ref, inst.started_by)

    url = _interp(params.get("url") or "", tctx).strip()
    if not url:
        raise ValueError("webhook: 'url' erforderlich")
    method = str(params.get("method") or "POST").upper()
    timeout = float(params.get("timeout_sec") or 10)
    headers = {k: _interp(str(v), tctx) for k, v in (params.get("headers") or {}).items()}
    payload = params.get("payload")
    if payload is None:
        payload = params.get("body")

    kwargs: dict = {"headers": headers, "timeout": timeout}
    if method not in ("GET", "HEAD", "DELETE") and payload is not None:
        p = _interp_deep(payload, tctx)
        if isinstance(p, (dict, list)):
            kwargs["json"] = p
        else:
            kwargs["content"] = str(p)

    async with httpx.AsyncClient() as client:
        resp = await client.request(method, url, **kwargs)
    body = (resp.text or "")[:500]
    ok = 200 <= resp.status_code < 300
    return {"action": "webhook", "url": url, "method": method,
            "status_code": resp.status_code, "ok": ok, "response": body}


# ── Ticket-Lebenszyklus ──────────────────────────────────────────────────────

# Vor dem Artefakt-Register gab es je Subjekt eine eigene Aktion. Beide sind heute
# `set_status`; die Namen stehen aber noch in veröffentlichten Versionen, und die sind
# unveränderlich (laufende Instanzen hängen daran). Darum: umbiegen statt doppelt pflegen.
_ALT_AKTIONEN = {"set_agent_status", "set_purchase_status"}

# Welcher Agent-Status welche Benachrichtigung auslöst — identisch zur früheren
# Nachbearbeitung im Dispatcher, damit Telegram/Glocke sich unverändert verhalten.
_NOTIFY_ON = {
    "plan_review": ("plan_review", "Plan bereit"),
    "to_test": ("to_test", "bereit zur Abnahme"),
    "failed": ("failed", "fehlgeschlagen"),
    "hold": ("blocked", "blockiert"),
}


async def _issue_of(db, inst: WorkflowInstance):
    if inst.issue_id is None:
        return None
    from ..models.ticket import Issue
    return await db.get(Issue, inst.issue_id)


async def _artefakt_von(db, inst: WorkflowInstance):
    """Die gemeinsame Artefakt-Zeile des Ablaufs — daran hängen die freien Felder.

    Bevorzugt die Bindung an der Instanz; ältere Instanzen (vor der gemeinsamen Identität)
    haben sie noch nicht und werden über Ticket bzw. Exemplar aufgelöst.
    """
    from ..models.artifact import Artifact
    from . import artifacts as art

    if inst.artifact_id:
        return await db.get(Artifact, inst.artifact_id)
    if inst.issue_id:
        issue = await _issue_of(db, inst)
        return await art.ensure_for_issue(db, issue) if issue else None
    if inst.hardware_asset_id:
        from ..models.hardware import HardwareAsset
        asset = await db.get(HardwareAsset, inst.hardware_asset_id)
        return await art.ensure_for_asset(db, asset) if asset else None
    return None


async def _set_field(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Ein freies Feld des gebundenen Artefakts setzen (Administration → Artefakte).

    `mode` entscheidet, was mit vorhandenen Werten geschieht: `set` ersetzt sie, `add`
    ergänzt, `remove` nimmt weg. Bei einem Feld ohne Mehrfachauswahl ist nur `set`
    sinnvoll — die Prüfung im Register lehnt alles andere ohnehin ab.

    Die neuen Werte landen zusätzlich im Kontext unter `fields.<schlüssel>`, damit
    nachgelagerte Bedingungen im selben Durchlauf darauf zugreifen können.
    """
    from . import artifact_fields as af

    key = str(_interp(params.get("field") or params.get("key") or "", ctx)).strip()
    if not key:
        return {"action": "set_field", "applied": False, "reason": "kein Feld angegeben"}
    artefakt = await _artefakt_von(db, inst)
    if artefakt is None:
        return {"action": "set_field", "applied": False, "reason": "kein Artefakt an diesem Ablauf"}

    feld = next((f for f in await af.fields_of(db, artefakt.type_id, artefakt.project_id)
                 if f.key == key), None)
    if feld is None:
        raise ValueError(f"Feld '{key}' gibt es an diesem Artefakt nicht")

    # Werte dürfen als Liste, als einzelner Wert oder mit Komma getrennt kommen.
    roh = params.get("values", params.get("value"))
    if roh is None:
        neue = []
    elif isinstance(roh, list):
        neue = [_interp(v, ctx) for v in roh]
    else:
        text = str(_interp(roh, ctx))
        neue = [t.strip() for t in text.split(",")] if "," in text else [text]
    neue = [v for v in neue if str(v).strip() != ""]

    modus = str(params.get("mode") or "set").lower()
    if modus in ("add", "remove"):
        vorhanden = [str(v) for v in (await af.values_of(db, artefakt.id)).get(key, [])]
        if modus == "add":
            neue = vorhanden + [str(v) for v in neue if str(v) not in vorhanden]
        else:
            weg = {str(v) for v in neue}
            neue = [v for v in vorhanden if v not in weg]

    gesetzt = await af.set_values(db, artefakt.id, feld, neue)
    inst.context = {**ctx, "fields": {**(ctx.get("fields") or {}), key: gesetzt}}
    return {"action": "set_field", "field": key, "mode": modus, "values": gesetzt}


async def _set_status(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Zustand des gebundenen Artefakts setzen — Ticket, Hardware oder eigener Typ.

    Der EINE Weg im Graphen; welche Werte erlaubt sind, sagt das Artefakt-Register
    (Administration → Artefakte), und der Editor zeigt nur die Zustände des Subjekts, an
    dem der Ablauf hängt. Die Alt-Namen `set_agent_status`/`set_purchase_status` landen
    über `_ALT_AKTIONEN` ebenfalls hier — sie stehen noch in veröffentlichten Versionen.
    """
    from . import artifacts as art
    from ..models.hardware import HardwareAsset

    wert = str(_interp(params.get("status") or params.get("value") or "", ctx)).strip()
    if not wert:
        import logging
        logging.getLogger("workflow_actions").warning(
            "set_status ohne Zustand (Vorlage %r leer) → hold", params.get("status"))
        wert = "hold"
    issue = await _issue_of(db, inst)
    asset = (await db.get(HardwareAsset, inst.hardware_asset_id)
             if inst.hardware_asset_id else None)
    ergebnis = await art.apply_status(
        db, subject_kind=inst.subject_kind, issue=issue, asset=asset, status_key=wert,
        reason=str(_interp(params.get("reason") or params.get("hold_reason") or "", ctx)).strip(),
    )
    # Meldungen bleiben am Ticket hängen (Board/Telegram/Glocke lesen agent_status).
    if issue is not None and params.get("notify", True) and wert in _NOTIFY_ON:
        kind, label = _NOTIFY_ON[wert]
        from .notify import notify_issue
        detail = (ctx.get("agent") or {}).get("summary") or issue.summary
        if wert == "hold" and issue.hold_reason:
            label = f"blockiert ({issue.hold_reason.value})"
        await notify_issue(db, issue, kind, f"{issue.key}: {label}", str(detail)[:400])
        ergebnis["notified"] = True
    if issue is not None:
        from .events import emit
        await emit(db, "issue.done" if wert == "done" else "issue.agent_status_changed",
                   project_id=issue.project_id, issue_id=issue.id,
                   payload={"issue": {
                       "key": issue.key, "agent_status": wert,
                       "hold_reason": issue.hold_reason.value if issue.hold_reason else None}})
    return {"action": "set_status", **ergebnis}


async def _refresh_facts(db, inst: WorkflowInstance, ctx: dict) -> dict:
    """Aktuelle Projekt-/Ticket-Fakten in den Kontext schreiben — Futter für Entscheidungen.

    Ohne diesen Knoten müssten Guards auf Werte prüfen, die beim Instanz-Start eingefroren
    wurden; Einstellungen (Testumgebung, Auto-Deploy, Fortsetzen) sollen aber zum Zeitpunkt
    der Entscheidung gelten.
    """
    from ..models.project import Project
    issue = await _issue_of(db, inst)
    pid = issue.project_id if issue else inst.project_id
    project = await db.get(Project, pid) if pid else None
    facts: dict = {}
    if project is not None:
        facts["project"] = {
            "testenv_enabled": bool(project.testenv_enabled),
            "managed": bool(project.managed),
            "verify_command": bool(project.verify_command),
            "needs_acceptance": bool(project.testenv_enabled or project.managed
                                     or project.verify_command),
            "auto_deploy": bool(project.auto_deploy),
            "auto_continue": bool(project.auto_continue),
            "review_enabled": bool(project.review_enabled),
            "use_pull_request": bool(project.use_pull_request),
            "git_enabled": bool(project.git_enabled),
        }
    if issue is not None:
        facts["issue"] = {
            "has_parent": issue.parent_ticket_id is not None,
            "has_plan": bool(issue.plan),
            "merge_status": issue.merge_status or "",
            "continuation_count": issue.continuation_count,
            "testenv_status": issue.testenv_status or "",
            "assigned_agent": issue.assigned_agent or "",
        }
    inst.context = {**ctx, **facts}
    return {"action": "refresh_facts", "keys": list(facts.keys())}


async def _assign_agent(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    import datetime as _dt
    issue = await _issue_of(db, inst)
    agent = str(_interp(params.get("agent") or params.get("role") or "", ctx)).strip()
    if issue is None or not agent:
        return {"action": "assign_agent", "applied": False}
    issue.assigned_agent = agent
    issue.assigned_by_user_id = issue.assigned_by_user_id or inst.started_by
    issue.assigned_at = _dt.datetime.now(tz=_dt.timezone.utc)
    return {"action": "assign_agent", "agent": agent}


async def _set_cap_baseline(db, inst: WorkflowInstance) -> dict:
    """Cap-Fenster neu setzen: ab hier zählt die Runaway-Bremse nur noch frische Läufe.

    Gehört an jede menschliche Freigabe — alte Fehlversuche (z. B. 429-Abbrüche) dürfen
    legitime Neu-Arbeit nicht sofort in den Cap laufen lassen.

    Aus demselben Grund beginnt hier auch die Fortsetzungs-Zählung von vorn: Planung und
    Umsetzung teilen sich einen Zähler, und eine zähe Planung würde der Umsetzung sonst
    ihr Budget wegessen, bevor sie die erste Zeile geschrieben hat.
    """
    from sqlalchemy import func, select

    from ..models.agents import Run
    issue = await _issue_of(db, inst)
    if issue is None:
        return {"action": "set_cap_baseline", "applied": False}
    issue.cap_baseline_run_id = (
        await db.execute(select(func.max(Run.id)).where(Run.issue_id == issue.id))).scalar()
    issue.continuation_count = 0
    # Die Prüfrunden gehören zum selben Abschnitt: ein neuer Anlauf (Nacharbeit nach
    # Rückfrage, erneute Zuweisung) beginnt mit frischem Korrektur-Budget — sonst stünde ein
    # Ticket, das einmal zwei Runden gebraucht hat, beim nächsten Befund sofort wieder still.
    issue.review_rounds = 0
    inst.context = {**(inst.context or {}), "continuation": 0, "continuation_hint": ""}
    return {"action": "set_cap_baseline", "baseline": issue.cap_baseline_run_id,
            "continuation": 0}


async def _testenv(db, inst: WorkflowInstance, start: bool) -> dict:
    from ..models.project import Project
    from .testenv import start_testenv, stop_testenv
    issue = await _issue_of(db, inst)
    if issue is None:
        return {"action": "testenv", "applied": False, "reason": "keine Ticket-Bindung"}
    project = await db.get(Project, issue.project_id)
    if project is None:
        return {"action": "testenv", "applied": False, "reason": "Projekt fehlt"}
    if start:
        if not project.testenv_enabled:
            return {"action": "start_testenv", "applied": False, "reason": "am Projekt aus"}
        res = await start_testenv(db, issue, project.key)
        return {"action": "start_testenv", "applied": True, "result": res}
    if not issue.testenv_status:
        return {"action": "stop_testenv", "applied": False, "reason": "keine Testumgebung"}
    await stop_testenv(db, issue, project.key)
    return {"action": "stop_testenv", "applied": True}


async def _accept_merge(db, inst: WorkflowInstance, params: dict) -> dict:
    """Abnahme-Merge beim Worker einreihen — asynchron.

    Gibt `_wait` zurück: die Engine parkt den Schritt und schaltet erst weiter, wenn das
    Ergebnis vorliegt (merged|conflict|pr_open|no_git|…). Ohne das würde der Merge die
    Engine-Session minutenlang blockieren.
    """
    import uuid

    from ..core.redis import enqueue_task
    issue = await _issue_of(db, inst)
    if issue is None:
        raise ValueError("accept_merge erfordert ein gebundenes Ticket")
    task_id = f"accept-{issue.key}-{uuid.uuid4().hex[:8]}"
    await enqueue_task({"kind": "accept", "task_id": task_id,
                        "issue_id": issue.id, "project_id": issue.project_id})
    return {"action": "accept_merge",
            "_wait": {"task_id": task_id, "timeout": int(params.get("timeout_sec") or 0),
                      "context_key": "merge"}}


async def _deploy(db, inst: WorkflowInstance, params: dict) -> dict:
    """Deployment einreihen. Ohne `force` nur, wenn das Projekt Auto-Deploy anhat und ein
    echtes Stack-Verzeichnis besitzt (nie das Wartungs-/Host-Projekt selbst — TRA-19)."""
    from ..models.ops import Deployment
    from ..models.project import Project
    issue = await _issue_of(db, inst)
    pid = issue.project_id if issue else inst.project_id
    project = await db.get(Project, pid) if pid else None
    if project is None:
        return {"action": "deploy", "queued": False, "reason": "kein Projekt"}
    if not params.get("force") and not project.auto_deploy:
        return {"action": "deploy", "queued": False, "reason": "Auto-Deploy aus"}
    if not project.workspace_dir:
        return {"action": "deploy", "queued": False, "reason": "Self-/Host-Projekt"}
    db.add(Deployment(project_id=project.id, issue_id=issue.id if issue else None,
                      stack_dir=project.workspace_dir, status="pending", source="workflow"))
    return {"action": "deploy", "queued": True}


async def _split_tickets(db, inst: WorkflowInstance, params: dict) -> dict:
    """Legt die im Plan vorgeschlagenen Teilaufgaben als Kind-Tickets an (Teil 1 startet,
    der Rest ist geparkt). Ausgelöst wird das aus dem Lebenszyklus-Graphen, nachdem ein
    Mensch die Aufteilung freigegeben hat."""
    import datetime as _dt
    import json
    import re

    from sqlalchemy import select

    from ..models.enums import TicketAgentStatus
    from ..models.project import Project
    from ..models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus

    umbrella = await _issue_of(db, inst)
    if umbrella is None:
        raise ValueError("split_tickets erfordert ein gebundenes Ticket")
    m = re.search(r"<subtickets>\s*(\[.*?\])\s*</subtickets>", umbrella.plan or "", re.DOTALL)
    if not m:
        return {"action": "split_tickets", "created": 0, "reason": "kein <subtickets>-Block"}
    subs = json.loads(m.group(1))
    project = await db.get(Project, umbrella.project_id)
    exec_agent = umbrella.exec_agent or project.exec_agent or "developer"
    t = (await db.execute(select(IssueType).where(IssueType.project_id == project.id)
                          .order_by(IssueType.order))).scalars().first()
    s = (await db.execute(select(WorkflowStatus).where(WorkflowStatus.project_id == project.id)
                          .order_by(WorkflowStatus.order))).scalars().first()
    counter = (await db.execute(select(IssueCounter).where(IssueCounter.project_id == project.id)
                                .with_for_update())).scalar_one()
    keys = []
    for order, sub in enumerate(subs):
        counter.last_number += 1
        n = counter.last_number
        child = Issue(
            project_id=project.id, number=n, key=f"{project.key}-{n}", type_id=t.id,
            status_id=s.id, summary=(sub.get("summary") or f"Teil {order + 1}")[:500],
            description=sub.get("description", ""), reporter_id=umbrella.reporter_id,
            rank=f"{n:08d}", parent_ticket_id=umbrella.id, split_order=order,
            plan=sub.get("plan", ""), assigned_agent=exec_agent,
            assigned_by_user_id=umbrella.assigned_by_user_id or umbrella.reporter_id,
            assigned_at=_dt.datetime.now(tz=_dt.timezone.utc),
            # Kind 0 startet sofort, der Rest wartet auf seinen Vorgänger.
            agent_status=(TicketAgentStatus.approved if order == 0 else None),
        )
        db.add(child)
        keys.append(child.key)
    from .artifacts import set_ticket_status
    # Sammelticket wartet auf seine Teile — kein Zustand, Board bleibt stehen.
    await set_ticket_status(db, umbrella, None, board=False)
    from .comments import add_system_comment
    await add_system_comment(
        db, umbrella.id, f"✅ Aufteilung übernommen — {len(keys)} Teilaufgaben angelegt.",
        author_label="Workflow")
    return {"action": "split_tickets", "created": len(keys), "keys": keys}


async def _stop_agent(db, inst: WorkflowInstance) -> dict:
    from ..core.redis import publish_kill
    issue = await _issue_of(db, inst)
    if issue is None:
        return {"action": "stop_agent", "applied": False}
    await publish_kill(issue.key)
    issue.agent_working = False
    return {"action": "stop_agent", "applied": True}


async def _http_request(db, inst: WorkflowInstance, params: dict, ctx: dict) -> dict:
    """Ruft ein hinterlegtes **Ziel** auf (Basis-URL + Anmeldung stecken dort).

    Parameter — alle mit `{{pfad}}`-Templating aus dem Kontext:
      destination  Name des Ziels (Projekt → Nutzer → systemweit aufgelöst)
      method       GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS (Default POST)
      path         Ergänzung der Basis-URL, z. B. "/api/v2/orders"
      query        {schlüssel: wert} → wird an die URL gehängt
      headers      {schlüssel: wert} → zusätzlich zu den Standard-Kopfzeilen des Ziels
      body         dict/Liste (→ JSON) oder Text; bei GET/HEAD/DELETE ignoriert
      context_key  wohin das Ergebnis im Kontext geschrieben wird (Default "http")
      fail_on_error  true → ein 4xx/5xx lässt den Schritt fehlschlagen (Default false:
                     der Prozess entscheidet selbst über `status_code`/`ok`)
    """
    from . import destinations

    name = str(_interp(params.get("destination") or params.get("target") or "", ctx)).strip()
    if not name:
        raise ValueError("http_request: kein Ziel angegeben")
    owner = inst.started_by
    if owner is None and inst.issue_id:
        issue = await _issue_of(db, inst)
        owner = (issue.assigned_by_user_id or issue.reporter_id) if issue else None
    dest = await destinations.resolve(db, name, project_id=inst.project_id, owner_id=owner)
    if dest is None:
        raise ValueError(f"http_request: unbekanntes oder deaktiviertes Ziel '{name}'")

    result = await destinations.call(
        db, dest,
        method=str(_interp(params.get("method") or "POST", ctx)),
        path=str(_interp(params.get("path") or "", ctx)),
        query={k: _interp_deep(v, ctx) for k, v in (params.get("query") or {}).items()},
        headers={k: str(_interp(v, ctx)) for k, v in (params.get("headers") or {}).items()},
        body=_interp_deep(params.get("body", params.get("payload")), ctx),
        timeout=int(params["timeout_sec"]) if params.get("timeout_sec") else None,
    )
    key = str(params.get("context_key") or "http")
    inst.context = {**ctx, key: result}
    if params.get("fail_on_error") and not result["ok"]:
        raise ValueError(f"Ziel '{name}' antwortete mit {result['status_code']}: "
                         f"{result.get('error', '')[:200]}")
    return {"action": "http_request", **{k: result[k] for k in ("destination", "method", "url",
                                                                "status_code", "ok")}}


async def run_action(db, inst: WorkflowInstance, node: dict) -> dict:
    cfg = _config(node)
    action, params = _normalize_action(cfg)
    ctx = dict(inst.context or {})

    if action == "set_context":
        # Editor liefert die Zuweisungen direkt als params ({key:val}); explizites
        # {set:{...}} wird ebenfalls unterstützt.
        raw = params.get("set") if isinstance(params.get("set"), dict) else params
        updates = {k: v for k, v in raw.items() if k != "set"}
        applied = {k: _interp(v, ctx) for k, v in updates.items()}
        # Neues dict zuweisen, damit SQLAlchemy die JSON-Spalte als geändert erkennt.
        inst.context = {**ctx, **applied}
        return {"action": "set_context", "keys": list(applied.keys())}

    if action == "comment":
        text = _interp(params.get("text") or params.get("message") or "", ctx)
        if inst.issue_id and text:
            from .comments import add_system_comment
            await add_system_comment(db, inst.issue_id, text, author_label="Workflow")
        return {"action": "comment", "text": text, "written": bool(inst.issue_id and text)}

    if action == "create_ticket":
        return await _create_ticket(db, inst, params, ctx)

    if action == "set_board_status":
        return await _set_board_status(db, inst, params, ctx)

    if action == "webhook":
        # Ein Ziel im Parametersatz gewinnt: dann ist es ein normaler Ziel-Aufruf.
        if params.get("destination") or params.get("target"):
            return await _http_request(db, inst, params, ctx)
        return await _webhook(db, inst, params, ctx)

    if action == "http_request":
        return await _http_request(db, inst, params, ctx)

    if action in _ALT_AKTIONEN or action == "set_status":
        return await _set_status(db, inst, params, ctx)

    if action == "set_field":
        return await _set_field(db, inst, params, ctx)

    if action == "refresh_facts":
        return await _refresh_facts(db, inst, ctx)

    if action == "assign_agent":
        return await _assign_agent(db, inst, params, ctx)

    if action == "set_cap_baseline":
        return await _set_cap_baseline(db, inst)

    if action == "start_testenv":
        return await _testenv(db, inst, start=True)

    if action == "stop_testenv":
        return await _testenv(db, inst, start=False)

    if action == "accept_merge":
        return await _accept_merge(db, inst, params)

    if action == "deploy":
        return await _deploy(db, inst, params)

    if action == "split_tickets":
        return await _split_tickets(db, inst, params)

    if action == "stop_agent":
        return await _stop_agent(db, inst)

    if action == "notify":
        target = await _resolve_target(db, inst, params.get("to") or {})
        title = _interp(params.get("title") or "Workflow-Benachrichtigung", ctx)
        body = _interp(params.get("text") or params.get("message") or "", ctx)
        from ..models.notification import Notification
        from ..models.user import User
        chat = None
        if target is not None:
            u = await db.get(User, target)
            chat = (u.telegram_chat_id if u else None)
        chat = chat or os.getenv("TELEGRAM_OWNER_CHAT", "") or None
        db.add(Notification(
            user_id=target, project_id=inst.project_id, issue_id=inst.issue_id,
            kind="workflow_notify", title=title[:500], body=body[:4000], chat_id=chat,
        ))
        return {"action": "notify", "user_id": target}

    # Unbekannte/absichtliche noop-Aktion: kein Fehler, damit der Workflow durchläuft.
    return {"action": "noop", "requested": action}


async def _resolve_target(db, inst: WorkflowInstance, to: dict) -> int | None:
    """Zielnutzer einer notify-Aktion (analog Assignee-Auflösung, minimal gehalten)."""
    mode = to.get("mode", "user")
    if mode == "user":
        uid = to.get("user_id")
        return int(uid) if uid is not None else None
    if mode == "reporter" and inst.issue_id:
        from ..models.ticket import Issue
        issue = await db.get(Issue, inst.issue_id)
        return issue.reporter_id if issue else None
    if mode == "context":
        val = _dig(inst.context or {}, to.get("path") or "")
        try:
            return int(val) if val is not None else None
        except (TypeError, ValueError):
            return None
    if mode == "role" and inst.project_id and to.get("role"):
        from ..models.enums import ProjectRole
        from ..models.project import ProjectMember
        from sqlalchemy import select
        try:
            prole = ProjectRole(to["role"])
        except ValueError:
            return None
        m = (
            await db.execute(
                select(ProjectMember)
                .where(ProjectMember.project_id == inst.project_id, ProjectMember.role == prole)
                .order_by(ProjectMember.id)
            )
        ).scalars().first()
        return m.user_id if m else None
    return None
