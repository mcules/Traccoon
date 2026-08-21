"""Webhooks der alten Modi einmalig auf Abläufe umstellen.

Ein Webhook konnte einmal selbst ein Ticket anlegen (`task`), eine Nachricht schicken
(`notify`) oder den Assistenten beauftragen (`assistant`). Jeder dieser Wege hatte eigene
Spalten am Webhook — `agent`, `prompt_tmpl`, `auto_run`, `title_template`, `notify_chat` —
und war nur dort zu haben: Wer dasselbe aus einem Job oder einem Ereignis heraus wollte,
schaute in die Röhre.

Dieselbe Arbeit machen heute Knoten, die in jedem Ablauf stehen können. Übrig bleibt für den
Webhook, was ein Auslöser wirklich ist: entgegennehmen, prüfen, weitergeben — als Ablauf
(`workflow`) oder als Ereignis (`event`).

Hier steht der Übergang. Er läuft beim Start, ist idempotent (umgestellte Webhooks tragen den
neuen Modus und werden nicht wieder angefasst) und verliert nichts: Der Prompt wird zum
Auftragstext, die Vorlagen werden zu Titel und Text, `auto_run` wird zum Freigabe-Schalter.
Der öffentliche Pfad (`public_id`) bleibt, außen merkt niemand etwas.
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
    """`{feld}` der alten Vorlagen wird `{{ feld }}` der Ablauf-Sprache."""
    return re.sub(r"\{([A-Za-z0-9_.]+)\}", r"{{ \1 }}", text or "")


def _own_title(sub: WebhookSub) -> str:
    """Der Titel, sofern er einer ist.

    „{title}“ ist die unangetastete Voreinstellung: Der Assistenten-Weg hat sie nie benutzt
    (er nahm den Betreff der Mail), und die Nutzlast eines Melders hat selten ein Feld
    `title`. Übernähme man sie, stünde der Platzhalter am Ende als Text in der Überschrift.
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
    """Ob dieser Assistenten-Webhook der Mail-Eingang ist.

    `classify_agent` gab es nur für ihn (lokale Vorklassifizierung vor dem Assistenten); der
    Name der Route ist der zweite Anhaltspunkt für Postfächer, die ohne sie eingerichtet
    wurden. Ein Treffer heißt: Der Webhook meldet künftig `mail.received`, und der
    Mail-Eingangs-Ablauf hört darauf — wie bisher, nur ohne Sonderweg im Code.
    """
    return bool(sub.classify_agent) or bool(re.search(r"mail|email", sub.route or "", re.I))


async def _recipient(db: AsyncSession, sub: WebhookSub) -> int | None:
    """Wer die Nachricht bekommt: der Mensch hinter dem Chat, sonst der Besitzer."""
    if sub.notify_chat:
        who = (await db.execute(select(User).where(
            User.telegram_chat_id == sub.notify_chat))).scalars().first()
        if who is not None:
            return who.id
        log.warning("Webhook %s: Chat %s gehört zu niemandem — die Nachricht geht künftig "
                    "an den Besitzer", sub.route, sub.notify_chat)
    return sub.owner_user_id


def _itemname(route: str) -> str:
    """Aus einer Route ein Name für die Sache: `ha-battery-low` → „Ha battery low“.

    Was der Ablauf tut, weiß nur der Mensch — aber wie er heißt, soll wenigstens nicht vom
    Auslöser handeln. „Webhook: ha-battery-low“ beschreibt den Briefkasten, nicht den Brief;
    umbenennen kann man ihn danach in der Prozessliste.
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
    """Stellt alles um, was noch einen alten Modus trägt. Gibt die Anzahl zurück."""
    subs = (await db.execute(select(WebhookSub).where(
        WebhookSub.mode.in_(OLD_MODI)))).scalars().all()
    if not subs:
        return 0
    for sub in subs:
        if sub.mode == "assistant" and _is_mail(sub):
            # Der Mail-Eingang war nie Sache des Webhooks: Er meldet, dass eine Mail da ist,
            # und wer darauf hört, entscheidet der Ablauf. Was die Schritte über den Eingang
            # wissen müssen, stand vorher fest im Code und steht jetzt im Kontext.
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
                # `auto_run` hieß „ohne Rückfrage laufen“ — der Schalter am Knoten fragt
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
        # Der Ablauf gehört in das Projekt, in dem er anlegt. Sonst wäre das Zielprojekt für
        # ihn ein fremdes, und `create_ticket` verlangte vom Besitzer eine Mitgliedschaft,
        # die der Webhook nie gebraucht hat.
        await _as_flow(db, sub, "webhook-ticket", graph, project_id=sub.project_id)

    await db.commit()
    return len(subs)
