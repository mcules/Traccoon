"""A webhook receives and checks — what becomes of it is written in the flow.

It used to be able to create a ticket itself, send a message or assign the assistant: three
ways in the code, each with columns of its own and available to webhooks only. What is checked
here is both — that the context comes into being generically (from the payload, from fixed
values, nested) and that the conversion loses nothing of what the old modes
konnten.
"""
import pytest
from app.api.ops import _context, _reference
from app.models.assistant import AssistantTask
from app.models.notification import Notification
from app.models.ops import WebhookSub
from app.models.ticket import Issue
from app.models.workflow import WorkflowDefinition
from sqlalchemy import select

from conftest import make_project, make_user, make_webhook, report


@pytest.fixture
async def owner(db):
    return await make_user(db, "owner")


def _sub(**fields) -> WebhookSub:
    return WebhookSub(public_id="x", route="test", **fields)


# ── Kontextaufbau ────────────────────────────────────────────────────────────

def test_without_a_mapping_the_payload_is_the_context():
    assert _context(_sub(), {"a": 1}) == {"a": 1}


def test_an_empty_path_puts_the_whole_payload_under_one_key():
    """That way the mail arrives under `mail` instead of scattering its fields flat in the context."""
    ctx = _context(_sub(context_map={"mail": ""}), {"uid": 7, "subject": "Hallo"})
    assert ctx == {"mail": {"uid": 7, "subject": "Hallo"}}


def test_dots_in_the_target_nest():
    ctx = _context(_sub(context_map={"post.absender": "from.addr"},
                        context_fixed={"post.kanal": "imap", "post.zahl": 3}),
                   {"from": {"addr": "a@b.de"}})
    assert ctx == {"post": {"absender": "a@b.de", "kanal": "imap", "zahl": 3}}


def test_fixed_values_may_be_filled_from_the_payload():
    ctx = _context(_sub(context_fixed={"quelle": "Konto {account}, Nachricht {uid}"}),
                   {"account": "privat", "uid": 4})
    assert ctx["quelle"] == "Konto privat, Nachricht 4"


def test_a_reference_built_from_several_fields():
    """A foreign system rarely sends an id of its own — then one composes it."""
    assert _reference(_sub(ref_field="{account}:{uid}"), {"account": "privat", "uid": 4}) \
        == "privat:4"
    assert _reference(_sub(ref_field="event.id"), {"event": {"id": 12}}) == "12"
    assert _reference(_sub(ref_field=""), {"a": 1}) is None
    assert _reference(_sub(ref_field="fehlt"), {"a": 1}) is None


# ── Umstellung der alten Modi ────────────────────────────────────────────────

async def test_the_assistant_becomes_a_flow(db, owner, redis_stub):
    sub = await make_webhook(db, owner, "batterie", mode="assistant", agent="hausmeister",
                             auto_run=True, prompt_tmpl="Sensor {entity_id} ist leer.")
    assert sub.mode == "workflow" and sub.workflow_definition_id

    await report(db, sub, {"entity_id": "sensor.tuer"})
    task = (await db.execute(select(AssistantTask))).scalars().one()
    assert task.meta["agent"] == "hausmeister"
    # The prompt becomes the assignment text, and the placeholders the flow now fills.
    assert task.meta["prompt"] == "Sensor sensor.tuer ist leer."
    # `auto_run` meant "run without asking" — out of it comes the approval switch.
    assert task.status == "approved"


async def test_without_auto_run_the_task_waits(db, owner, redis_stub):
    sub = await make_webhook(db, owner, "post", mode="assistant", agent="assistent",
                             auto_run=False, prompt_tmpl="Schau dir {sache} an.")
    await report(db, sub, {"sache": "das Paket"})
    task = (await db.execute(select(AssistantTask))).scalars().one()
    assert task.status == "new"


async def test_a_report_becomes_a_flow(db, owner):
    sub = await make_webhook(db, owner, "alarm", mode="notify",
                             title_template="Alarm {ort}", body_template="{text}")
    assert sub.mode == "workflow"

    await report(db, sub, {"ort": "Keller", "text": "Wasser"})
    karte = (await db.execute(select(Notification))).scalars().one()
    assert karte.title == "Alarm Keller" and karte.body == "Wasser"
    assert karte.user_id == owner.id


