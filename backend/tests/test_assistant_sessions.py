"""Unterhaltungen (Sessions) des persönlichen Assistenten.

Der Faden war endlos: alles, was ein Mensch je in den Chat getippt hat, war EINE Unterhaltung,
nur vom Kalender beschnitten. Ein neues Thema schleppte das gestrige mit, und ein
weggelegtes Thema kam nie zurück.

Geprüft wird hier vor allem das, was man nicht sieht, wenn es kaputt ist: dass die
verdichtete Erinnerung der einen Unterhaltung NICHT in die nächste gelesen wird (der
schlimmste Fehler dieser Funktion, weil er unsichtbar ist), dass der Zeiger von Telegram über
mehrere Nachrichten hält, und dass der Löschweg — der einzige zerstörende — seine Leitplanken
wirklich hat.
"""
import datetime as dt
import importlib.util
import pathlib

import pytest
from sqlalchemy import select

from app.models.assistant import AssistantSession, AssistantTask, ChatSummary
from app.models.enums import WorkflowSubjectKind, WorkflowVersionStatus
from app.models.notification import Notification
from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowVersion
from app.services import assistant_sessions as sessions
from app.services.assistant_inbox import create_chat_task
from app.services.workflow_actions import run_action
from app.worker import __main__ as worker

from conftest import auth, make_user

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def anna(db):
    return await make_user(db, "anna")


@pytest.fixture(autouse=True)
def no_real_model(monkeypatch):
    """Kein Test spricht mit einem echten Modell; die Verdichtung wird je Test gesetzt."""
    async def fake_load_agent(*a, **kw):
        class A:
            role = name = "assistent"
            provider, model = "claude_code", "sonnet"
        return A()

    async def fake_tokens(*a, **kw):
        return {}, {}

    monkeypatch.setattr(worker, "_load_agent", fake_load_agent)
    monkeypatch.setattr(worker, "_build_tokens", fake_tokens)


def _mock_aux(monkeypatch, text):
    async def fake_aux(*a, **kw):
        fake_aux.seen = kw.get("messages", [{}])[0].get("content", "")
        return text
    monkeypatch.setattr("app.worker.aux.aux_chat", fake_aux)
    return fake_aux


async def _msg(db, anna, session, question: str, answer: str = "ok", *,
               status: str = "done", agent: str | None = None) -> AssistantTask:
    meta = {"chat_text": question}
    if agent:
        meta["agent"] = agent
    t = AssistantTask(owner_user_id=anna.id, kind="chat", title=question[:200], status=status,
                      result=answer, meta=meta,
                      session_id=session.id if session is not None else None)
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return t


# ── 1. Der Titel kommt aus der ersten Nachricht ──────────────────────────────

async def test_the_first_message_becomes_the_title(client, db, anna):
    r = await client.post("/assistant/chat", json={
        "text": "Wann läuft die Kfz-Versicherung ab und was kostet der Wechsel?"},
        headers=auth(anna))
    assert r.status_code == 200, r.text
    sid = r.json()["session_id"]
    assert sid

    s = await db.get(AssistantSession, sid)
    assert s.title.startswith("Wann läuft die Kfz-Versicherung ab")
    # An der Wortgrenze geschnitten, nicht mitten im Wort.
    assert len(s.title) <= sessions.TITLE_MAX + 1 and not s.title.rstrip("…").endswith(" ")


async def test_an_explicit_title_wins_over_the_first_message(client, db, anna):
    r = await client.post("/assistant/sessions", json={"title": "Versicherungen"},
                          headers=auth(anna))
    assert r.status_code == 201, r.text
    sid = r.json()["id"]

    await client.post("/assistant/chat", json={"text": "Ganz was anderes", "session_id": sid},
                      headers=auth(anna))
    s = await db.get(AssistantSession, sid)
    assert s.title == "Versicherungen"


async def test_a_long_first_word_is_truncated_not_dropped():
    """Eine nackte URL hat keine Wortgrenze — dann lieber abgeschnitten als leer."""
    title = sessions.title_from("https://example.com/" + "a" * 200)
    assert title.startswith("https://example.com/") and len(title) <= sessions.TITLE_MAX + 1


# ── 2. Die Unterhaltungen sehen einander nicht ───────────────────────────────

