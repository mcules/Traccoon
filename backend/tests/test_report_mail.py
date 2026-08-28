"""A report as a conversation: answering by mail, and the answer that comes back.

Two questions decide whether this way works at all, and both are about recognising: does the
answer of the team find the reporter, and does the reply of the reporter find its report.
Everything else here guards the two ways it must NOT work — a stranger writing into a
conversation by knowing a number, and a mail becoming a report because it happened to arrive
in a private mailbox.
"""
import pytest

from app.core.security import encrypt_secret
from app.models.bugs import BugSource, ReportPost
from app.models.mail import MailAccount
from app.models.agents import AgentDefinition
from app.models.project import Project
from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
from app.services import bugs as svc
from app.services import mailbox, report_draft, report_mail
from sqlalchemy import select

from conftest import auth, make_project, make_user


async def make_mailbox(db, user, address="reports@example.org"):
    """A mailbox of a person, and the address a project would answer under.

    The address is not an identity on file: on a catch-all domain it is the same mailbox with
    another name on the envelope, and the login stays where it is maintained.
    """
    account = MailAccount(owner_user_id=user.id, name="post",
                          imap_host="imap.example.org", imap_user="ich",
                          imap_password_enc=encrypt_secret("x"),
                          smtp_host="smtp.example.org", smtp_user="ich",
                          smtp_password_enc=encrypt_secret("x"), enabled=True)
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return account, address


async def make_source(db, key="devprog", box=None):
    """A reporting program with its project. The mailbox hangs off the project, which is the
    whole point of this arrangement: two programs of one project answer from one address."""
    project = await make_project(db, key[:3].upper(), key.title())
    if box is not None:
        account, address = box
        project.mail_account_id = account.id
        project.reply_from = address
    source = BugSource(key=key, name=key.title(), hourly_limit=20, project_id=project.id)
    token = svc.new_token()
    svc.set_token(source, token)
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source, token


@pytest.fixture
def outbox(monkeypatch):
    """Everything that would go out by SMTP, as a list."""
    sent: list[dict] = []

    async def send(account, identity, fields):
        sent.append(fields)

    monkeypatch.setattr(mailbox, "send", send)
    return sent


REPORT = {"title": "The channel list stays empty", "details": "Since the update.",
          "contact": "DL1XXX", "reply_email": "reporter@example.net"}


def mail(**fields) -> dict:
    """A mail the way the watcher reports it."""
    return {"account": "post", "folder": "INBOX", "uid": 7,
            "from": [{"name": "Reporter", "addr": "reporter@example.net"}],
            "to": [{"name": "", "addr": "reports@example.org"}],
            "subject": "Re: something", "body_text": "Thanks, that was it.",
            "message_id": "<their-1@example.net>", "headers": {}, **fields}


# ── Answering ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_answer_goes_out_by_mail(client, db, outbox):
    anna = await make_user(db, "anna", admin=True)
    box = await make_mailbox(db, anna)
    source, token = await make_source(db, box=box)
    number = (await client.post("/bugs/report", json=REPORT,
                                headers={"X-Bug-Token": token})).json()["id"]

    answer = await client.post(f"/bugs/{number}/posts", json={"body": "Fixed, please look."},
                               headers=auth(anna))

    assert answer.status_code == 201, answer.text
    assert len(outbox) == 1
    assert outbox[0]["to"] == ["reporter@example.net"]
    assert f"[BUG-{number}]" in outbox[0]["subject"]
    # The reference the reply carries back. Without it the answer of the reporter would be a
    # mail without a home.
    assert outbox[0]["message_id"].startswith(f"<bug{number}.")


@pytest.mark.asyncio
async def test_an_internal_note_never_travels(client, db, outbox):
    anna = await make_user(db, "anna", admin=True)
    box = await make_mailbox(db, anna)
    _, token = await make_source(db, box=box)
    number = (await client.post("/bugs/report", json=REPORT,
                                headers={"X-Bug-Token": token})).json()["id"]

    await client.post(f"/bugs/{number}/posts",
                      json={"body": "He never had the profile.", "internal": True},
                      headers=auth(anna))

    assert outbox == []


