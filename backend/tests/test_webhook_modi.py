"""Ein Webhook nimmt entgegen und prüft — was daraus wird, steht im Ablauf.

Früher konnte er selbst ein Ticket anlegen, eine Nachricht schicken oder den Assistenten
beauftragen: drei Wege im Code, jeder mit eigenen Spalten und nur für Webhooks zu haben.
Geprüft wird hier beides — dass der Kontext generisch entsteht (aus der Nutzlast, aus festen
Werten, verschachtelt) und dass die Umstellung nichts von dem verliert, was die alten Modi
konnten.
"""
import pytest
from app.api.ops import _kontext, _referenz
from app.models.assistant import AssistantTask
from app.models.notification import Notification
from app.models.ops import WebhookSub
from app.models.ticket import Issue
from app.models.workflow import WorkflowDefinition
from sqlalchemy import select

from conftest import make_project, make_user, make_webhook, melde


@pytest.fixture
async def owner(db):
    return await make_user(db, "owner")


def _sub(**felder) -> WebhookSub:
    return WebhookSub(public_id="x", route="test", **felder)


# ── Kontextaufbau ────────────────────────────────────────────────────────────

def test_ohne_abbildung_ist_die_nutzlast_der_kontext():
    assert _kontext(_sub(), {"a": 1}) == {"a": 1}


def test_leerer_pfad_legt_die_ganze_nutzlast_unter_einen_schluessel():
    """So kommt die Mail unter `mail`, statt ihre Felder flach im Kontext zu verstreuen."""
    ctx = _kontext(_sub(context_map={"mail": ""}), {"uid": 7, "subject": "Hallo"})
    assert ctx == {"mail": {"uid": 7, "subject": "Hallo"}}


def test_punkte_im_ziel_verschachteln():
    ctx = _kontext(_sub(context_map={"post.absender": "from.addr"},
                        context_fixed={"post.kanal": "imap", "post.zahl": 3}),
                   {"from": {"addr": "a@b.de"}})
    assert ctx == {"post": {"absender": "a@b.de", "kanal": "imap", "zahl": 3}}


def test_feste_werte_duerfen_aus_der_nutzlast_fuellen():
    ctx = _kontext(_sub(context_fixed={"quelle": "Konto {account}, Nachricht {uid}"}),
                   {"account": "privat", "uid": 4})
    assert ctx["quelle"] == "Konto privat, Nachricht 4"


def test_referenz_aus_mehreren_feldern():
    """Ein fremdes System schickt selten eine eigene Id — dann setzt man sie zusammen."""
    assert _referenz(_sub(ref_field="{account}:{uid}"), {"account": "privat", "uid": 4}) \
        == "privat:4"
    assert _referenz(_sub(ref_field="event.id"), {"event": {"id": 12}}) == "12"
    assert _referenz(_sub(ref_field=""), {"a": 1}) is None
    assert _referenz(_sub(ref_field="fehlt"), {"a": 1}) is None


# ── Umstellung der alten Modi ────────────────────────────────────────────────

async def test_assistent_wird_ein_ablauf(db, owner, redis_stub):
    sub = await make_webhook(db, owner, "batterie", mode="assistant", agent="hausmeister",
                             auto_run=True, prompt_tmpl="Sensor {entity_id} ist leer.")
    assert sub.mode == "workflow" and sub.workflow_definition_id

    await melde(db, sub, {"entity_id": "sensor.tuer"})
    task = (await db.execute(select(AssistantTask))).scalars().one()
    assert task.meta["agent"] == "hausmeister"
    # Der Prompt wird zum Auftragstext, und die Platzhalter füllt jetzt der Ablauf.
    assert task.meta["prompt"] == "Sensor sensor.tuer ist leer."
    # `auto_run` hieß „ohne Rückfrage laufen“ — daraus wird der Freigabe-Schalter.
    assert task.status == "approved"


async def test_ohne_auto_run_wartet_der_auftrag(db, owner, redis_stub):
    sub = await make_webhook(db, owner, "post", mode="assistant", agent="assistent",
                             auto_run=False, prompt_tmpl="Schau dir {sache} an.")
    await melde(db, sub, {"sache": "das Paket"})
    task = (await db.execute(select(AssistantTask))).scalars().one()
    assert task.status == "new"


async def test_meldung_wird_ein_ablauf(db, owner):
    sub = await make_webhook(db, owner, "alarm", mode="notify",
                             title_template="Alarm {ort}", body_template="{text}")
    assert sub.mode == "workflow"

    await melde(db, sub, {"ort": "Keller", "text": "Wasser"})
    karte = (await db.execute(select(Notification))).scalars().one()
    assert karte.title == "Alarm Keller" and karte.body == "Wasser"
    assert karte.user_id == owner.id


