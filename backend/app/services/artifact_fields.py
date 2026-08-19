"""Fields of an artifact and the values a single unit of it carries.

Following the Artefakt model (`Artifacts → Fields → Values`): an artifact (ticket, hardware,
own type) carries any number of fields; a field of type "choice" has a maintained value
list, and the field says whether a unit may carry one or several values from it.

The values hang off `artifacts.id`: every ticket and every hardware unit has a row there, so
there is no special path per origin. Writing happens exclusively through `set_values()`,
where type check, cardinality and the comparison against the value list sit. A field may be
added at any time; existing units simply have no row for it.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.artifact import ArtifactField, ArtifactFieldOption, ArtifactValue
from .builtin_fields import BUILTIN_FIELDS, STATUS_KEY

KINDS = ("text", "number", "date", "boolean", "select")


class FieldError(ValueError):
    """The input does not fit the field; the API turns this into a 400 with plain text."""


async def fields_of(db: AsyncSession, type_id: int, project_id: int | None = None, *,
                    nur_aktive: bool = True) -> list[ArtifactField]:
    """Fields of an artifact: the ones applying everywhere plus the additions of the project.

    Without `project_id` only the general ones come, which is the view of the administration.
    With a project one sees what actually hangs off its tickets.
    """
    from sqlalchemy import or_
    q = select(ArtifactField).where(ArtifactField.type_id == type_id)
    q = q.where(ArtifactField.project_id.is_(None) if project_id is None
                else or_(ArtifactField.project_id.is_(None),
                         ArtifactField.project_id == project_id))
    if nur_aktive:
        q = q.where(ArtifactField.enabled.is_(True))
    return list((await db.execute(
        q.order_by(ArtifactField.order, ArtifactField.id))).scalars().all())


async def options_of(db: AsyncSession, field_id: int, *, nur_aktive: bool = True
                     ) -> list[ArtifactFieldOption]:
    q = select(ArtifactFieldOption).where(ArtifactFieldOption.field_id == field_id)
    if nur_aktive:
        q = q.where(ArtifactFieldOption.enabled.is_(True))
    return list((await db.execute(
        q.order_by(ArtifactFieldOption.order, ArtifactFieldOption.id))).scalars().all())


# ── Built-in fields (columns of ticket and unit) ─────────────────────────────

async def detailzeile(db: AsyncSession, artefakt):
    """The detail table behind an artifact: ticket or unit, nothing else."""
    from ..models.hardware import HardwareAsset
    from ..models.ticket import Issue
    for modell in (Issue, HardwareAsset):
        row = (await db.execute(select(modell).where(
            modell.artifact_id == artefakt.id))).scalars().first()
        if row is not None:
            return row
    return None


def _aus_spalte(feld: ArtifactField, row) -> list:
    """Bring a column value into the list shape free fields are delivered in as well."""
    wert = getattr(row, feld.source, None)
    if wert is None or wert == "":
        return []
    wert = getattr(wert, "value", wert)          # Enum → Wert
    if feld.kind == "boolean":
        return [bool(wert)]
    if feld.kind == "number":
        zahl = float(wert)
        return [int(zahl) if zahl.is_integer() else zahl]
    if feld.kind == "date":
        return [wert.date().isoformat() if hasattr(wert, "date") else str(wert)[:10]]
    return [str(wert)]


async def _in_spalte(db: AsyncSession, feld: ArtifactField, row, werte: list) -> list:
    """Write a checked value into the real column. The state takes the way over
    `services.artifacts` so that board column, message and artifact row are pulled along."""
    wert = werte[0] if werte else None
    if feld.key == STATUS_KEY:
        from . import artifacts as art
        from ..models.hardware import HardwareAsset
        if isinstance(row, HardwareAsset):
            await art.set_asset_status(db, row, wert)
        else:
            await art.set_ticket_status(db, row, wert)
        return _aus_spalte(feld, row)

    if wert is None:
        neu_wert = None
    elif feld.kind == "number":
        zahl = float(wert)
        neu_wert = int(zahl) if float(zahl).is_integer() else zahl
    elif feld.kind == "boolean":
        neu_wert = bool(wert)
    elif feld.kind == "date":
        neu_wert = dt.date.fromisoformat(str(wert)[:10])
    elif feld.options_source in ("issue_type", "board_status", "sprint", "member", "location"):
        neu_wert = int(wert)          # these fields hold foreign keys
    else:
        neu_wert = str(wert)
    # Columns with an enum type tolerate the plain value (SQLAlchemy converts); with date
    # columns that have a time part we add midnight UTC.
    ziel = getattr(type(row), feld.source).type
    if feld.kind == "date" and neu_wert is not None and hasattr(ziel, "timezone"):
        neu_wert = dt.datetime.combine(neu_wert, dt.time(0, 0), tzinfo=dt.timezone.utc)
    setattr(row, feld.source, neu_wert)
    await db.flush()
    return _aus_spalte(feld, row)


# ── Lesen ────────────────────────────────────────────────────────────────────

async def values_for(db: AsyncSession, artifact_ids: list[int]) -> dict[int, dict[str, list]]:
    """Werte mehrerer Artefakte in einem Rutsch — `{artefakt_id: {feld_key: [werte]}}`.

    One query instead of one per artifact: lists with 200 entries should not ask the database
    200 times. Built-in fields come from the detail table, free ones from `artifact_values`.
    """
    if not artifact_ids:
        return {}
    rows = (await db.execute(
        select(ArtifactValue, ArtifactField)
        .join(ArtifactField, ArtifactField.id == ArtifactValue.field_id)
        .where(ArtifactValue.artifact_id.in_(artifact_ids))
        .order_by(ArtifactValue.artifact_id, ArtifactField.order, ArtifactValue.order,
                  ArtifactValue.id))).all()
    out: dict[int, dict[str, list]] = {}
    for wert, feld in rows:
        out.setdefault(wert.artifact_id, {}).setdefault(feld.key, []).append(
            _lesbar(feld, wert.value_text))

    # Built-in fields: fetch the detail row once per artifact.
    from ..models.artifact import Artifact
    artefakte = (await db.execute(select(Artifact).where(
        Artifact.id.in_(artifact_ids)))).scalars().all()
    for a in artefakte:
        gebunden = [f for f in await fields_of(db, a.type_id, a.project_id) if f.source]
        if not gebunden:
            continue
        row = await detailzeile(db, a)
        if row is None:
            continue
        for f in gebunden:
            werte = _aus_spalte(f, row)
            if werte:
                out.setdefault(a.id, {})[f.key] = werte
    return out


async def values_of(db: AsyncSession, artifact_id: int) -> dict[str, list]:
    return (await values_for(db, [artifact_id])).get(artifact_id, {})


def _lesbar(feld: ArtifactField, text: str):
    """Gespeicherten Text in den Typ des Feldes zurückverwandeln."""
    if feld.kind == "number":
        try:
            zahl = float(text)
            return int(zahl) if zahl.is_integer() else zahl
        except ValueError:
            return text
    if feld.kind == "boolean":
        return text == "true"
    return text


# ── Schreiben ────────────────────────────────────────────────────────────────

def _als_text(feld: ArtifactField, wert) -> str:
    """Check one input value and bring it into its stored form."""
    if wert is None:
        return ""
    if feld.kind == "boolean":
        if isinstance(wert, bool):
            return "true" if wert else "false"
        if str(wert).strip().lower() in ("true", "1", "ja", "yes"):
            return "true"
        if str(wert).strip().lower() in ("false", "0", "nein", "no"):
            return "false"
        raise FieldError(f'"{feld.label}" expects yes or no, not "{wert}"')
    text = str(wert).strip()
    if feld.kind == "number":
        try:
            float(text)
        except ValueError:
            raise FieldError(f'"{feld.label}" expects a number, not "{text}"')
    elif feld.kind == "date":
        try:
            dt.date.fromisoformat(text[:10])
        except ValueError:
            raise FieldError(f'"{feld.label}" expects a date (YYYY-MM-DD), not "{text}"')
    return text


async def pruefe(db: AsyncSession, feld: ArtifactField, werte: list,
                 project_id: int | None = None) -> list[tuple[str, int | None]]:
    """Check inputs and bring them into their stored form, without writing anything.

    Separated from writing so that a call with several fields can be checked completely first
    and applied afterwards. Otherwise half the change would already stand in the database
    after the error in the third field, and it would need a rollback of the whole session.
    """
    sauber = [w for w in (werte or []) if not (w is None or str(w).strip() == "")]
    if not feld.multi and len(sauber) > 1:
        raise FieldError(f'"{feld.label}" takes only one value, {len(sauber)} arrived')
    if feld.required and not sauber:
        raise FieldError(f'"{feld.label}" is a required field')

    if feld.kind != "select":
        return [(_als_text(feld, w), None) for w in sauber]

    if feld.options_source:
        # Issue type, sprint, board column, person, location: the list hangs off the project.
        erlaubte = dict(await dynamic_options(db, feld, project_id))
        for w in sauber:
            if str(w) not in erlaubte:
                namen = ", ".join(erlaubte.values()) or "— nichts im Projekt vorhanden"
                raise FieldError(
                    f'"{w}" is not a valid value for "{feld.label}" ({namen})')
        return [(str(w), None) for w in sauber]

    moeglich = {o.value: o for o in await options_of(db, feld.id)}
    zuordnung: list[tuple[str, int | None]] = []
    for w in sauber:
        text = str(w).strip()
        treffer = moeglich.get(text)
        if treffer is None:
            erlaubt = ", ".join(moeglich) or "— die Werteliste ist leer"
            raise FieldError(
                f'"{text}" is not in the value list of "{feld.label}" ({erlaubt})')
        zuordnung.append((treffer.value, treffer.id))
    return zuordnung


async def schreibe(db: AsyncSession, artifact_id: int, feld: ArtifactField,
                   zuordnung: list[tuple[str, int | None]]) -> list:
    """Commit checked values, replacing the previous ones of this field completely.

    A built-in field lands in its real column (board, sprints and the AI lifecycle read there
    unchanged), a free one in `artifact_values`.
    """
    if feld.source:
        from ..models.artifact import Artifact
        artefakt = await db.get(Artifact, artifact_id)
        row = await detailzeile(db, artefakt) if artefakt else None
        if row is None:
            raise FieldError(f'"{feld.label}" belongs to a detail table that does not exist here')
        return await _in_spalte(db, feld, row, [t for t, _ in zuordnung])

    await db.execute(delete(ArtifactValue).where(
        ArtifactValue.artifact_id == artifact_id, ArtifactValue.field_id == feld.id))
    for i, (text, option_id) in enumerate(zuordnung):
        db.add(ArtifactValue(artifact_id=artifact_id, field_id=feld.id,
                             option_id=option_id, value_text=text, order=i))
    await db.flush()
    return [_lesbar(feld, text) for text, _ in zuordnung]


async def set_values(db: AsyncSession, artifact_id: int, feld: ArtifactField, werte: list,
                     project_id: int | None = None) -> list:
    """Der EINE Weg für ein einzelnes Feld: prüfen und schreiben."""
    return await schreibe(db, artifact_id, feld,
                          await pruefe(db, feld, werte, project_id))


async def option_usage(db: AsyncSession, option_id: int) -> int:
    """How many artifacts carry this list value? Protects against silent data loss."""
    return (await db.execute(select(func.count()).select_from(ArtifactValue)
                             .where(ArtifactValue.option_id == option_id))).scalar() or 0


async def field_usage(db: AsyncSession, field_id: int) -> int:
    return (await db.execute(select(func.count()).select_from(ArtifactValue)
                             .where(ArtifactValue.field_id == field_id))).scalar() or 0


# ── Creating and maintaining built-in fields ─────────────────────────────────

async def ensure_builtin_fields(db: AsyncSession) -> None:
    """Create respectively update the built-in fields of ticket and unit.

    Idempotent: existing fields are only straightened out in their origin, never in their
    label, which the admin may change. The same applies to the selectable values: missing ones
    are added, existing ones keep label, category and "waiting".
    """
    from . import artifacts as art

    for typ_key, felder_spec in BUILTIN_FIELDS.items():
        typ = await art.type_by_key(db, typ_key)
        if typ is None:
            continue
        vorhanden = {f.key: f for f in await fields_of(db, typ.id, nur_aktive=False)}
        for i, spec in enumerate(felder_spec):
            feld = vorhanden.get(spec["key"])
            if feld is None:
                feld = ArtifactField(
                    type_id=typ.id, key=spec["key"], label=spec["label"], kind=spec["kind"],
                    multi=spec["multi"], order=i, source=spec["source"],
                    options_source=spec["options_source"], builtin=True)
                db.add(feld)
                await db.flush()
            else:
                # Origin and type belong to the program, the label to the admin.
                feld.source = spec["source"]
                feld.options_source = spec["options_source"]
                feld.kind = spec["kind"]
                feld.builtin = True
            if spec["options"]:
                await _ensure_options(db, feld, spec["options"])
    await db.commit()


async def _ensure_options(db: AsyncSession, feld: ArtifactField,
                          spec: list[tuple[str, str, str, bool]]) -> None:
    da = {o.value: o for o in await options_of(db, feld.id, nur_aktive=False)}
    for i, (wert, label, kategorie, wartet) in enumerate(spec):
        if wert in da:
            continue
        db.add(ArtifactFieldOption(field_id=feld.id, value=wert, label=label, order=i,
                                   category=kategorie, waiting=wartet))
    await db.flush()


async def status_field(db: AsyncSession, type_id: int) -> ArtifactField | None:
    """The state field of an artifact. Board mirror, lifecycle and the evaluation "waiting for
    a human" read exactly this field, so its key is locked."""
    return next((f for f in await fields_of(db, type_id, nur_aktive=False)
                 if f.key == STATUS_KEY), None)


