"""Ein Name je Sache — und Alt-Namen brechen trotzdem nichts.

Vor dem Artefakt-Register gab es je Subjekt eine eigene Zustands-Aktion
(`set_agent_status`, `set_purchase_status`). Beide sind heute `set_status`. Die alten
Namen stehen aber weiter in veröffentlichten Versionen, und die sind unveränderlich —
laufende Instanzen hängen daran. Diese Tests halten beides fest: die Alt-Namen wirken
unverändert, und die ausgelieferten Abläufe benutzen sie nicht mehr.
"""
from app.models.enums import PurchaseStatus, TicketAgentStatus, WorkflowSubjectKind
from app.models.workflow import WorkflowInstance
from app.services import artifacts as art
from app.services.hardware_workflow import (
    build_hardware_graph, refresh_generated_definitions,
)
from app.services.workflow_actions import run_action
from app.services.workflow_seed import (
    build_acceptance, build_hardware_procurement, build_ticket_intake,
    build_ticket_lifecycle,
)
from conftest import make_asset, make_project


def _knoten(action: str, **params) -> dict:
    return {"type": "auto_action",
            "data": {"config": {"action": {"action": action, "params": params}}}}


async def _instanz(db, proj, subject, **bindung) -> WorkflowInstance:
    """Instanz mit minimaler Definition — die Aktion braucht nur die Bindung."""
    from app.models.enums import WorkflowVersionStatus
    from app.models.workflow import WorkflowDefinition, WorkflowVersion
    d = WorkflowDefinition(project_id=proj.id, key="alt", name="Alt", subject_kind=subject)
    db.add(d)
    await db.flush()
    v = WorkflowVersion(definition_id=d.id, version=1, graph={"nodes": [], "edges": []},
                        status=WorkflowVersionStatus.published)
    db.add(v)
    await db.flush()
    inst = WorkflowInstance(definition_id=d.id, version_id=v.id, project_id=proj.id,
                            subject_kind=subject, context={}, **bindung)
    db.add(inst)
    await db.flush()
    return inst


async def _ticket(db, proj):
    from app.models.enums import StatusCategory
    from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
    t = IssueType(project_id=proj.id, name="Aufgabe")
    s = WorkflowStatus(project_id=proj.id, name="To Do", category=StatusCategory.todo, order=0)
    db.add_all([t, s, IssueCounter(project_id=proj.id, last_number=0)])
    await db.commit()
    i = Issue(project_id=proj.id, number=1, key=f"{proj.key}-1", type_id=t.id, status_id=s.id,
              summary="Ein Ticket", reporter_id=1, rank="0001")
    db.add(i)
    await db.commit()
    return i


async def test_ausgelieferte_ablaeufe_kennen_keine_altnamen():
    """Der Standard-Satz darf den Übergang nicht verewigen."""
    for name, graph in (
        ("ticket_lifecycle", build_ticket_lifecycle()),
        ("acceptance", build_acceptance()),
        ("ticket_intake", build_ticket_intake()),
        ("hardware_procurement", build_hardware_procurement()),
    ):
        text = str(graph)
        assert "set_agent_status" not in text, name
        assert "set_purchase_status" not in text, name


async def test_alter_ticket_name_setzt_weiter_den_zustand(db):
    """`set_agent_status` aus einer veröffentlichten Version wirkt wie `set_status`."""
    await art.ensure_builtin_types(db)
    proj = await make_project(db, "ALT", "Alt")
    issue = await _ticket(db, proj)
    inst = await _instanz(db, proj, WorkflowSubjectKind.issue, issue_id=issue.id)

    await run_action(db, inst, _knoten("set_agent_status", status="plan_review",
                                       hold_reason="plan_split", notify=False))
    await db.commit()
    await db.refresh(issue)
    assert issue.agent_status == TicketAgentStatus.plan_review
    # `hold_reason` hieß beim Vorgänger so — der neue Weg nennt es `reason`.
    assert issue.hold_reason.value == "plan_split"
    a = await art.ensure_for_issue(db, issue)
    assert a.status_key == "plan_review"


