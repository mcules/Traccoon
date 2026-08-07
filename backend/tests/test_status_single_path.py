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


async def test_laufender_agent_steht_nie_auf_warten(db):
    """Die Regel ohne Ausnahme: läuft für ein Ticket ein Agent, ist es „In Arbeit".

    Ein Lauf startet auf mehreren Wegen — Prozess-Schritt, Review-Runde im Worker,
    Wiedervorlage der Reliable-Queue nach einem Neustart — und nur der erste geht durch den
    Graphen. Am 2026-08-07 arbeiteten nach einem Worker-Neustart zwei Agenten, während das
    Board „Warten" zeigte, weil den Zustand niemand angefasst hatte.
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
    """Der Plan-Lauf gehört auf `planning`, nicht auf `in_progress` — beide landen im Board
    unter „In Arbeit", aber der Zustand soll die Phase benennen."""
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
    """Die Gegenprobe: ein FERTIGER Lauf ist kein Grund, ein wartendes Ticket anzufassen —
    sonst risse der Abgleich jedes abgeschlossene Ticket zurück in die Arbeit."""
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
    """Der Zustand kann stimmen und die SPALTE trotzdem falsch stehen.

    TRA-32 am 2026-08-07: das Ticket wurde aus dem Störungs-Zweig heraus fortgesetzt, der
    Agent lief mit `in_progress` — die Board-Spalte blieb auf „Warten", weil sie beim Parken
    gesetzt und nie wieder angefasst wurde. Ein Abgleich, der nur `agent_status` prüft, sieht
    daran nichts Falsches.
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
    """Gegenprobe: ein manuell abgenommenes Ticket zieht der Abgleich nicht zurück in die
    Arbeit, auch wenn noch ein Lauf nachläuft."""
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
