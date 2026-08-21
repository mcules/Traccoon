"""The way it is about: a process calls an external place over a destination."""
import httpx
import pytest
from app.models.destination import Destination
from app.models.enums import WorkflowSubjectKind, WorkflowVersionStatus
from app.models.workflow import WorkflowDefinition, WorkflowVersion
from app.services import destinations as svc
from app.services.workflow_engine import start_workflow, drain
from conftest import make_project, make_user


@pytest.fixture
def calls(monkeypatch):
    aufzeichnung: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        aufzeichnung.append(request)
        return httpx.Response(201, json={"id": "EXT-7"})

    transport = httpx.MockTransport(handler)

    class Client(httpx.AsyncClient):
        def __init__(self, *a, **kw):
            kw.pop("verify", None)
            kw.pop("follow_redirects", None)
            super().__init__(*a, transport=transport, **kw)

    monkeypatch.setattr(svc.httpx, "AsyncClient", Client)
    return aufzeichnung


async def test_prozess_ruft_target_auf(db, calls):
    from app.core.security import encrypt_secret
    user = await make_user(db, "anna")
    proj = await make_project(db, "TST", "Test")
    db.add(Destination(name="crm", base_url="https://crm.test/api", auth_type="bearer",
                       secret_enc=encrypt_secret("t0k"), project_id=proj.id))
    await db.commit()

    graph = {
        "nodes": [
            {"id": "s", "type": "start", "position": {"x": 0, "y": 0}, "data": {"config": {}}},
            {"id": "ruf", "type": "auto_action", "position": {"x": 0, "y": 1},
             "data": {"config": {"label": "CRM anlegen", "action": {
                 "action": "http_request",
                 "params": {"destination": "crm", "method": "POST", "path": "/tickets",
                            "query": {"quelle": "traccoon"},
                            "headers": {"X-Referenz": "{{ref}}"},
                            "body": {"titel": "{{titel}}"}}}}}},
            {"id": "e", "type": "end", "position": {"x": 0, "y": 2},
             "data": {"config": {"outcome": "completed"}}},
        ],
        "edges": [
            {"id": "e1", "source": "s", "target": "ruf"},
            {"id": "e2", "source": "ruf", "target": "e"},
        ],
    }
    d = WorkflowDefinition(project_id=proj.id, key="ext", name="Extern",
                           subject_kind=WorkflowSubjectKind.standalone)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph=graph,
                        status=WorkflowVersionStatus.published)
    db.add(v)
    await db.flush()
    d.current_version_id = v.id
    await db.commit()

    inst = await start_workflow(db, d, subject_kind=WorkflowSubjectKind.standalone,
                                context={"titel": "Neue Störung", "ref": "R-9"},
                                actor_id=user.id)
    await drain()
    await db.refresh(inst)

    assert len(calls) == 1
    r = calls[0]
    assert r.method == "POST"
    assert str(r.url) == "https://crm.test/api/tickets?quelle=traccoon"
    assert r.headers["authorization"] == "Bearer t0k"          # the login from the destination
    assert r.headers["x-referenz"] == "R-9"                    # the header from the action
    assert r.read() == b'{"titel": "Neue St\\u00f6rung"}'      # the body with the inserted variable

    # The answer is available to the process.
    assert inst.context["http"]["status_code"] == 201
    assert inst.context["http"]["json"] == {"id": "EXT-7"}
    assert inst.status.value == "completed"
