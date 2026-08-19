"""One write path for states, proven in the source and in the behaviour.

Before, 21 places set `agent_status` directly, and the artifact row then lagged behind until
the next reconciliation. Now everything goes over `set_ticket_status`/`set_asset_status`,
which write both in one go. The reconciliation stays as a net but is no longer the mechanism.
"""
import pathlib
import re

import pytest
from app.models.artifact import Artifact
from app.models.enums import HoldReason, PurchaseStatus, StatusCategory, TicketAgentStatus
from app.models.ticket import Issue, IssueCounter, IssueType, WorkflowStatus
from app.services import artifacts as art
from sqlalchemy import select
from conftest import make_asset, make_project

APP = pathlib.Path(__file__).resolve().parent.parent / "app"
# Direct assignment is allowed here: the implementation itself respectively the creation of a
# ticket whose artifact row comes into being immediately afterwards.
ERLAUBT = {"services/artifacts.py", "services/workflow_actions.py"}


def test_niemand_setzt_den_zustand_an_der_zentrale_vorbei():
    treffer = []
    for datei in APP.rglob("*.py"):
        rel = str(datei.relative_to(APP))
        if rel in ERLAUBT:
            continue
        for nr, zeile in enumerate(datei.read_text().splitlines(), 1):
            # Assignment, not comparison: "= x" yes, "== x" and "!= x" no.
            if re.search(r"\.(agent_status|purchase_status)\s*=(?!=)", zeile):
                treffer.append(f"{rel}:{nr}: {zeile.strip()}")
    assert not treffer, (
        "The state is set directly instead of over set_ticket_status/set_asset_status, "
        "the artifact row would drift apart:\n" + "\n".join(treffer))


@pytest.fixture
async def register(db):
    await art.ensure_builtin_types(db)


async def _ticket(db, proj) -> Issue:
    t = IssueType(project_id=proj.id, name="Aufgabe")
    db.add(t)
    for i, (name, kat) in enumerate([("To Do", StatusCategory.todo),
                                     ("In Arbeit", StatusCategory.in_progress),
                                     ("Warten", StatusCategory.in_progress)]):
        db.add(WorkflowStatus(project_id=proj.id, name=name, category=kat, order=i))
    db.add(IssueCounter(project_id=proj.id, last_number=0))
    await db.commit()
    s = (await db.execute(select(WorkflowStatus).where(
        WorkflowStatus.project_id == proj.id, WorkflowStatus.name == "To Do"))).scalar_one()
    i = Issue(project_id=proj.id, number=1, key="TST-1", type_id=t.id, status_id=s.id,
              summary="Ein Ticket", reporter_id=1, rank="0001")
    db.add(i)
    await db.commit()
    return i


async def test_ein_aufruf_setzt_zustand_board_und_artefakt(db, register):
    proj = await make_project(db, "TST", "Test")
    issue = await _ticket(db, proj)

    await art.set_ticket_status(db, issue, TicketAgentStatus.hold, reason=HoldReason.merge)
    await db.commit()

    a = await db.get(Artifact, issue.artifact_id)
    spalte = await db.get(WorkflowStatus, issue.status_id)
    assert issue.agent_status == TicketAgentStatus.hold
    assert issue.hold_reason == HoldReason.merge
    assert a.status_key == "hold"           # Artefakt sofort deckungsgleich
    assert spalte.name == "Warten"          # Board ebenso


async def test_zustand_zuruecknehmen_laesst_das_board_stehen(db, register):
    """The agent was pulled off: no state any more, but the ticket does not jump back."""
    proj = await make_project(db, "TST", "Test")
    issue = await _ticket(db, proj)
    await art.set_ticket_status(db, issue, TicketAgentStatus.in_progress)
    await db.commit()
    vorher = issue.status_id

    await art.set_ticket_status(db, issue, None, board=False)
    await db.commit()
    a = await db.get(Artifact, issue.artifact_id)
    assert issue.agent_status is None and issue.hold_reason is None
    assert issue.status_id == vorher
    assert a.status_key == ""


async def test_hardware_zustand_fuehrt_die_datumsfelder_mit(db, register):
    proj = await make_project(db, "TST", "Test")
    asset = await make_asset(db, "Switch", project=proj)
    await art.ensure_for_asset(db, asset)
    await db.commit()

    await art.set_asset_status(db, asset, "ordered")
    await db.commit()
    a = await db.get(Artifact, asset.artifact_id)
    assert asset.purchase_status == PurchaseStatus.ordered
    assert asset.order_date is not None
    assert a.status_key == "ordered"