async def test_one_session_knows_nothing_of_the_other(db, anna, monkeypatch):
    _mock_aux(monkeypatch, "- egal")
    a = await sessions.create(db, anna.id, title="A")
    b = await sessions.create(db, anna.id, title="B")
    await _msg(db, anna, a, "Sache aus A")
    await _msg(db, anna, b, "Sache aus B")

    history = await worker._chat_history(db, await _msg(db, anna, b, "Weiter", ""))
    assert [w["body"] for w in history if w["role"] == "user"] == ["Sache aus B"]


async def test_the_summary_of_one_session_never_reaches_the_other(db, anna, monkeypatch):
    """Der schlimmste Fehler dieser Funktion, weil er unsichtbar ist: der Agent „erinnert"
    sich an etwas, das in DIESER Unterhaltung nie gesagt wurde."""
    _mock_aux(monkeypatch, "- Anna zieht nach Bremen um")
    a = await sessions.create(db, anna.id, title="Umzug")
    for i in range(16):
        await _msg(db, anna, a, f"Frage {i}")
    history = await worker._chat_history(db, await _msg(db, anna, a, "Weiter", ""))
    assert "Bremen" in history[0]["body"]

    summary = (await db.execute(select(ChatSummary))).scalars().one()
    assert summary.session_id == a.id

    b = await sessions.create(db, anna.id, title="Etwas völlig anderes")
    history_b = await worker._chat_history(db, await _msg(db, anna, b, "Hallo", ""))
    assert history_b == []
    assert not any("Bremen" in w["body"] for w in history_b)


async def test_a_session_arrives_whole_even_after_weeks(db, anna, monkeypatch):
    """Der Kalender schneidet nicht mehr: wer eine Unterhaltung nach drei Wochen wieder
    aufnimmt, will sie ganz vorfinden — genau dafür lädt man sie."""
    _mock_aux(monkeypatch, "- egal")
    a = await sessions.create(db, anna.id, title="Lange her")
    old = await _msg(db, anna, a, "Das war vor einem Monat")
    old.created_at = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(days=30)
    await db.commit()

    history = await worker._chat_history(db, await _msg(db, anna, a, "Wo waren wir?", ""))
    assert [w["body"] for w in history if w["role"] == "user"] == ["Das war vor einem Monat"]


async def test_a_task_without_a_session_keeps_the_old_window(db, anna, monkeypatch):
    """Posteingang und Webhook-Läufe laufen NICHT über Unterhaltungen."""
    _mock_aux(monkeypatch, "- egal")
    old = await _msg(db, anna, None, "Uralt")
    old.created_at = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(days=30)
    await _msg(db, anna, None, "Neulich")
    await db.commit()

    history = await worker._chat_history(db, await _msg(db, anna, None, "Weiter", ""))
    assert [w["body"] for w in history if w["role"] == "user"] == ["Neulich"]


# ── 3. Schließen, wieder öffnen, weiterreden ─────────────────────────────────

async def test_closing_takes_it_out_of_the_list_and_reopen_brings_it_back(client, db, anna):
    sid = (await client.post("/assistant/sessions", json={"title": "Steuer"},
                             headers=auth(anna))).json()["id"]

    assert [s["id"] for s in (await client.get("/assistant/sessions",
                                               headers=auth(anna))).json()] == [sid]

    r = await client.post(f"/assistant/sessions/{sid}/close", headers=auth(anna))
    assert r.status_code == 200 and r.json()["closed_at"]
    assert (await client.get("/assistant/sessions", headers=auth(anna))).json() == []
    assert [s["id"] for s in (await client.get("/assistant/sessions?closed=1",
                                               headers=auth(anna))).json()] == [sid]

    # Geschlossen heißt nicht stumm: wer sie wieder lädt, redet in ihr weiter.
    r = await client.post("/assistant/chat", json={"text": "doch noch was", "session_id": sid},
                          headers=auth(anna))
    assert r.status_code == 200 and r.json()["session_id"] == sid

    r = await client.post(f"/assistant/sessions/{sid}/reopen", headers=auth(anna))
    assert r.status_code == 200 and r.json()["closed_at"] is None
    assert [s["id"] for s in (await client.get("/assistant/sessions",
                                               headers=auth(anna))).json()] == [sid]