@pytest.mark.asyncio
async def test_without_a_mailbox_the_answer_only_stays_here(client, db, outbox):
    """A report of a program that answers from nowhere: the entry stands, nothing goes out.

    Deliberately not an error — the way through the program still works, and an answer
    refused because of a missing mailbox would be worse than one that travels only there.
    """
    anna = await make_user(db, "anna", admin=True)
    _, token = await make_source(db)          # a project without a mailbox
    number = (await client.post("/bugs/report", json=REPORT,
                                headers={"X-Bug-Token": token})).json()["id"]

    answer = await client.post(f"/bugs/{number}/posts", json={"body": "Have a look."},
                               headers=auth(anna))

    assert answer.status_code == 201
    assert outbox == []
    assert (await client.get(f"/bugs/{number}", headers=auth(anna))).json()["mail_ready"] is False


@pytest.mark.asyncio
async def test_a_report_without_an_address_says_so(client, db, outbox):
    anna = await make_user(db, "anna", admin=True)
    box = await make_mailbox(db, anna)
    _, token = await make_source(db, box=box)
    number = (await client.post("/bugs/report", json={**REPORT, "reply_email": "",
                                                      "contact": "DL1XXX"},
                                headers={"X-Bug-Token": token})).json()["id"]

    await client.post(f"/bugs/{number}/posts", json={"body": "Which radio is it?"},
                      headers=auth(anna))
    assert outbox == []

    # Typed in afterwards — that is the usual case, the address stands in the third sentence.
    later = await client.post(f"/bugs/{number}/reporter",
                              json={"reply_email": "reporter@example.net"}, headers=auth(anna))
    assert later.status_code == 200
    assert later.json()["mail_ready"] is True

    await client.post(f"/bugs/{number}/posts", json={"body": "And now?"}, headers=auth(anna))
    assert len(outbox) == 1


@pytest.mark.asyncio
async def test_a_callsign_is_not_an_address(client, db):
    anna = await make_user(db, "anna", admin=True)
    box = await make_mailbox(db, anna)
    _, token = await make_source(db, box=box)
    number = (await client.post("/bugs/report", json={**REPORT, "reply_email": ""},
                                headers={"X-Bug-Token": token})).json()["id"]

    answer = await client.post(f"/bugs/{number}/reporter", json={"reply_email": "DL1XXX"},
                               headers=auth(anna))

    assert answer.status_code == 400
    assert answer.json()["key"] == "err.no_mail_address"


# ── The way back ─────────────────────────────────────────────────────────────

async def one_report(client, db, anna, token):
    number = (await client.post("/bugs/report", json=REPORT,
                                headers={"X-Bug-Token": token})).json()["id"]
    await client.post(f"/bugs/{number}/posts", json={"body": "Fixed, please look."},
                      headers=auth(anna))
    return number


@pytest.mark.asyncio
async def test_the_reply_lands_in_the_thread(client, db, outbox):
    anna = await make_user(db, "anna", admin=True)
    box = await make_mailbox(db, anna)
    _, token = await make_source(db, box=box)
    number = await one_report(client, db, anna, token)
    ours = outbox[0]["message_id"]

    found = await report_mail.match(db, mail(headers={"In-Reply-To": ours}))
    assert found is not None
    artifact, way = found
    assert artifact.id == number and way == "reference"
    await report_mail.file_reply(db, artifact, mail(headers={"In-Reply-To": ours}))

    thread = (await client.get(f"/bugs/{number}/posts", headers=auth(anna))).json()
    assert [p["via"] for p in thread] == ["web", "mail"]
    assert thread[-1]["body"] == "Thanks, that was it."
    assert thread[-1]["team"] is False


