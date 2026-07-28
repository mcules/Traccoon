"""Ein Schreibweg für Zustände — nachgewiesen am Quelltext und im Verhalten.

Vorher setzten 21 Stellen `agent_status` direkt; die Artefakt-Zeile lief dann bis zum
nächsten Abgleich hinterher. Jetzt geht alles über `set_ticket_status`/`set_asset_status`,
die beides in einem Zug schreiben. Der Abgleich bleibt als Netz, ist aber nicht mehr der
Mechanismus.
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
# Hier darf direkt zugewiesen werden: die Umsetzung selbst bzw. die Neuanlage eines Tickets,
# dessen Artefakt-Zeile unmittelbar danach entsteht.
ERLAUBT = {"services/artifacts.py", "services/workflow_actions.py"}


def test_niemand_setzt_den_zustand_an_der_zentrale_vorbei():
    treffer = []
    for datei in APP.rglob("*.py"):
        rel = str(datei.relative_to(APP))
        if rel in ERLAUBT:
            continue
        for nr, zeile in enumerate(datei.read_text().splitlines(), 1):
            # Zuweisung, nicht Vergleich: „= x" ja, „== x" und „!= x" nein.
            if re.search(r"\.(agent_status|purchase_status)\s*=(?!=)", zeile):
                treffer.append(f"{rel}:{nr}: {zeile.strip()}")
    assert not treffer, (
        "Zustand wird direkt gesetzt statt über set_ticket_status/set_asset_status — "
        "die Artefakt-Zeile liefe auseinander:\n" + "\n".join(treffer))


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
    """Agent abgezogen: kein Zustand mehr, aber das Ticket springt nicht zurück."""
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
    """Wenn der eine Weg genutzt wird, hat der Abgleich nichts nachzuholen."""
    proj = await make_project(db, "TST", "Test")
    issue = await _ticket(db, proj)
    await art.set_ticket_status(db, issue, TicketAgentStatus.to_test)
    await db.commit()

    assert (await art.reconcile(db))["tickets_angeglichen"] == 0
