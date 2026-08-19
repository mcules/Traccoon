"""What a flow started from outside is attached to.

A flow with a ticket subject can set states, comment and assign agents, but only if it
knows which ticket it does that to. Started through a webhook it did not know: the instance
was born without an artifact, and every one of those actions found nothing to work on.

The foreign system does not know Traccoon's numbers, it knows its own. So the start node
names the field that holds the artifact, and this is where the binding comes from: a key
like `TRA-31`, a plain id like `31`, or whatever sits at that path.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.enums import GlobalRole, ProjectRole, WorkflowSubjectKind

log = logging.getLogger("workflow_subject")


def _feld(definition, version_graph: dict) -> str:
    """The field named in the start node (`trigger.subjekt_feld`), empty when there is none."""
    for n in (version_graph or {}).get("nodes") or []:
        typ = n.get("type") or (n.get("data") or {}).get("type")
        if typ == "start":
            cfg = (n.get("data") or {}).get("config") or n.get("config") or {}
            return str((cfg.get("trigger") or {}).get("subjekt_feld") or "").strip()
    return ""


def _dig(daten, pfad: str):
    cur = daten
    for teil in str(pfad).split("."):
        if isinstance(cur, dict) and teil in cur:
            cur = cur[teil]
        elif isinstance(cur, list) and teil.isdigit() and int(teil) < len(cur):
            cur = cur[int(teil)]
        else:
            return None
    return cur


async def subjekt_aus_nutzlast(db: AsyncSession, definition, payload: dict, ctx: dict, *,
                               besitzer_id: int | None) -> tuple[int | None, int | None, str]:
    """(issue_id, hardware_asset_id, error). An empty error means everything is fine.

    The lookup goes through the payload first, then through the mapped context: anyone
    using a `context_map` has the value there under a name of their own.
    """
    from ..models.workflow import WorkflowVersion

    if definition.subject_kind == WorkflowSubjectKind.standalone:
        return None, None, ""
    version = await db.get(WorkflowVersion, definition.current_version_id)
    pfad = _feld(definition, (version.graph if version else {}) or {})
    if not pfad:
        # No field named: the flow needs an artifact, the trigger delivers none.
        # That is a setup error and should stand out instead of running into nothing silently.
        return None, None, (f"Dieser Ablauf hängt an einem Artefakt "
                            f"({definition.subject_kind.value}); im Start-Knoten ist aber "
                            f"kein Feld dafür benannt.")

    roh = _dig(payload, pfad)
    if roh is None:
        roh = _dig(ctx, pfad)
    if roh is None or str(roh).strip() == "":
        return None, None, f"Feld {pfad!r} fehlt in der Nutzlast — kein Artefakt bestimmbar."
    wert = str(roh).strip()

    if definition.subject_kind == WorkflowSubjectKind.issue:
        from ..models.ticket import Issue
        issue = None
        if wert.isdigit():
            issue = await db.get(Issue, int(wert))
        if issue is None:
            issue = (await db.execute(select(Issue).where(
                Issue.key == wert.upper()))).scalar_one_or_none()
        if issue is None:
            return None, None, f"Kein Ticket zu {wert!r} gefunden."
        if not await _darf(db, besitzer_id, issue.project_id):
            return None, None, f"Keine Rechte am Projekt von {issue.key}."
        return issue.id, None, ""

    from ..models.hardware import HardwareAsset
    asset = await db.get(HardwareAsset, int(wert)) if wert.isdigit() else None
    if asset is None:
        return None, None, f"Kein Exemplar zu {wert!r} gefunden."
    if asset.project_id and not await _darf(db, besitzer_id, asset.project_id):
        return None, None, "Keine Rechte am Projekt dieses Exemplars."
    return None, asset.id, ""


async def _darf(db: AsyncSession, besitzer_id: int | None, project_id: int | None) -> bool:
    """May the owner of the trigger work on this project?

    A webhook is an address anyone might know, so the permissions do not come from the
    caller but from the person the trigger belongs to.
    """
    if project_id is None:
        return True
    if besitzer_id is None:
        return False
    from ..api.deps import build_access
    from ..models.project import Project
    from ..models.user import User

    person = await db.get(User, besitzer_id)
    projekt = await db.get(Project, project_id)
    if person is None or projekt is None:
        return False
    if person.global_role == GlobalRole.admin:
        return True
    try:
        zugriff = await build_access(projekt, person, db)
    except Exception:  # noqa: BLE001, a 403 or 404 means no access
        return False
    return zugriff.has_role(ProjectRole.member)
