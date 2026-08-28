"""A proposed answer to a report. Written by a model, sent by a person.

The order of those two is the whole design. Answering a stranger in the name of the house is
not a place for a machine that acts on its own: a wrong sentence in a bug report is a wrong
sentence at a customer, and it cannot be taken back. So this module can do exactly one thing
— return text. It writes no entry, it touches no mailbox, and there is no parameter that
would make it do either. What becomes of the proposal is decided at the same button that
sends a sentence somebody typed themselves.

Es ist eine Runde und kein einzelner Wurf: der Entwurf geht mit den Anmerkungen zurück
("kürzer", "frag nach der Version", "nicht so förmlich"), und was daraus wird, geht wieder
zurück. Deshalb bekommt das Modell beim Überarbeiten den bisherigen Text mit und die
Ansage, alles stehen zu lassen, was niemand beanstandet hat — sonst ist die Formulierung,
die beim letzten Mal gepasst hat, jedes Mal wieder weg.

Which model writes it: the agent named on the project (`answer_agent`), otherwise the one
the mail intake classifies with. It has to run over an API and not over a subscription CLI —
a draft is written while somebody waits in front of the form, and the CLI path is the one for
runs that take minutes.
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from ..models.artifact import Artifact
from ..models.project import Project
from ..models.ticket import Comment, Issue, WorkflowStatus
from ..worker.providers.base import ProviderError
from ..worker.providers.openai import OpenAIProvider
from ..worker.secrets import resolve_provider_base_url, resolve_provider_token
from . import bugs as bugs_svc
from .mail_classify import resolve_classify_from_agent

log = logging.getLogger("report_draft")

# The agent that classifies incoming mail. It is the one local model every installation that
# reads mail already has, which makes it the sensible default for a draft as well.
FALLBACK_AGENT = "mail_classifier"

# How much of a text goes into the prompt. A ticket description with a pasted log, a plan of
# four pages: read in full they push the actual conversation out of the window, and the answer
# is written about the wrong thing. The beginning is the part that says what the matter is.
MAX_TICKET_CHARS = 6000
MAX_COMMENT_CHARS = 2000
MAX_KNOWLEDGE_CHARS = 4000

# Wie viele Anmerkungen mitgehen. Wer zwanzig Runden dreht, hat nicht zwanzig Regeln,
# sondern eine andere Antwort im Kopf - die letzten zehn sagen alles, was noch gilt.
MAX_COMMENTS = 10

SYSTEM = (
    "Du schreibst den Entwurf einer Antwort auf eine Meldung (Fehler, Wunsch oder Frage), "
    "die jemand an uns geschickt hat. Ein Mensch liest deinen Entwurf, ändert ihn und "
    "verschickt ihn — oder verwirft ihn.\n"
    "Regeln:\n"
    "- Antworte in der Sprache der Meldung.\n"
    "- Schreib wie ein Mensch: kurz, klar, freundlich, ohne Werbe- oder Amtsdeutsch, ohne "
    "lange Gedankenstriche.\n"
    "- Erfinde nichts. Was du nicht weißt (ob etwas repariert wurde, wann eine Version "
    "kommt), fragst du nach oder lässt du weg.\n"
    "- Keine Zusagen zu Terminen, Geld oder Verantwortlichen.\n"
    "- Ticketverlauf, interne Notizen und Projektwissen bekommst du, damit du den Stand "
    "kennst. Sie sind Hintergrund für dich, nicht für den Melder: keine Ticketnummern, keine "
    "internen Notizen, keine technischen Interna, die der Melder nicht ohnehin geschrieben "
    "hat.\n"
    "- Nur der Text der Antwort, ohne Betreff, ohne Anrede-Platzhalter wie [Name], ohne "
    "Grußformel mit Unterschrift, ohne Anführungszeichen um das Ganze."
)

# Beim Überarbeiten steht der bisherige Entwurf schon da. Dann ist die Aufgabe eine andere:
# nicht neu schreiben, sondern ändern, was beanstandet wurde - und den Rest in Ruhe lassen.
# Ein Modell, dem man das nicht sagt, schreibt jedes Mal einen neuen Text, und die
# Formulierung, die beim letzten Mal gepasst hat, ist wieder weg.
REVISION = (
    "\nDu überarbeitest einen bestehenden Entwurf. Ändere, was die Anmerkungen verlangen, "
    "und lass alles andere so stehen, wie es ist - auch die Formulierungen, die niemand "
    "beanstandet hat. Widersprechen sich zwei Anmerkungen, gilt die letzte. Gib den "
    "vollständigen überarbeiteten Text zurück, keine Liste der Änderungen."
)


class NoDraftAgent(Exception):
    """No model available for a draft — with the reason in plain words."""


async def draft_answer(db: AsyncSession, artifact: Artifact, *, owner_id: int | None,
                       draft: str = "", comments: list[str] | None = None) -> tuple[str, str]:
    """Ein Vorschlag und der Agent, der ihn geschrieben hat. Schickt nichts, legt nichts ab.

    Mit `draft` wird überarbeitet statt neu geschrieben: der Text steht schon im Feld, und
    die `comments` sagen, was daran anders werden soll.
    """
    comments = [c.strip() for c in (comments or []) if c and c.strip()][-MAX_COMMENTS:]
    project = await db.get(Project, artifact.project_id) if artifact.project_id else None
    role = (project.answer_agent if project is not None and project.answer_agent
            else FALLBACK_AGENT)

    cfg = await resolve_classify_from_agent(db, owner_id, role)
    if cfg is None:
        raise NoDraftAgent(f"There is no agent '{role}'")
    provider, model, token_name = cfg
    if provider != "openai":
        # A subscription CLI writes drafts too, but as a run that takes minutes. Saying so is
        # better than a form that hangs and then reports a timeout.
        raise NoDraftAgent(
            f"The agent '{role}' runs over {provider} — a draft needs an agent over an API")
    token = await resolve_provider_token(db, owner_id, provider, token_name)
    base_url = await resolve_provider_base_url(db, owner_id, provider, token_name)
    if not token or not base_url or not model:
        raise NoDraftAgent(f"The agent '{role}' has no model, token or address on file")

    try:
        answer = await OpenAIProvider(base_url=base_url).chat(
            model=model,
            messages=[{"role": "system", "content": SYSTEM + (REVISION if draft.strip() else "")},
                      {"role": "user",
                       "content": await _conversation(db, artifact, draft, comments)}],
            temperature=0.4, max_tokens=1200, auth_token=token,
            # Same reason as with the classification: a thinking model spends the whole output
            # budget on reasoning and hands back an empty text.
            extra_body={"chat_template_kwargs": {"enable_thinking": False}})
    except ProviderError as exc:
        raise NoDraftAgent(f"The agent '{role}' did not answer: {exc}") from exc

    text = (answer.text or "").strip()
    if not text:
        raise NoDraftAgent(f"The agent '{role}' returned nothing")
    return text, role


async def _conversation(db: AsyncSession, artifact: Artifact, draft: str,
                        comments: list[str]) -> str:
    """Everything that is known about this matter, as the model reads it.

    Four things, in the order in which they answer the question "what do I write back":
    what the project is, what was reported, what was said about it, and what became of it as
    work. The last one is the one that usually holds the answer — the report says "the list
    stays empty", the ticket says why and whether it is fixed.

    Internal notes and the ticket travel along on purpose. They are what the house knows, and
    a draft written without them repeats the question that has long been answered. What must
    not happen is that any of it ends up in the answer — that is what the system prompt says,
    and behind it stands a person who reads before sending.
    """
    values = await bugs_svc.values_of_report(db, artifact.id)
    lines: list[str] = []
    knowledge = await _knowledge(db, artifact)
    if knowledge:
        lines.append(knowledge)
    lines += [f"Meldung {artifact.id}: {artifact.title}",
             f"Art: {values.get('kind') or 'bug'}",
             f"Melder: {values.get('contact') or 'unbekannt'}"]
    if values.get("app"):
        lines.append(f"Programm: {values['app']} {values.get('version') or ''}".strip())
    if values.get("environment"):
        lines.append(f"Umgebung: {values['environment']}")
    lines.append(f"\nWas gemeldet wurde:\n{values.get('details') or '(kein Text)'}")

    posts = await bugs_svc.posts_of(db, artifact.id, with_internal=True)
    if posts:
        lines.append("\nBisheriger Verlauf:")
        for post in posts:
            who = "Interne Notiz" if post.internal else (
                "Wir" if post.via == "web" else f"{post.author_label or 'Melder'}")
            lines.append(f"- {who}: {post.body.strip()[:2000]}")
    ticket = await _ticket(db, values.get("ticket") or "")
    if ticket:
        lines.append(ticket)
    # Der bisherige Entwurf und die Anmerkungen dazu stehen ganz unten: sie sind das
    # Jüngste an der Sache und das, worum es in dieser Runde geht.
    if draft.strip():
        lines.append(f"\nBisheriger Entwurf:\n{draft.strip()[:MAX_TICKET_CHARS]}")
    if comments:
        lines.append("\nAnmerkungen dazu, älteste zuerst:")
        lines += [f"{i}. {c[:1000]}" for i, c in enumerate(comments, 1)]
    return "\n".join(lines)


async def _knowledge(db: AsyncSession, artifact: Artifact) -> str:
    """What the project is, as background — the same text the agents are given.

    Marked as background and not as an assignment, for the same reason as there: a
    description ages, and a model that reads it as a task starts answering about the open
    points it finds listed in it.
    """
    project = await db.get(Project, artifact.project_id) if artifact.project_id else None
    if project is None:
        return ""
    parts = [f"Projekt: {project.name} ({project.key})"]
    text = (project.description or "").strip()
    if text:
        parts.append("Hintergrundwissen zum Projekt (keine Aufgabe, kann älter sein als der "
                     "Stand der Dinge):\n" + text[:MAX_KNOWLEDGE_CHARS])
    return "\n".join(parts) + "\n"


async def _ticket(db: AsyncSession, key: str) -> str:
    """The ticket this report became, with its whole history.

    Without it the draft answers out of the report alone — and the report is the oldest thing
    in the matter. Everything that has been found out since stands here: the description as
    somebody wrote it down, the plan, the state, and the comments in the order they were
    written.
    """
    if not key:
        return ""
    issue = (await db.execute(select(Issue).where(Issue.key == key))).scalar_one_or_none()
    if issue is None:
        return ""
    status = await db.get(WorkflowStatus, issue.status_id)
    lines = [f"\nDaraus wurde das Ticket {issue.key}: {issue.summary}",
             f"Stand: {status.name if status is not None else '?'}"
             + (f" · KI: {issue.agent_status.value}" if issue.agent_status else "")]
    if issue.description:
        lines.append(f"\nBeschreibung des Tickets:\n{issue.description[:MAX_TICKET_CHARS]}")
    if issue.plan:
        lines.append(f"\nGeplantes Vorgehen:\n{issue.plan[:MAX_TICKET_CHARS]}")
    comments = (await db.execute(
        select(Comment).where(Comment.issue_id == issue.id).order_by(Comment.id))).scalars().all()
    if comments:
        lines.append("\nVerlauf des Tickets:")
        for one in comments:
            who = one.author_label or ("Agent" if one.kind == "agent" else "Notiz")
            lines.append(f"- {who}: {one.body.strip()[:MAX_COMMENT_CHARS]}")
    return "\n".join(lines)
