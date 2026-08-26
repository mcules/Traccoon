"""A standing rule can say no, and taking it back must not need a developer.

The occasion: one tap on "always this sender" in the chat card created a permanent approval
for a sender that was never to be approved. Nothing in the product could take it back — the
list showed rules, but there was no way to say "never" and no way from the card back out.
Both directions are pinned here, because a permission that only grows is the failure mode
this feature exists to prevent.
"""
from app.models.assistant import AssistantPolicy
from app.services.assistant_policy import match_policy, revoke_policy, upsert_policy

from conftest import make_user


async def _owner(db):
    return await make_user(db, "owner")


async def test_a_block_beats_an_approval_for_the_same_address(db):
    u = await _owner(db)
    await upsert_policy(db, u.id, match_kind="sender", match_value="post@example.org")
    await upsert_policy(db, u.id, match_kind="sender", match_value="post@example.org",
                        blocked=True)
    await db.commit()

    hit = await match_policy(db, u.id, sender_email="post@example.org", domain="example.org",
                             category="")
    assert hit is not None and hit.blocked
    assert hit.auto_approve is False, "one row cannot say yes and no at once"


async def test_a_blocked_domain_beats_an_approved_sender_inside_it(db):
    """The reason a domain is blocked at all: not having to hunt down every address in it.

    The specific rule usually wins. Here it must not — otherwise an approval granted earlier
    punches a hole into the block that nobody sees.
    """
    u = await _owner(db)
    await upsert_policy(db, u.id, match_kind="sender", match_value="one@example.org")
    await upsert_policy(db, u.id, match_kind="domain", match_value="example.org", blocked=True)
    await db.commit()

    hit = await match_policy(db, u.id, sender_email="one@example.org", domain="example.org",
                             category="")
    assert hit is not None and hit.blocked and hit.match_kind == "domain"


async def test_without_a_block_the_specific_rule_still_wins(db):
    u = await _owner(db)
    await upsert_policy(db, u.id, match_kind="domain", match_value="example.org",
                        action_hint="the broad one")
    await upsert_policy(db, u.id, match_kind="sender", match_value="one@example.org",
                        action_hint="the exact one")
    await db.commit()

    hit = await match_policy(db, u.id, sender_email="one@example.org", domain="example.org",
                             category="")
    assert hit is not None and hit.match_kind == "sender"


async def test_a_rule_says_where_it_came_from(db):
    u = await _owner(db)
    await upsert_policy(db, u.id, match_kind="sender", match_value="post@example.org",
                        origin="A delivery note", origin_task_id=42)
    await db.commit()
    p = (await db.execute(
        AssistantPolicy.__table__.select().where(AssistantPolicy.owner_user_id == u.id))).first()
    assert p.origin == "A delivery note" and p.origin_task_id == 42

    # A second grant does not overwrite the origin: that is the moment it was granted, not
    # the last time somebody touched the row.
    await upsert_policy(db, u.id, match_kind="sender", match_value="post@example.org",
                        origin="Something else")
    await db.commit()
    p = (await db.execute(
        AssistantPolicy.__table__.select().where(AssistantPolicy.owner_user_id == u.id))).first()
    assert p.origin == "A delivery note"


async def test_taking_a_rule_back(db):
    u = await _owner(db)
    await upsert_policy(db, u.id, match_kind="sender", match_value="post@example.org")
    await db.commit()

    assert await revoke_policy(db, u.id, match_kind="sender", match_value="POST@example.org")
    await db.commit()
    assert await match_policy(db, u.id, sender_email="post@example.org", domain="example.org",
                              category="") is None
    # And a second time it says so instead of pretending.
    assert not await revoke_policy(db, u.id, match_kind="sender", match_value="post@example.org")


async def test_a_blocked_sender_does_not_run_by_itself(db):
    """The whole point, on the real way in.

    The trigger may be set to run without asking (`auto_run`). A block is stronger: whoever
    puts a sender on the block list means every way in, not only the one through the review
    card. Without this the guard would sit in the wrong place and the item would run anyway.
    """
    from app.models.assistant import AssistantTask
    from app.services import workflow_templates
    from sqlalchemy import select

    from conftest import make_user, make_webhook, report

    user = await make_user(db, "blocked-owner")
    await workflow_templates.create(db, "mail-eingang", owner_id=user.id)
    await upsert_policy(db, user.id, match_kind="domain", match_value="beispiel.de",
                        blocked=True)
    await db.commit()

    sub = await make_webhook(db, user, "mail-block", mode="assistant", agent="assistent",
                             auto_run=True)
    await report(db, sub, {"account": "privat", "uid": 4711,
                           "from": "wer@beispiel.de", "subject": "Etwas", "body": "Text"})
    task = (await db.execute(select(AssistantTask).where(
        AssistantTask.source_ref == "privat:4711"))).scalars().one()
    assert task.status == "new", "it waits for a person instead of running"