async def status_options(db: AsyncSession, type_id: int) -> list[ArtifactFieldOption]:
    feld = await status_field(db, type_id)
    return await options_of(db, feld.id, nur_aktive=False) if feld else []


# ── Selectable values that depend on the project ─────────────────────────────

async def dynamic_options(db: AsyncSession, feld: ArtifactField,
                          project_id: int | None) -> list[tuple[str, str]]:
    """Selectable values that do not stand in the register but hang off the project:
    issue types, board columns, sprints, members, locations."""
    if not feld.options_source or project_id is None:
        return []
    from ..models.hardware import Location
    from ..models.project import ProjectMember
    from ..models.ticket import IssueType, Sprint, WorkflowStatus
    from ..models.user import User

    quelle = feld.options_source
    if quelle == "issue_type":
        rows = (await db.execute(select(IssueType).where(IssueType.project_id == project_id)
                                 .order_by(IssueType.order))).scalars().all()
        return [(str(r.id), r.name) for r in rows]
    if quelle == "board_status":
        rows = (await db.execute(select(WorkflowStatus)
                                 .where(WorkflowStatus.project_id == project_id)
                                 .order_by(WorkflowStatus.order))).scalars().all()
        return [(str(r.id), r.name) for r in rows]
    if quelle == "sprint":
        # Sprints hang off the board, not directly off the project.
        from ..models.ticket import Board
        rows = (await db.execute(
            select(Sprint).join(Board, Board.id == Sprint.board_id)
            .where(Board.project_id == project_id)
            .order_by(Sprint.id.desc()))).scalars().all()
        return [(str(r.id), r.name) for r in rows]
    if quelle == "member":
        rows = (await db.execute(
            select(User, ProjectMember).join(ProjectMember, ProjectMember.user_id == User.id)
            .where(ProjectMember.project_id == project_id))).all()
        return [(str(u.id), u.display_name or u.username) for u, _ in rows]
    if quelle == "location":
        rows = (await db.execute(select(Location).order_by(Location.full_path))).scalars().all()
        return [(str(r.id), r.full_path or r.name) for r in rows]
    return []