async def test_alter_hardware_name_setzt_weiter_den_zustand(db):
    await art.ensure_builtin_types(db)
    proj = await make_project(db, "ALTH", "AltH")
    asset = await make_asset(db, "Switch", project=proj)
    inst = await _instanz(db, proj, WorkflowSubjectKind.hardware_asset,
                          hardware_asset_id=asset.id)

    await run_action(db, inst, _knoten("set_purchase_status", status="ordered"))
    await db.commit()
    await db.refresh(asset)
    assert asset.purchase_status == PurchaseStatus.ordered
    assert asset.order_date is not None
    a = await art.ensure_for_asset(db, asset)
    assert a.status_key == "ordered"


# ── Auffrischen maschinell erzeugter Beschaffungs-Ketten ─────────────────────

def _alte_form(graph: dict) -> dict:
    """Denselben Graphen in der Schreibweise von früher: flache Aktion, alter Name."""
    aus = {"nodes": [], "edges": [dict(e) for e in graph["edges"]]}
    for n in graph["nodes"]:
        n = {**n, "data": {**n["data"], "config": dict(n["data"]["config"])}}
        cfg = n["data"]["config"]
        if n["type"] == "auto_action":
            inner = cfg.pop("action")
            cfg["action"] = "set_purchase_status"
            cfg.update(inner["params"])
        aus["nodes"].append(n)
    return aus


async def test_alte_kette_wird_gehoben(db):
    """Nur die Schreibweise unterscheidet sich → auf die aktuelle Bauform heben."""
    from app.models.workflow import WorkflowDefinition, WorkflowVersion
    from app.models.enums import WorkflowVersionStatus
    from app.services.hardware_workflow import HARDWARE_DEF_KEY

    proj = await make_project(db, "HWR", "HW-Refresh")
    d = WorkflowDefinition(project_id=proj.id, key=HARDWARE_DEF_KEY, name="Beschaffung",
                           subject_kind=WorkflowSubjectKind.hardware_asset)
    db.add(d)
    await db.flush()
    from app.services.hardware_workflow import _project_steps
    v = WorkflowVersion(definition_id=d.id, version=1,
                        graph=_alte_form(build_hardware_graph(await _project_steps(db, proj.id))),
                        status=WorkflowVersionStatus.published)
    db.add(v)
    await db.flush()
    d.current_version_id = v.id
    await db.commit()

    assert await refresh_generated_definitions(db) == 1
    await db.refresh(d)
    neu = await db.get(WorkflowVersion, d.current_version_id)
    text = str(neu.graph)
    assert "set_purchase_status" not in text
    assert "set_status" in text

    # Zweiter Lauf findet nichts mehr — sonst entstünde bei jedem Start eine neue Version.
    assert await refresh_generated_definitions(db) == 0


async def test_angepasste_kette_bleibt_unberuehrt(db):
    """Wer den Ablauf inhaltlich geändert hat, darf ihn nicht beim Start verlieren."""
    from app.models.workflow import WorkflowDefinition, WorkflowVersion
    from app.models.enums import WorkflowVersionStatus
    from app.services.hardware_workflow import HARDWARE_DEF_KEY, _project_steps

    proj = await make_project(db, "HWX", "HW-Angepasst")
    d = WorkflowDefinition(project_id=proj.id, key=HARDWARE_DEF_KEY, name="Beschaffung",
                           subject_kind=WorkflowSubjectKind.hardware_asset)
    db.add(d)
    await db.flush()
    graph = _alte_form(build_hardware_graph(await _project_steps(db, proj.id)))
    graph["nodes"][1]["data"]["config"]["label"] = "Von Hand umbenannt"
    v = WorkflowVersion(definition_id=d.id, version=1, graph=graph,
                        status=WorkflowVersionStatus.published)
    db.add(v)
    await db.flush()
    d.current_version_id = v.id
    await db.commit()

    assert await refresh_generated_definitions(db) == 0
    await db.refresh(d)
    unveraendert = await db.get(WorkflowVersion, d.current_version_id)
    assert unveraendert.graph["nodes"][1]["data"]["config"]["label"] == "Von Hand umbenannt"