async def test_abgleich_findet_nichts_mehr_zu_tun(db, register):
    """When the one path is used, the reconciliation has nothing to catch up."""
    proj = await make_project(db, "TST", "Test")
    issue = await _ticket(db, proj)
    await art.set_ticket_status(db, issue, TicketAgentStatus.to_test)
    await db.commit()

    assert (await art.reconcile(db))["tickets_angeglichen"] == 0


async def test_laufender_agent_steht_nie_auf_warten(db):
    """The rule without an exception: if an agent is running for a ticket, it is "in progress".

    A run starts over several paths (process step, review round in the worker, follow-up of
    the reliable queue after a restart), and only the first goes through the graph. On
    2026-08-07 two agents were working after a worker restart while the board showed
    "waiting", because nobody had touched the state.
    """
    from app.models.agents import Run
    from app.models.enums import HoldReason, TicketAgentStatus
    from app.services.artifacts import reconcile
    from test_lifecycle_process import _projekt_mit_ticket

    _, _, issue, _ = await _projekt_mit_ticket(db, TicketAgentStatus.hold)
    issue.hold_reason = HoldReason.merge
    db.add(Run(issue_id=issue.id, agent="developer", phase="execution", status="running"))
    await db.commit()

    await reconcile(db)
    await db.refresh(issue)

    assert issue.agent_status == TicketAgentStatus.in_progress
    assert issue.hold_reason is None
    assert issue.agent_working is True


async def test_planungslauf_steht_auf_planung(db):
    """The plan run belongs on `planning`, not on `in_progress`: both land in the board under
    "in progress", but the state should name the phase."""
    from app.models.agents import Run
    from app.models.enums import TicketAgentStatus
    from app.services.artifacts import reconcile
    from test_lifecycle_process import _projekt_mit_ticket

    _, _, issue, _ = await _projekt_mit_ticket(db, TicketAgentStatus.failed)
    db.add(Run(issue_id=issue.id, agent="architect", phase="planning", status="running"))
    await db.commit()

    await reconcile(db)
    await db.refresh(issue)
    assert issue.agent_status == TicketAgentStatus.planning


async def test_beendeter_lauf_ruehrt_den_zustand_nicht_an(db):
    """The counter-check: a FINISHED run is no reason to touch a waiting ticket; otherwise the
    reconciliation would tear every completed ticket back into the work."""
    import datetime as dt

    from app.models.agents import Run
    from app.models.enums import TicketAgentStatus
    from app.services.artifacts import reconcile
    from test_lifecycle_process import _projekt_mit_ticket

    _, _, issue, _ = await _projekt_mit_ticket(db, TicketAgentStatus.hold)
    db.add(Run(issue_id=issue.id, agent="developer", phase="execution", status="success",
               finished_at=dt.datetime.now(tz=dt.timezone.utc)))
    await db.commit()

    await reconcile(db)
    await db.refresh(issue)
    assert issue.agent_status == TicketAgentStatus.hold


async def test_stehengebliebene_spalte_wird_nachgezogen(db):
    """The state can be right and the COLUMN still stand wrongly.

    ABC-32 on 2026-08-07: the ticket was continued out of the disturbance branch, the agent
    ran with `in_progress`, and the board column stayed on "waiting" because it was set while
    parking and never touched again. A reconciliation that only checks `agent_status` sees nothing wrong.
    """
    from app.models.agents import Run
    from app.models.enums import TicketAgentStatus
    from app.services.artifacts import reconcile
    from test_lifecycle_process import _projekt_mit_ticket

    _, _, issue, stats = await _projekt_mit_ticket(db, TicketAgentStatus.in_progress)
    issue.status_id = stats["Warten"].id           # Spalte hinkt hinterher
    db.add(Run(issue_id=issue.id, agent="developer", phase="execution", status="running"))
    await db.commit()

    await reconcile(db)
    await db.refresh(issue)

    assert issue.status_id == stats["In Arbeit"].id
    assert issue.agent_status == TicketAgentStatus.in_progress
    assert issue.agent_working is True


async def test_abgenommenes_ticket_bleibt_fertig(db):
    """Counter-check: a manually accepted ticket is not pulled back into the work by the
    reconciliation, even when a run is still trailing."""
    from app.models.agents import Run
    from app.models.enums import TicketAgentStatus
    from app.services.artifacts import reconcile
    from test_lifecycle_process import _projekt_mit_ticket

    _, _, issue, stats = await _projekt_mit_ticket(db, TicketAgentStatus.done)
    issue.status_id = stats["Fertig"].id
    db.add(Run(issue_id=issue.id, agent="developer", phase="execution", status="running"))
    await db.commit()

    await reconcile(db)
    await db.refresh(issue)

    assert issue.status_id == stats["Fertig"].id
    assert issue.agent_status == TicketAgentStatus.done