@pytest.mark.asyncio
async def test_the_same_mail_twice_stays_one_entry(client, db, outbox):
    anna = await make_user(db, "anna", admin=True)
    box = await make_mailbox(db, anna)
    _, token = await make_source(db, box=box)
    number = await one_report(client, db, anna, token)
    reply = mail(headers={"In-Reply-To": outbox[0]["message_id"]})

    artifact, _ = await report_mail.match(db, reply)
    assert await report_mail.file_reply(db, artifact, reply) is not None
    assert await report_mail.file_reply(db, artifact, reply) is None

    posts = (await db.execute(select(ReportPost).where(
        ReportPost.artifact_id == number, ReportPost.via == "mail"))).scalars().all()
    assert len(posts) == 1


@pytest.mark.asyncio
async def test_the_number_alone_opens_no_conversation(client, db, outbox):
    """A forged reference is none: the secret is what makes the reply belong here."""
    anna = await make_user(db, "anna", admin=True)
    box = await make_mailbox(db, anna)
    _, token = await make_source(db, box=box)
    number = await one_report(client, db, anna, token)

    forged = mail(headers={"In-Reply-To": f"<bug{number}.000000000000.1@example.org>"},
                  subject=f"[BUG-{number}] I want in",
                  **{"from": [{"name": "Stranger", "addr": "someone@elsewhere.example"}]})

    assert await report_mail.match(db, forged) is None


@pytest.mark.asyncio
async def test_the_subject_works_when_the_sender_is_the_reporter(client, db, outbox):
    """Mail programs that answer without a reference exist. Then the tag counts — but only
    together with the address of the report, or the number would be the whole password."""
    anna = await make_user(db, "anna", admin=True)
    box = await make_mailbox(db, anna)
    _, token = await make_source(db, box=box)
    number = await one_report(client, db, anna, token)

    found = await report_mail.match(db, mail(subject=f"Re: [BUG-{number}] The channel list"))
    assert found is not None and found[1] == "subject"

    stranger = mail(subject=f"Re: [BUG-{number}] The channel list",
                    **{"from": [{"name": "X", "addr": "someone@elsewhere.example"}]})
    assert await report_mail.match(db, stranger) is None


@pytest.mark.asyncio
async def test_the_reply_quotes_our_answer_and_that_is_left_out(client, db, outbox):
    anna = await make_user(db, "anna", admin=True)
    box = await make_mailbox(db, anna)
    _, token = await make_source(db, box=box)
    number = await one_report(client, db, anna, token)
    reply = mail(headers={"In-Reply-To": outbox[0]["message_id"]},
                 body_text="Works now, thanks.\n\nAm 28.08.2026 schrieb Anna:\n> Fixed, "
                           "please look.\n> [BUG-1]")

    artifact, _ = await report_mail.match(db, reply)
    post = await report_mail.file_reply(db, artifact, reply)

    assert post.body == "Works now, thanks."


# ── A mail that answers nothing ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_mail_to_a_report_address_becomes_a_report(client, db):
    anna = await make_user(db, "anna", admin=True)
    box = await make_mailbox(db, anna)
    source, _ = await make_source(db, box=box)

    artifact = await report_mail.new_from_mail(db, mail(
        subject="The list stays empty", message_id="<first@example.net>"))

    assert artifact is not None
    assert await svc.value_of(db, artifact.id, "app") == source.key
    assert await svc.value_of(db, artifact.id, "reply_email") == "reporter@example.net"
    # The second mail of the same person refers to their own first one — and must land in
    # the same report instead of opening a second.
    again = mail(message_id="<second@example.net>",
                 headers={"In-Reply-To": "<first@example.net>"})
    found = await report_mail.match(db, again)
    assert found is not None and found[0].id == artifact.id


@pytest.mark.asyncio
async def test_private_mail_never_becomes_a_report(client, db):
    """The same watcher carries the private mailbox. A mail to it is nobody's report."""
    anna = await make_user(db, "anna", admin=True)
    box = await make_mailbox(db, anna)
    await make_source(db, box=box)

    private = mail(to=[{"name": "", "addr": "anna@example.org"}], headers={})

    assert await report_mail.new_from_mail(db, private) is None


