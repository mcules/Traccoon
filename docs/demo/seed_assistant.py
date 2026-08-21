"""An inbox for the assistant — runs INSIDE the backend container of the demo stack.

The items are what the assistant really produces: mail that was pre-classified locally, a
question from the chat, a free assignment out of a flow. Everything invented.

    docker compose -f docs/demo/compose.yml exec -T backend python - < docs/demo/seed_assistant.py
"""
import asyncio
import datetime as dt

from sqlalchemy import select

from app.db import SessionLocal
from app.models.assistant import AssistantTask
from app.models.user import User

ITEMS = [
    # kind, source, title, category, priority, summary, status, result, minutes ago
    ("email", "Private", "Invoice from the utility for August", "invoice", "normal",
     "An invoice for August over a low three-digit amount, direct debit at the start of next "
     "month. Nothing to do beyond filing it.",
     "done", "Filed in Paperless under \"Stadtwerke / 2026\" and noted in the vault: "
     "direct debit on 01.09.", 12),
    ("email", "Private", "Workshop appointment confirmed", "appointment", "normal",
     "A workshop confirms an appointment in two weeks, 09:00, and asks for the vehicle "
     "registration to be brought along.",
     "done", "Appointment created in the calendar including a reminder the evening before, "
     "with the note about the registration.", 95),
    ("email", "Private", "Minutes of the board meeting", "club", "high",
     "The minutes of a board meeting; one item is waiting for an answer from your person.",
     "new", "", 240),
    ("chat", "telegram", "What is coming up this week?", "question", "normal",
     "A question from the chat about appointments and open items of the week.",
     "done", "Three appointments, two open questions from mail, one deadline on Friday — "
     "answered in the chat.", 35),
    ("task", "flow: weekly review", "Write the weekly review", "report", "normal",
     "A free assignment out of a flow: summarise the week and put it into the store.",
     "done", "Review written and stored in \"weekly.review\"; the link went out as a message.",
     420),
    ("email", "Private", "Alleged account suspension", "phishing", "high",
     "A mail claims an account was blocked and asks for a login within 24 hours. The sender "
     "domain does not belong to the bank.",
     "done", "Recognised as phishing, moved to the spam folder, sender noted.", 28),
]


async def main() -> None:
    async with SessionLocal() as db:
        ada = (await db.execute(select(User).where(User.username == "ada"))).scalars().first()
        now = dt.datetime.now(dt.timezone.utc)
        for kind, source, title, category, priority, summary, status, result, ago in ITEMS:
            when = now - dt.timedelta(minutes=ago)
            db.add(AssistantTask(
                owner_user_id=ada.id, kind=kind, source=source, title=title,
                category=category, priority=priority, redacted_summary=summary,
                meta={}, status=status, result=result, error="",
                finished_at=when if status == "done" else None,
                created_at=when, updated_at=when, notified=True))
        await db.commit()
    print(f"{len(ITEMS)} assistant items")


asyncio.run(main())