async def test_the_list_says_where_something_is_still_running(client, db, anna):
    sid = (await client.post("/assistant/sessions", json={"title": "Läuft"},
                             headers=auth(anna))).json()["id"]
    s = await db.get(AssistantSession, sid)
    await _msg(db, anna, s, "arbeitet noch", status="running")

    row = (await client.get("/assistant/sessions", headers=auth(anna))).json()[0]
    assert row["running"] is True and row["message_count"] == 1


async def test_rename(client, db, anna):
    sid = (await client.post("/assistant/sessions", json={}, headers=auth(anna))).json()["id"]
    r = await client.patch(f"/assistant/sessions/{sid}", json={"title": "Neuer Name"},
                           headers=auth(anna))
    assert r.status_code == 200 and r.json()["title"] == "Neuer Name"
    assert (await client.patch(f"/assistant/sessions/{sid}", json={"title": "  "},
                               headers=auth(anna))).status_code == 400


async def test_a_foreign_session_is_not_found(client, db, anna):
    berta = await make_user(db, "berta")
    sid = (await client.post("/assistant/sessions", json={}, headers=auth(berta))).json()["id"]
    assert (await client.post(f"/assistant/sessions/{sid}/close",
                              headers=auth(anna))).status_code == 404
    assert (await client.post("/assistant/chat", json={"text": "hi", "session_id": sid},
                              headers=auth(anna))).status_code == 404


# ── 4. Der Backfill der Migration ────────────────────────────────────────────