@pytest.mark.asyncio
async def test_an_automatic_reply_opens_nothing(client, db):
    """An out-of-office answer to our answer would otherwise become a report, whose answer
    would trigger the next one."""
    anna = await make_user(db, "anna", admin=True)
    box = await make_mailbox(db, anna)
    await make_source(db, box=box)

    away = mail(subject="Out of office", headers={"Auto-Submitted": "auto-replied"})

    assert await report_mail.new_from_mail(db, away) is None


# ── The conversation one starts oneself ──────────────────────────────────────

@pytest.mark.asyncio
async def test_a_report_opened_here_answers_by_mail(client, db, outbox):
    anna = await make_user(db, "anna", admin=True)
    box = await make_mailbox(db, anna)

    made = await client.post("/bugs", headers=auth(anna), json={
        "title": "About the club evening", "kind": "question",
        "details": "Is the room booked?", "contact": "Someone",
        "reply_email": "someone@example.net",
        "account_id": box[0].id, "mail_from": box[1]})
    assert made.status_code == 201, made.text
    number = made.json()["id"]
    assert made.json()["mail_ready"] is True

    await client.post(f"/bugs/{number}/posts", json={"body": "Is the room booked?"},
                      headers=auth(anna))

    assert len(outbox) == 1
    assert outbox[0]["to"] == ["someone@example.net"]


@pytest.mark.asyncio
async def test_a_foreign_mailbox_is_not_available(client, db):
    anna = await make_user(db, "anna", admin=True)
    berta = await make_user(db, "berta")
    box = await make_mailbox(db, berta, address="berta@example.org")

    answer = await client.post("/bugs", headers=auth(anna), json={
        "title": "Not with that address", "account_id": box[0].id,
        "mail_from": box[1]})

    assert answer.status_code == 404
    assert answer.json()["key"] == "err.mail_account_not_found"


# ── The proposed answer ──────────────────────────────────────────────────────

@pytest.fixture
def model(monkeypatch):
    """A model that answers, and everything that was asked of it."""
    asked: list[dict] = []

    class Answer:
        text = "Danke für die Meldung, wir schauen uns das an."

    class Provider:
        def __init__(self, base_url=""):
            pass

        async def chat(self, **fields):
            asked.append(fields)
            return Answer()

    async def token(*a, **k):
        return "tok"

    async def address(*a, **k):
        return "http://model.local/v1"

    monkeypatch.setattr(report_draft, "OpenAIProvider", Provider)
    monkeypatch.setattr(report_draft, "resolve_provider_token", token)
    monkeypatch.setattr(report_draft, "resolve_provider_base_url", address)
    return asked


async def make_agent(db, user, role="mail_classifier", provider="openai"):
    db.add(AgentDefinition(user_id=user.id, role=role, provider=provider,
                           model="a-model"))
    await db.commit()


@pytest.mark.asyncio
async def test_a_draft_is_written_but_never_sent(client, db, outbox, model):
    """The whole point of this feature: text comes back, nothing leaves the house."""
    anna = await make_user(db, "anna", admin=True)
    box = await make_mailbox(db, anna)
    _, token = await make_source(db, box=box)
    await make_agent(db, anna)
    number = (await client.post("/bugs/report", json=REPORT,
                                headers={"X-Bug-Token": token})).json()["id"]

    answer = await client.post(f"/bugs/{number}/draft",
                               json={"comments": ["Ist behoben."]}, headers=auth(anna))

    assert answer.status_code == 200, answer.text
    assert answer.json()["text"].startswith("Danke")
    assert answer.json()["agent"] == "mail_classifier"
    # Neither sent nor filed: the draft exists in the answer and nowhere else.
    assert outbox == []
    assert (await client.get(f"/bugs/{number}/posts", headers=auth(anna))).json() == []
    # What the model got to read: the report, and what the person added to it.
    asked = model[0]["messages"][1]["content"]
    assert "The channel list stays empty" in asked and "Ist behoben." in asked