async def test_ticket_wird_ein_ablauf(db, owner):
    from app.models.enums import StatusCategory
    from app.models.ticket import IssueCounter, IssueType, WorkflowStatus

    projekt = await make_project(db, "WEB", "Web")
    db.add_all([
        IssueType(project_id=projekt.id, name="Aufgabe"),
        WorkflowStatus(project_id=projekt.id, name="To Do", category=StatusCategory.todo,
                       order=0),
        IssueCounter(project_id=projekt.id, last_number=0),
    ])
    await db.commit()
    sub = await make_webhook(db, owner, "anfrage", mode="task", project_id=projekt.id,
                             title_template="{betreff}", body_template="{text}")
    assert sub.mode == "workflow"

    await melde(db, sub, {"betreff": "Seite kaputt", "text": "500 beim Speichern"})
    issue = (await db.execute(select(Issue))).scalars().one()
    assert issue.summary == "Seite kaputt" and issue.description == "500 beim Speichern"
    assert issue.project_id == projekt.id


async def test_mail_meldet_ein_ereignis_statt_zu_handeln(db, owner):
    """Der Mail-Eingang ist der eine Fall, der kein eigener Ablauf wird: Er meldet, dass eine
    Mail da ist, und wer darauf hört, entscheidet der Ablauf mit dem passenden Auslöser."""
    sub = await make_webhook(db, owner, "new-email", mode="assistant", agent="assistent",
                             classify_agent="mail_classifier", prompt_tmpl="Betreff: {subject}")
    assert sub.mode == "event" and sub.event_name == "mail.received"
    assert sub.ref_field == "{account}:{uid}"

    ctx = _kontext(sub, {"account": "privat", "uid": 9, "subject": "Rechnung"})
    assert ctx["mail"]["subject"] == "Rechnung"
    assert ctx["intake"]["agent"] == "assistent"
    assert ctx["intake"]["classify_agent"] == "mail_classifier"
    assert ctx["intake"]["source_ref"] == "privat:9"
    assert ctx["intake"]["owner_id"] == owner.id


async def test_umstellung_fasst_umgestellte_nicht_wieder_an(db, owner):
    from app.services.webhook_modes import umstellen

    sub = await make_webhook(db, owner, "einmal", mode="notify", body_template="hallo")
    erste_definition = sub.workflow_definition_id

    assert await umstellen(db) == 0
    await db.refresh(sub)
    assert sub.workflow_definition_id == erste_definition
    assert len((await db.execute(select(WorkflowDefinition))).scalars().all()) == 1


# ── Name und Schlüssel gehören der Sache ─────────────────────────────────────

async def test_name_beschreibt_die_sache_nicht_den_auslöser(db, owner, redis_stub):
    """„Webhook: ha-battery-low“ benannte den Briefkasten, nicht den Brief."""
    sub = await make_webhook(db, owner, "ha-battery-low", mode="assistant",
                             agent="assistent", prompt_tmpl="Batterie leer.")
    d = await db.get(WorkflowDefinition, sub.workflow_definition_id)
    assert d.name == "Ha battery low"
    assert d.key == "ha-battery-low"


async def test_schluessel_weicht_einer_kollision_aus(db, owner, redis_stub):
    erste = await make_webhook(db, owner, "alarm", mode="notify", body_template="a")
    zweite = await make_webhook(db, owner, "alarm", mode="notify", body_template="b")
    keys = {(await db.get(WorkflowDefinition, s.workflow_definition_id)).key
            for s in (erste, zweite)}
    assert keys == {"alarm", "alarm-2"}


async def test_umbenennen_geht_ueber_die_api(client, db, owner):
    from conftest import auth

    sub = await make_webhook(db, owner, "post", mode="notify", body_template="x")
    r = await client.put(f"/workflows/{sub.workflow_definition_id}", headers=auth(owner),
                         json={"name": "Post sortieren", "key": "Post Sortieren!"})
    assert r.status_code == 200, r.text
    # Aus der Eingabe wird ein sauberer Schlüssel, statt sie abzulehnen.
    assert r.json()["key"] == "post-sortieren" and r.json()["name"] == "Post sortieren"


async def test_belegter_schluessel_wird_abgelehnt(client, db, owner):
    from conftest import auth

    erste = await make_webhook(db, owner, "eins", mode="notify", body_template="x")
    zweite = await make_webhook(db, owner, "zwei", mode="notify", body_template="x")
    r = await client.put(f"/workflows/{zweite.workflow_definition_id}", headers=auth(owner),
                         json={"key": "eins"})
    assert r.status_code == 400
    await db.refresh(await db.get(WorkflowDefinition, erste.workflow_definition_id))


async def test_ein_satz_ablauf_behaelt_seinen_schluessel(client, db, owner):
    """Dort ist der Schlüssel die Verbindung, nicht die Beschriftung."""
    from app.models.enums import WorkflowSubjectKind
    from conftest import auth

    d = WorkflowDefinition(project_id=None, key="ticket-lifecycle", name="Lebenszyklus",
                           slot="ticket_lifecycle", created_by=owner.id,
                           subject_kind=WorkflowSubjectKind.issue)
    db.add(d)
    await db.commit()
    r = await client.put(f"/workflows/{d.id}", headers=auth(owner), json={"key": "anders"})
    assert r.status_code == 400