async def uebernimm_alte_zustaende(db: AsyncSession) -> int:
    """One-off takeover: the former table `artifact_statuses` into the value list.

    Until the merge, states were a model of their own. Whoever had adjusted labels,
    categories or "waiting" there should keep that, which is why the old rows are created as
    values of the field `status` before the table falls. If the takeover runs a second time,
    it finds nothing any more.
    """
    from sqlalchemy import text

    from ..models.artifact import ArtifactType
    # Look first, then ask. The failure was caught, but Postgres writes it into the server log
    # as an ERROR regardless, on every start and every reload. These lines stood right beside
    # the real deadlock on 2026-08-07 and make the search unnecessarily hard; a log full of
    # harmless errors is a log in which one overlooks the real one.
    if await db.scalar(text("SELECT to_regclass('artifact_statuses')")) is None:
        return 0
    try:
        rows = (await db.execute(text(
            "SELECT type_id, key, label, category, \"order\", waiting FROM artifact_statuses"
        ))).all()
    except Exception:      # noqa: BLE001 - for instance SQLite in the tests: nothing to do
        await db.rollback()
        return 0
    if not rows:
        return 0

    uebernommen = 0
    for type_id, key, label, kategorie, reihenfolge, wartet in rows:
        typ = await db.get(ArtifactType, type_id)
        if typ is None:
            continue
        feld = await status_field(db, type_id)
        if feld is None:
            spec = next((f for f in BUILTIN_FIELDS.get(typ.key, []) if f["key"] == STATUS_KEY),
                        None)
            if spec is None:
                continue
            feld = ArtifactField(type_id=type_id, key=STATUS_KEY, label=spec["label"],
                                 kind="select", source=spec["source"], builtin=True, order=0)
            db.add(feld)
            await db.flush()
        da = {o.value for o in await options_of(db, feld.id, nur_aktive=False)}
        if key in da:
            continue
        db.add(ArtifactFieldOption(field_id=feld.id, value=key, label=label or key,
                                   category=kategorie or "", order=reihenfolge or 0,
                                   waiting=bool(wartet)))
        uebernommen += 1
    await db.commit()
    if uebernommen:
        import logging
        logging.getLogger("artifacts").info(
            "States taken over into the value list: %s", uebernommen)
    return uebernommen
