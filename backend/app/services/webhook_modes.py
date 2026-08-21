"""Convert webhooks of the old modes to flows, once.

Ein Webhook konnte einmal selbst ein Ticket anlegen (`task`), eine Nachricht schicken
(`notify`) or assign the assistant (`assistant`). Each of these ways had columns of its own on
the webhook — `agent`, `prompt_tmpl`, `auto_run`, `title_template`, `notify_chat` — and was
available only there: whoever wanted the same thing out of a job or an event was out of luck.

The same work is done today by nodes that can stand in any flow. What is left for the webhook
is what a trigger really is: receive, check, pass on — as a flow
(`workflow`) oder als Ereignis (`event`).

Here stands the transition. It runs at startup, is idempotent (converted webhooks carry the
new mode and are not touched again) and loses nothing: the prompt becomes the assignment text,
the templates become title and text, `auto_run` becomes the approval switch. The public path
(`public_id`) stays, nobody outside notices a thing.
"""
from __future__ import annotations

import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.ops import WebhookSub
from ..models.user import User
from . import workflow_templates as templates

log = logging.getLogger("traccoon.webhooks")

OLD_MODI = ("task", "notify", "assistant")


def _tpl(text: str | None) -> str:
    """`{field}` of the old templates becomes `{{ field }}` of the flow language."""
    return re.sub(r"\{([A-Za-z0-9_.]+)\}", r"{{ \1 }}", text or "")


def _own_title(sub: WebhookSub) -> str:
    """The title, provided it is one.

    "{title}" is the untouched default: the assistant path never used it (it took the subject
    of the mail), and the payload of a reporter rarely has a field `title`. Adopting it would
    leave the placeholder standing as text in the heading.
    """
    raw = (sub.title_template or "").strip()
    return "" if raw in ("", "{title}") else _tpl(raw)


def _node(graph: dict, node_id: str) -> dict:
    for n in graph.get("nodes") or []:
        if n.get("id") == node_id:
            return n
    raise KeyError(f"Vorlage ohne Knoten '{node_id}'")


def _set_params(graph: dict, node_id: str, params: dict) -> None:
    cfg = _node(graph, node_id)["data"]["config"]
    cfg["action"]["params"] = params


def _is_mail(sub: WebhookSub) -> bool:
    """Whether this assistant webhook is the mail intake.

    `classify_agent` existed only for it (local pre-classification before the assistant); the
    name of the route is the second clue for mailboxes that were set up without it. A match
    means: the webhook reports `mail.received` from now on, and the mail intake flow listens
    for it — as before, only without a special path in the code.
    """
    return bool(sub.classify_agent) or bool(re.search(r"mail|email", sub.route or "", re.I))


async def _recipient(db: AsyncSession, sub: WebhookSub) -> int | None:
    """Who gets the message: the person behind the chat, otherwise the owner."""
    if sub.notify_chat:
        who = (await db.execute(select(User).where(
            User.telegram_chat_id == sub.notify_chat))).scalars().first()
        if who is not None:
            return who.id
        log.warning("Webhook %s: Chat %s gehört zu niemandem — die Nachricht geht künftig "
                    "an den Besitzer", sub.route, sub.notify_chat)
    return sub.owner_user_id


def _itemname(route: str) -> str:
    """A name for the matter out of a route: `ha-battery-low` → "Ha battery low".

    What the flow does only the person knows — but what it is called should at least not be
    about the trigger. "Webhook: ha-battery-low" describes the letterbox, not the letter;
    renaming it afterwards is possible in the flow list.
    """
    text = (route or "").replace("_", " ").replace("-", " ").strip()
    return text[:1].upper() + text[1:] if text else "Ablauf"


async def _as_flow(db: AsyncSession, sub: WebhookSub, key: str, graph: dict,
                      project_id: int | None = None) -> None:
    name = _itemname(sub.route)
    d = await templates.create(
        db, key, owner_id=sub.owner_user_id,
        def_key=await templates.free_key(db, name, project_id),
        name=name, graph=graph, project_id=project_id)
    await db.flush()
    sub.mode = "workflow"
    sub.workflow_definition_id = d.id
    log.info("Webhook %s (%s) läuft jetzt über den Ablauf %s", sub.route, key, d.key)


async def convert(db: AsyncSession) -> int:
    """Converts everything that still carries an old mode. Returns the count."""
    subs = (await db.execute(select(WebhookSub).where(
        WebhookSub.mode.in_(OLD_MODI)))).scalars().all()
    if not subs:
        return 0
    for sub in subs:
        if sub.mode == "assistant" and _is_mail(sub):
            # The mail intake was never the webhook's business: it reports that a mail is
            # there, and who listens to that the flow decides. What the steps need to know
            # about the intake used to sit in the code and now stands in the context.
            ref = sub.ref_field or "{account}:{uid}"
            sub.ref_field = ref
            sub.context_map = {"mail": ""}
            sub.context_fixed = {
                "intake.source": f"webhook:{sub.route}",
                "intake.source_ref": ref,
                "intake.classify_agent": sub.classify_agent or "",
                "intake.agent": sub.agent or "assistent",
                "intake.prompt_tmpl": sub.prompt_tmpl or "",
                "intake.ref_field": "",
                "intake.auto_run": bool(sub.auto_run),
                "intake.owner_id": sub.owner_user_id,
            }
            sub.mode = "event"
            sub.event_name = "mail.received"
            log.info("Webhook %s meldet jetzt das Ereignis mail.received", sub.route)
            continue

        if sub.mode == "assistant":
            graph = templates.graph("webhook-assistent")
            task = _tpl(sub.prompt_tmpl) or (
                f"{_own_title(sub)}\n\n{_tpl(sub.body_template)}".strip())
            _set_params(graph, "auftrag", {
                "agent": sub.agent or "assistent",
                "titel": _own_title(sub),
                "task": task,
                # `auto_run` meant "run without asking" — the switch on the node asks
                # andersherum, deshalb die Umkehrung.
                "freigabe": not bool(sub.auto_run),
            })
            await _as_flow(db, sub, "webhook-assistent", graph)
            continue

        if sub.mode == "notify":
            graph = templates.graph("webhook-melden")
            _set_params(graph, "melden", {
                "to": {"mode": "user", "user_id": await _recipient(db, sub)},
                "title": _own_title(sub) or sub.route,
                "text": _tpl(sub.body_template),
            })
            await _as_flow(db, sub, "webhook-melden", graph)
            continue

        # mode == "task"
        graph = templates.graph("webhook-ticket")
        params = {"project_id": sub.project_id,
                  "summary": _own_title(sub) or f"Webhook {sub.route}",
                  "description": _tpl(sub.body_template)}
        if sub.agent:
            params["assigned_agent"] = sub.agent
            params["start_agent_status"] = sub.status_new or "planning"
        _set_params(graph, "ticket", params)
        # The flow belongs in the project it creates in. Otherwise the target project would be
        # a foreign one to it, and `create_ticket` would demand a membership from the owner
        # that the webhook never needed.
        await _as_flow(db, sub, "webhook-ticket", graph, project_id=sub.project_id)

    await db.commit()
    return len(subs)
