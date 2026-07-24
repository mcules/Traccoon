"""Auto-Action-Handler für auto_action-Knoten der Workflow-Engine.

Ein auto_action-Knoten trägt in `config` einen `action`-Typ + Parameter. `run_action`
führt den Seiteneffekt aus und gibt ein Ergebnis-dict zurück (wird im StepRun.result
persistiert). Bewusst nebenwirkungsarm & offline: keine externen HTTP-Calls in v1.

Unterstützte Aktionen:
  set_context         {set:{key:val,...}}     — Variablen in instance.context schreiben
  set_purchase_status {status}                — purchase_status des gebundenen HW-Exemplars setzen
  set_board_status    {status|category}       — Board-Spalte des gebundenen Tickets setzen
  create_ticket       {summary, ...}          — neues Ticket anlegen (analog Inbound-Webhook)
  webhook             {url, method, headers, payload, secret} — ausgehender HTTP-Aufruf
  comment             {text}                  — System-Kommentar am gebundenen Issue
  notify              {to:{mode,...}, title, text} — In-App/Telegram-Benachrichtigung
  noop                (Default)               — nichts (Platzhalter)

Aktionen unterstützen beide Config-Formen (Editor verschachtelt {action:{action,params}} und
flach {action:"name",...}) via _normalize_action. Text-/Wert-Felder: {{var.pfad}}-Templating.

Text-/Wert-Felder unterstützen einfaches {{var.pfad}}-Templating aus dem Kontext.
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

    if action == "set_purchase_status":
        # Setzt den purchase_status des gebundenen Hardware-Exemplars (Etappe 4:
        # der Workflow ist Quelle, purchase_status abgeleitet). Optional passende Datumsfelder.
        status_val = _interp(params.get("status") or params.get("value") or "", ctx)
        applied = False
        if inst.hardware_asset_id and status_val:
            import datetime as _dt
            from ..models.enums import PurchaseStatus
            from ..models.hardware import HardwareAsset
            try:
                ps = PurchaseStatus(status_val)
            except ValueError:
                raise ValueError(f"Unbekannter purchase_status '{status_val}'")
            asset = await db.get(HardwareAsset, inst.hardware_asset_id)
            if asset is not None:
                asset.purchase_status = ps
                now = _dt.datetime.now(tz=_dt.timezone.utc)
                if ps == PurchaseStatus.ordered and asset.order_date is None:
                    asset.order_date = now
                elif ps == PurchaseStatus.delivered and asset.delivery_date is None:
                    asset.delivery_date = now
                elif ps == PurchaseStatus.installed and asset.install_date is None:
                    asset.install_date = now
                applied = True
        return {"action": "set_purchase_status", "status": status_val, "applied": applied}

    if action == "create_ticket":
        return await _create_ticket(db, inst, params, ctx)

    if action == "set_board_status":
        return await _set_board_status(db, inst, params, ctx)

    if action == "webhook":
        return await _webhook(db, inst, params, ctx)

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