@pytest.mark.asyncio
async def test_a_subscription_agent_writes_no_draft(client, db, model):
    """A CLI agent takes minutes. Saying so beats a form that hangs."""
    anna = await make_user(db, "anna", admin=True)
    box = await make_mailbox(db, anna)
    _, token = await make_source(db, box=box)
    await make_agent(db, anna, provider="claude_code")
    number = (await client.post("/bugs/report", json=REPORT,
                                headers={"X-Bug-Token": token})).json()["id"]

    answer = await client.post(f"/bugs/{number}/draft", json={}, headers=auth(anna))

    assert answer.status_code == 400
    assert answer.json()["key"] == "err.no_draft_agent"


@pytest.mark.asyncio
async def test_without_an_agent_the_form_learns_why(client, db):
    anna = await make_user(db, "anna", admin=True)
    box = await make_mailbox(db, anna)
    _, token = await make_source(db, box=box)
    number = (await client.post("/bugs/report", json=REPORT,
                                headers={"X-Bug-Token": token})).json()["id"]

    answer = await client.post(f"/bugs/{number}/draft", json={}, headers=auth(anna))

    assert answer.status_code == 400
    # The reason travels in words: without it the button says "did not work" and the person
    # goes looking in the wrong place.
    assert "mail_classifier" in answer.json()["detail"]


@pytest.mark.asyncio
async def test_the_draft_reads_the_ticket_and_the_project(client, db, outbox, model):
    """A report is the oldest thing in the matter. What became of it as work is where the
    answer stands — so the ticket, its comments and the project knowledge travel along."""
    from app.models.ticket import Comment
    from app.services import bugs as bugs_svc

    anna = await make_user(db, "anna", admin=True)
    box = await make_mailbox(db, anna)
    source, token = await make_source(db, box=box)
    await make_agent(db, anna)

    project = await db.get(Project, source.project_id)
    project.description = "Ein Programm, das Geräte programmiert."
    db.add(IssueType(project_id=project.id, name="Bug"))
    db.add(WorkflowStatus(project_id=project.id, name="In Arbeit"))
    db.add(IssueCounter(project_id=project.id, last_number=0))
    await db.commit()

    number = (await client.post("/bugs/report", json=REPORT,
                                headers={"X-Bug-Token": token})).json()["id"]
    made = await client.post(f"/bugs/{number}/ticket", headers=auth(anna),
                             json={"project_id": project.id, "summary": "Kanalliste leer"})
    assert made.status_code == 200, made.text
    key = made.json()["ticket"]
    issue = (await db.execute(select(Issue).where(Issue.key == key))).scalar_one()
    issue.plan = "Erst die Liste laden, dann anzeigen."
    db.add(Comment(issue_id=issue.id, author_label="developer", kind="agent",
                   body="Ursache war ein leeres Profil, behoben in 0.2.0."))
    await db.commit()

    answer = await client.post(f"/bugs/{number}/draft", json={}, headers=auth(anna))
    assert answer.status_code == 200, answer.text

    asked = model[0]["messages"][1]["content"]
    assert "Ein Programm, das Geräte programmiert." in asked      # project knowledge
    assert key in asked and "Kanalliste leer" in asked            # the ticket
    assert "behoben in 0.2.0" in asked                            # its history
    assert "Erst die Liste laden" in asked                        # the plan
    assert await bugs_svc.value_of(db, number, "ticket") == key