async def test_a_ticket_becomes_a_flow(db, owner):
    from app.models.enums import StatusCategory
    from app.models.ticket import IssueCounter, IssueType, WorkflowStatus

    project = await make_project(db, "WEB", "Web")
    db.add_all([
        IssueType(project_id=project.id, name="Aufgabe"),
        WorkflowStatus(project_id=project.id, name="To Do", category=StatusCategory.todo,
                       order=0),
        IssueCounter(project_id=project.id, last_number=0),
    ])
    await db.commit()
    sub = await make_webhook(db, owner, "anfrage", mode="task", project_id=project.id,
                             title_template="{betreff}", body_template="{text}")
    assert sub.mode == "workflow"

    await report(db, sub, {"betreff": "Seite kaputt", "text": "500 beim Speichern"})
    issue = (await db.execute(select(Issue))).scalars().one()
    assert issue.summary == "Seite kaputt" and issue.description == "500 beim Speichern"
    assert issue.project_id == project.id


async def test_mail_reports_an_event_instead_of_acting(db, owner):
    """The mail intake is the one case that does not become a flow of its own: it reports that a
    mail is there, and who listens to that the flow with the matching trigger decides."""
    sub = await make_webhook(db, owner, "new-email", mode="assistant", agent="assistent",
                             classify_agent="mail_classifier", prompt_tmpl="Betreff: {subject}")
    assert sub.mode == "event" and sub.event_name == "mail.received"
    assert sub.ref_field == "{account}:{uid}"

    ctx = _context(sub, {"account": "privat", "uid": 9, "subject": "Rechnung"})
    assert ctx["mail"]["subject"] == "Rechnung"
    assert ctx["intake"]["agent"] == "assistent"
    assert ctx["intake"]["classify_agent"] == "mail_classifier"
    assert ctx["intake"]["source_ref"] == "privat:9"
    assert ctx["intake"]["owner_id"] == owner.id


async def test_the_conversion_does_not_touch_the_converted_again(db, owner):
    from app.services.webhook_modes import convert

    sub = await make_webhook(db, owner, "einmal", mode="notify", body_template="hallo")
    first_definition = sub.workflow_definition_id

    assert await convert(db) == 0
    await db.refresh(sub)
    assert sub.workflow_definition_id == first_definition
    assert len((await db.execute(select(WorkflowDefinition))).scalars().all()) == 1


# ── Name and key belong to the matter ───────────────────────────────────────

async def test_the_name_describes_the_matter_not_the_trigger(db, owner, redis_stub):
    """"Webhook: ha-battery-low" named the letterbox, not the letter."""
    sub = await make_webhook(db, owner, "ha-battery-low", mode="assistant",
                             agent="assistent", prompt_tmpl="Batterie leer.")
    d = await db.get(WorkflowDefinition, sub.workflow_definition_id)
    assert d.name == "Ha battery low"
    assert d.key == "ha-battery-low"


async def test_the_key_dodges_a_collision(db, owner, redis_stub):
    first = await make_webhook(db, owner, "alarm", mode="notify", body_template="a")
    second = await make_webhook(db, owner, "alarm", mode="notify", body_template="b")
    keys = {(await db.get(WorkflowDefinition, s.workflow_definition_id)).key
            for s in (first, second)}
    assert keys == {"alarm", "alarm-2"}


async def test_renaming_works_through_the_api(client, db, owner):
    from conftest import auth

    sub = await make_webhook(db, owner, "post", mode="notify", body_template="x")
    r = await client.put(f"/workflows/{sub.workflow_definition_id}", headers=auth(owner),
                         json={"name": "Post sortieren", "key": "Post Sortieren!"})
    assert r.status_code == 200, r.text
    # Out of the input comes a clean key instead of refusing it.
    assert r.json()["key"] == "post-sortieren" and r.json()["name"] == "Post sortieren"


async def test_a_taken_key_is_refused(client, db, owner):
    from conftest import auth

    first = await make_webhook(db, owner, "eins", mode="notify", body_template="x")
    second = await make_webhook(db, owner, "zwei", mode="notify", body_template="x")
    r = await client.put(f"/workflows/{second.workflow_definition_id}", headers=auth(owner),
                         json={"key": "eins"})
    assert r.status_code == 400
    await db.refresh(await db.get(WorkflowDefinition, first.workflow_definition_id))


async def test_a_preset_flow_keeps_its_key(client, db, owner):
    """There the key is the connection, not the label."""
    from app.models.enums import WorkflowSubjectKind
    from conftest import auth

    d = WorkflowDefinition(project_id=None, key="ticket-lifecycle", name="Lebenszyklus",
                           slot="ticket_lifecycle", created_by=owner.id,
                           subject_kind=WorkflowSubjectKind.issue)
    db.add(d)
    await db.commit()
    r = await client.put(f"/workflows/{d.id}", headers=auth(owner), json={"key": "anders"})
    assert r.status_code == 400