def _migration():
    """Genau der Code, der in der Migration steht — nicht eine Nachbildung davon."""
    path = (pathlib.Path(__file__).resolve().parent.parent
            / "alembic" / "versions" / "c9d4b17a3e58_assistant_sessions.py")
    spec = importlib.util.spec_from_file_location("mig_sessions", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_the_backfill_puts_the_existing_history_where_people_expect_it(db, anna):
    """Niemand verliert seine Historie, und sie landet dort, wo er sie sucht: als die
    Unterhaltung, in der er stand."""
    berta = await make_user(db, "berta")
    for i in range(3):
        await _msg(db, anna, None, f"Assistent {i}")
    await _msg(db, anna, None, "Game matter", agent="game-operator")
    await _msg(db, berta, None, "Bertas Sache")
    db.add(ChatSummary(owner_user_id=anna.id, agent="assistent", text="- alte Erinnerung"))
    await db.commit()

    created = await db.run_sync(lambda sync: _migration().backfill(sync.connection()))
    await db.commit()
    # Anna hat zwei Agenten, Berta einen — ein Agent, eine Unterhaltung.
    assert created == 3

    rows = (await db.execute(select(AssistantSession))).scalars().all()
    assert {s.title for s in rows} == {"Bisherige Unterhaltung"}

    tasks = (await db.execute(select(AssistantTask))).scalars().all()
    assert all(t.session_id is not None for t in tasks)
    annas_assistant = {t.session_id for t in tasks
                       if t.owner_user_id == anna.id and "agent" not in (t.meta or {})}
    assert len(annas_assistant) == 1

    summary = (await db.execute(select(ChatSummary))).scalars().one()
    assert summary.session_id == annas_assistant.pop()

    # Und die Zeiten stehen so, wie die Liste sie sortiert.
    s = await db.get(AssistantSession, summary.session_id)
    assert s.last_message_at is not None and s.created_at is not None


# ── 5. + 6. Der Kanal-Zeiger ─────────────────────────────────────────────────

async def test_sending_without_a_session_follows_the_pointer_and_creates_one(client, db, anna):
    first = (await client.post("/assistant/chat", json={"text": "eins"},
                               headers=auth(anna))).json()
    assert first["session_id"]

    # Zweite Nachricht ohne Angabe: dieselbe Unterhaltung, weil der Zeiger sie hält.
    second = (await client.post("/assistant/chat", json={"text": "zwei"},
                                headers=auth(anna))).json()
    assert second["session_id"] == first["session_id"]

    pointer = await sessions.pointer(db, anna.id, "web")
    assert pointer is not None and pointer.session_id == first["session_id"]


async def test_switching_in_the_browser_moves_the_pointer(client, db, anna):
    a = (await client.post("/assistant/chat", json={"text": "in A"},
                           headers=auth(anna))).json()["session_id"]
    b = (await client.post("/assistant/sessions", json={"title": "B"},
                           headers=auth(anna))).json()["id"]
    await client.post("/assistant/chat", json={"text": "in B", "session_id": b},
                      headers=auth(anna))

    assert (await sessions.pointer(db, anna.id, "web")).session_id == b
    # Und die nächste Nachricht ohne Angabe landet in B, nicht wieder in A.
    assert (await client.post("/assistant/chat", json={"text": "noch in B"},
                              headers=auth(anna))).json()["session_id"] == b != a


async def test_the_telegram_pointer_survives_between_messages(db, anna):
    """Laden, senden, nochmal senden — beide Nachrichten liegen in der geladenen."""
    target = await sessions.create(db, anna.id, title="Geladen")
    other = await sessions.create(db, anna.id, title="Andere")
    await sessions.load(db, anna.id, "telegram", target.id)

    one = await create_chat_task(db, anna.id, "erste", "4711")
    two = await create_chat_task(db, anna.id, "zweite", "4711")
    assert one.session_id == two.session_id == target.id
    assert other.id != target.id

    # Der Web-Zeiger ist davon unberührt: zwei Kanäle, zwei Zeiger.
    assert await sessions.pointer(db, anna.id, "web") is None


async def test_telegram_without_a_pointer_starts_a_conversation(db, anna):
    task = await create_chat_task(db, anna.id, "Hallo Assistent", "4711")
    assert task.session_id
    s = await db.get(AssistantSession, task.session_id)
    assert s.title == "Hallo Assistent" and s.agent == "assistent"


async def test_a_named_agent_does_not_land_in_the_assistants_conversation(db, anna):
    """A specialist agent has a conversation of its own; mixing the two poisoned both."""
    loaded = await sessions.create(db, anna.id, title="Assistent")
    await sessions.load(db, anna.id, "telegram", loaded.id)

    task = await create_chat_task(db, anna.id, "Angriff?", "4711", agent="game-operator")
    assert task.session_id != loaded.id
    assert (await db.get(AssistantSession, task.session_id)).agent == "game-operator"


# ── 7. Löschen ist eine Ablauf-Aktion ────────────────────────────────────────

async def _instance(db, anna, context: dict | None = None) -> WorkflowInstance:
    d = WorkflowDefinition(project_id=None, key="aufraeumen", name="Aufräumen",
                           created_by=anna.id, subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph={"nodes": [], "edges": []},
                        status=WorkflowVersionStatus.published)
    db.add(v)
    await db.flush()
    inst = WorkflowInstance(definition_id=d.id, version_id=v.id,
                            subject_kind=WorkflowSubjectKind.standalone,
                            context=context or {}, started_by=anna.id)
    db.add(inst)
    await db.flush()
    return inst


def _node(**params) -> dict:
    return {"id": "n", "type": "auto_action",
            "data": {"config": {"action": {"action": "assistant_session", "params": params}}}}


async def _aged(db, anna, title: str, days: int, *, closed: bool = True) -> AssistantSession:
    s = await sessions.create(db, anna.id, title=title)
    when = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(days=days)
    s.last_message_at = when
    s.created_at = when
    if closed:
        s.closed_at = when
    await db.commit()
    return s


async def test_the_delete_action_refuses_a_running_session(db, anna):
    s = await _aged(db, anna, "läuft noch", 200)
    await _msg(db, anna, s, "arbeitet", status="running")
    inst = await _instance(db, anna)

    result = await run_action(db, inst, _node(op="delete", session_id=s.id))
    assert result["deleted"] == 0
    assert await db.get(AssistantSession, s.id) is not None


async def test_a_sweep_never_touches_an_open_conversation(db, anna):
    """Einen Ablauf, in dem gerade jemand sitzt, wegzufegen wäre unverzeihlich."""
    open_one = await _aged(db, anna, "offen und alt", 300, closed=False)
    closed_one = await _aged(db, anna, "geschlossen und alt", 300)
    inst = await _instance(db, anna)

    result = await run_action(db, inst, _node(op="delete", older_than_days=90, keep_last=0))
    assert result["ids"] == [closed_one.id]
    assert await db.get(AssistantSession, open_one.id) is not None


async def test_keep_last_protects_the_most_recent(db, anna):
    old = [await _aged(db, anna, f"alt {i}", 300 - i) for i in range(5)]
    inst = await _instance(db, anna)

    result = await run_action(db, inst, _node(op="delete", older_than_days=90, keep_last=2))
    # Die beiden jüngsten (die zuletzt angelegten) bleiben stehen.
    assert set(result["ids"]) == {s.id for s in old[:3]}
    assert await db.get(AssistantSession, old[4].id) is not None


async def test_deleting_leaves_no_bell_that_opens_nothing(db, anna):
    s = await _aged(db, anna, "weg damit", 300)
    task = await _msg(db, anna, s, "eine Nachricht")
    db.add(Notification(user_id=anna.id, assistant_task_id=task.id, kind="assistant_review",
                        title="Freigabe?"))
    db.add(ChatSummary(owner_user_id=anna.id, agent="assistent", session_id=s.id,
                       text="- Erinnerung"))
    await db.commit()
    inst = await _instance(db, anna)

    result = await run_action(db, inst, _node(op="delete", session_id=s.id))
    await db.commit()
    assert result["deleted"] == 1
    assert (await db.execute(select(Notification))).scalars().all() == []
    assert (await db.execute(select(AssistantTask))).scalars().all() == []
    assert (await db.execute(select(ChatSummary))).scalars().all() == []


async def test_a_selector_without_an_owner_does_nothing(db, anna):
    await _aged(db, anna, "alt", 300)
    inst = await _instance(db, anna)
    inst.started_by = None
    await db.flush()

    result = await run_action(db, inst, _node(op="delete", older_than_days=90))
    assert result["deleted"] == 0 and "owner" in result["reason"]


async def test_create_and_close_as_an_action(db, anna):
    inst = await _instance(db, anna)
    result = await run_action(db, inst, _node(op="create", title="Aus dem Ablauf"))
    assert result["created"] is True
    # Die Nummer steht im Kontext, damit ein folgender Knoten hineinschreiben kann.
    assert inst.context["session"]["id"] == result["session_id"]

    sid = result["session_id"]
    closed = await run_action(db, inst, _node(op="close", session_id=sid))
    assert closed["ids"] == [sid]
    assert (await db.get(AssistantSession, sid)).closed_at is not None


async def test_the_session_events_are_reported(db, anna, monkeypatch):
    seen = []

    async def fake_emit(db_, event, **kw):
        seen.append((event, kw.get("payload", {})))
        return []

    monkeypatch.setattr("app.services.events.emit", fake_emit)
    s = await sessions.create(db, anna.id, title="Ereignisse")
    await sessions.close(db, s)
    await sessions.delete(db, [s])

    assert [e for e, _ in seen] == ["assistant.session_created", "assistant.session_closed",
                                    "assistant.session_deleted"]
    assert seen[0][1]["session"]["title"] == "Ereignisse"


# ── 8. Der Token mit dem assistant-Scope erreicht alles davon ────────────────

async def test_an_assistant_token_reaches_every_session_endpoint(client, db, anna):
    """Sie liegen alle unter `/assistant/*`, deshalb braucht es keinen neuen Scope."""
    minted = await client.post("/me/tokens", json={"name": "Obsidian",
                                                   "scopes": ["assistant"]},
                               headers=auth(anna))
    assert minted.status_code == 201, minted.text
    head = {"Authorization": f"Bearer {minted.json()['token']}"}

    assert (await client.get("/assistant/sessions", headers=head)).status_code == 200
    created = await client.post("/assistant/sessions", json={"title": "Über den Token"},
                                headers=head)
    assert created.status_code == 201, created.text
    sid = created.json()["id"]
    assert (await client.patch(f"/assistant/sessions/{sid}", json={"title": "Umbenannt"},
                               headers=head)).status_code == 200
    assert (await client.post(f"/assistant/sessions/{sid}/close",
                              headers=head)).status_code == 200
    assert (await client.post(f"/assistant/sessions/{sid}/reopen",
                              headers=head)).status_code == 200
    assert (await client.post("/assistant/chat", json={"text": "hallo", "session_id": sid},
                              headers=head)).status_code == 200
    assert (await client.get(f"/assistant/chat?session_id={sid}",
                             headers=head)).status_code == 200


# ── 9. Wie voll die nächste Nachricht wird ───────────────────────────────────
#
# Ein Chat-Client hat keinen anderen Weg, danach zu fragen: die Zahlen liegen hinter
# `/office/*` und den Modell-Endpunkten, die der `assistant`-Scope bewusst nicht erreicht.
# Also stehen sie an der Unterhaltung.

async def _run(db, session, task, *, input_tokens: int, model: str = "claude-sonnet-5",
               provider: str = "claude_code", status: str = "success"):
    from app.models.agents import Run
    run = Run(owner_id=session.owner_user_id, task_id=f"assistant-{task.id}",
              agent="assistent", provider=provider, model=model, status=status,
              input_tokens=input_tokens, output_tokens=100)
    db.add(run)
    await db.flush()
    task.run_id = run.id
    await db.commit()
    return run


async def _window(db, *, provider="claude_code", model="claude-sonnet-5", tokens=200000):
    from app.models.ops import ProviderModel
    db.add(ProviderModel(provider=provider, model=model, context_tokens=tokens))
    await db.commit()


async def test_a_session_that_never_ran_reports_nothing(client, db, anna):
    sid = (await client.post("/assistant/sessions", json={"title": "Frisch"},
                             headers=auth(anna))).json()["id"]
    row = next(s for s in (await client.get("/assistant/sessions",
                                            headers=auth(anna))).json() if s["id"] == sid)
    # Nicht 0: eine Null läse sich als „leeres Fenster" statt als „nie gemessen".
    assert row["context"] is None


async def test_the_newest_run_counts_not_the_largest(client, db, anna):
    """Die Frage ist „wie voll war es zuletzt". Ein Mittelwert glättete genau die Spitze weg,
    auf die es ankommt — und der grösste Lauf ist nicht der letzte."""
    await _window(db)
    s = await sessions.create(db, anna.id, title="Läuft")
    await _run(db, s, await _msg(db, anna, s, "erste"), input_tokens=90000)
    await _run(db, s, await _msg(db, anna, s, "zweite"), input_tokens=12480)

    row = next(r for r in (await client.get("/assistant/sessions",
                                            headers=auth(anna))).json() if r["id"] == s.id)
    ctx = row["context"]
    assert ctx["input_tokens"] == 12480
    assert ctx["model"] == "claude-sonnet-5"
    assert ctx["context_tokens"] == 200000 and ctx["pct"] == 6
    assert ctx["measured_at"]


async def test_a_running_run_is_not_the_answer_yet(client, db, anna):
    await _window(db)
    s = await sessions.create(db, anna.id, title="Mittendrin")
    await _run(db, s, await _msg(db, anna, s, "fertig"), input_tokens=5000)
    await _run(db, s, await _msg(db, anna, s, "läuft noch", status="running"),
               input_tokens=99999, status="running")

    row = next(r for r in (await client.get("/assistant/sessions",
                                            headers=auth(anna))).json() if r["id"] == s.id)
    assert row["context"]["input_tokens"] == 5000


async def test_an_unknown_model_leaves_the_window_empty(client, db, anna):
    """Ein falscher Nenner ist schlimmer als gar kein Prozentwert — er sieht amtlich aus."""
    s = await sessions.create(db, anna.id, title="Fremdes Modell")
    await _run(db, s, await _msg(db, anna, s, "hallo"), input_tokens=4321,
               model="qwen3.6-irgendwas")

    row = next(r for r in (await client.get("/assistant/sessions",
                                            headers=auth(anna))).json() if r["id"] == s.id)
    ctx = row["context"]
    assert ctx["context_tokens"] is None and ctx["pct"] is None
    # Der Rest der Zeile steht trotzdem.
    assert ctx["input_tokens"] == 4321 and ctx["model"] == "qwen3.6-irgendwas"


async def test_a_short_conversation_travels_whole(client, db, anna):
    """Solange nichts verdichtet ist, reist alles wörtlich — und die Zahl sagt genau das."""
    await _window(db)
    s = await sessions.create(db, anna.id, title="Kurz")
    for i in range(5):
        await _msg(db, anna, s, f"Frage {i}")
    await _run(db, s, await _msg(db, anna, s, "und jetzt", ""), input_tokens=3000)

    ctx = next(r for r in (await client.get("/assistant/sessions",
                                            headers=auth(anna))).json()
               if r["id"] == s.id)["context"]
    assert ctx["verbatim_exchanges"] == 6 and ctx["summary_chars"] == 0


async def test_a_long_conversation_shows_why_it_plateaus(client, db, anna, monkeypatch):
    """Der Prozentwert allein verschweigt das Verfahren: dieser Kontext wird verdichtet, er
    läuft also nicht voll, sondern läuft ein. Wer eine Zahl beobachtet, die nie 100 erreicht,
    darf sehen, warum — deshalb stehen beide Zahlen da.

    `verbatim_exchanges` ist der GEDECKELTE Wert, also das, was wirklich in den Prompt geht.
    Er fällt durch eine Verdichtung deshalb nicht: die Deckelung nimmt sie schon vorweg. Was
    sich sichtbar bewegt, ist `summary_chars` — von nichts auf etwas."""
    _mock_aux(monkeypatch, "- was bisher geschah")
    await _window(db)
    s = await sessions.create(db, anna.id, title="Lang")
    for i in range(16):
        await _msg(db, anna, s, f"Frage {i}")
    task = await _msg(db, anna, s, "und jetzt", "")
    await _run(db, s, task, input_tokens=30000)

    before = next(r for r in (await client.get("/assistant/sessions",
                                               headers=auth(anna))).json()
                  if r["id"] == s.id)["context"]
    from app.worker.__main__ import CHAT_HISTORY_MAX
    assert before["verbatim_exchanges"] == CHAT_HISTORY_MAX      # gedeckelt, nicht 17
    assert before["summary_chars"] == 0

    await worker._chat_history(db, task)      # verdichtet den älteren Teil

    after = next(r for r in (await client.get("/assistant/sessions",
                                              headers=auth(anna))).json()
                 if r["id"] == s.id)["context"]
    # Danach steht die Erinnerung, und offen sind nur noch die jungen Wortwechsel. Die Zahl
    # fällt dabei NICHT — sie sägt: vorher war sie gedeckelt (die Deckelung nimmt die
    # Verdichtung vorweg), danach stehen wieder etwas mehr offen als der Deckel. Was sich
    # eindeutig bewegt, ist die Erinnerung: von nichts auf etwas.
    from app.worker.__main__ import CHAT_SUMMARY_BLOCK
    assert after["summary_chars"] == len("- was bisher geschah")
    assert 0 < after["verbatim_exchanges"] <= CHAT_HISTORY_MAX + CHAT_SUMMARY_BLOCK
    # Und die Erinnerung gehört DIESER Unterhaltung: ohne den Sitzungs-Schnitt läse die
    # Zahl den Stand einer fremden mit.
    other = await sessions.create(db, anna.id, title="Andere")
    await _run(db, other, await _msg(db, anna, other, "hallo"), input_tokens=100)
    fremd = next(r for r in (await client.get("/assistant/sessions",
                                              headers=auth(anna))).json()
                 if r["id"] == other.id)["context"]
    assert fremd["summary_chars"] == 0


async def test_the_list_does_not_query_per_session(client, db, anna):
    """Diese Liste fragt jeder offene Chat im Sekundentakt ab. Eine Unterabfrage je Zeile
    wäre bei zehn Unterhaltungen zehnmal dieselbe Arbeit."""
    await _window(db)
    counted = {"n": 0}
    import sqlalchemy

    @sqlalchemy.event.listens_for(db.sync_session, "do_orm_execute")
    def _count(_state):
        counted["n"] += 1

    for i in range(3):
        s = await sessions.create(db, anna.id, title=f"A{i}")
        await _run(db, s, await _msg(db, anna, s, "x"), input_tokens=1000 + i)
    counted["n"] = 0
    three = (await client.get("/assistant/sessions", headers=auth(anna))).json()
    for_three = counted["n"]

    for i in range(3, 9):
        s = await sessions.create(db, anna.id, title=f"A{i}")
        await _run(db, s, await _msg(db, anna, s, "x"), input_tokens=1000 + i)
    counted["n"] = 0
    nine = (await client.get("/assistant/sessions", headers=auth(anna))).json()

    assert len(three) == 3 and len(nine) == 9
    assert all(r["context"] for r in nine)
    # Dreimal so viele Unterhaltungen, gleich viele Abfragen.
    assert counted["n"] == for_three