@pytest.mark.asyncio
async def test_our_own_answer_coming_back_is_recognised(client, db, outbox):
    """Answering somebody in the same mailbox delivers our own mail back to us — once into
    Sent, once into the inbox. Neither is a report, and neither is a matter for anybody."""
    anna = await make_user(db, "anna", admin=True)
    box = await make_mailbox(db, anna)
    _, token = await make_source(db, box=box)
    number = await one_report(client, db, anna, token)
    ours = outbox[0]["message_id"]

    copy = mail(message_id=ours, subject=f"[BUG-{number}] The channel list",
                **{"from": [{"name": "", "addr": "reports@example.org"}],
                   "to": [{"name": "", "addr": "reporter@example.net"}]})

    found = await report_mail.ours(db, copy)
    assert found is not None and found.id == number
    # And it stays what it was: one entry, the one we wrote.
    posts = (await client.get(f"/bugs/{number}/posts", headers=auth(anna))).json()
    assert len(posts) == 1 and posts[0]["via"] == "web"


@pytest.mark.asyncio
async def test_a_reply_without_a_message_id_lands_once(client, db, outbox):
    """Not every mail program sets a Message-ID. Then the text within a short window decides,
    because a thread that shows every sentence twice is worse than a lost repetition."""
    anna = await make_user(db, "anna", admin=True)
    box = await make_mailbox(db, anna)
    _, token = await make_source(db, box=box)
    number = await one_report(client, db, anna, token)
    reply = mail(message_id=None, headers={"In-Reply-To": outbox[0]["message_id"]})

    artifact, _ = await report_mail.match(db, reply)
    assert await report_mail.file_reply(db, artifact, reply) is not None
    assert await report_mail.file_reply(db, artifact, reply) is None

    posts = (await db.execute(select(ReportPost).where(
        ReportPost.artifact_id == number, ReportPost.via == "mail"))).scalars().all()
    assert len(posts) == 1


@pytest.mark.asyncio
async def test_every_mail_we_send_carries_a_message_id(db):
    """Without one nothing can be filed under it afterwards — and our own spam rules count a
    missing Message-ID against the sender."""
    from app.models.mail import MailIdentity
    from app.services.mailbox import build_message

    msg = build_message(MailIdentity(account_id=1, email="ich@example.org"),
                        {"to": ["du@example.net"], "subject": "Moin", "text": "Hallo"})

    assert msg["Message-ID"].endswith("@example.org>")


@pytest.mark.asyncio
async def test_ein_entwurf_wird_ueberarbeitet_statt_neu_geschrieben(client, db, outbox, model):
    """Die zweite Runde: der Text steht schon, die Anmerkung sagt, was anders werden soll."""
    anna = await make_user(db, "anna", admin=True)
    box = await make_mailbox(db, anna)
    _, token = await make_source(db, box=box)
    await make_agent(db, anna)
    number = (await client.post("/bugs/report", json=REPORT,
                                headers={"X-Bug-Token": token})).json()["id"]

    answer = await client.post(f"/bugs/{number}/draft", headers=auth(anna), json={
        "draft": "Guten Tag, wir schauen uns das an.",
        "comments": ["kürzer", "frag nach der Version"]})

    assert answer.status_code == 200, answer.text
    system = model[0]["messages"][0]["content"]
    gefragt = model[0]["messages"][1]["content"]
    # Die Ansage, Unbeanstandetes stehen zu lassen - ohne sie schreibt das Modell jede Runde
    # einen neuen Text, und die Formulierung von vorhin ist weg.
    assert "überarbeitest einen bestehenden Entwurf" in system
    assert "Guten Tag, wir schauen uns das an." in gefragt
    # Reihenfolge bleibt: die letzte Anmerkung sticht eine frühere.
    assert gefragt.index("1. kürzer") < gefragt.index("2. frag nach der Version")


@pytest.mark.asyncio
async def test_ohne_entwurf_bleibt_es_eine_erste_fassung(client, db, outbox, model):
    anna = await make_user(db, "anna", admin=True)
    box = await make_mailbox(db, anna)
    _, token = await make_source(db, box=box)
    await make_agent(db, anna)
    number = (await client.post("/bugs/report", json=REPORT,
                                headers={"X-Bug-Token": token})).json()["id"]

    await client.post(f"/bugs/{number}/draft", json={}, headers=auth(anna))

    assert "überarbeitest einen bestehenden Entwurf" not in model[0]["messages"][0]["content"]
