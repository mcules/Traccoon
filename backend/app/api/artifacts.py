"""Artifact types and their states: readable for everyone, changeable only for admins.

The process editor reads here which states a flow can set; the administration maintains
labels, order and types of its own.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models.artifact import (
    Artifact, ArtifactField, ArtifactFieldOption, ArtifactType,
)
from ..models.enums import GlobalRole
from ..models.user import User
from ..services import artifact_fields as felder
from ..services import artifacts as svc
from .deps import get_current_user

router = APIRouter(tags=["artifacts"])


class StatusOut(BaseModel):
    """One possible state, since the merge an entry of the value list of the field `status`.
    `key` is the stored value."""
    id: int
    key: str
    label: str
    category: str
    order: int
    waiting: bool


class OptionOut(BaseModel):
    """Ein Eintrag der Werteliste eines Feldes."""
    id: int
    value: str
    label: str
    color: str
    order: int
    enabled: bool
    # Only meaningful with the field `status`: controls the board column respectively
    # highlights that a human is needed here.
    category: str = ""
    waiting: bool = False
    model_config = {"from_attributes": True}


class FieldOut(BaseModel):
    id: int
    key: str
    label: str
    kind: str
    multi: bool
    required: bool
    order: int
    description: str
    enabled: bool
    # Empty = freely created; otherwise the real column the field writes into.
    source: str = ""
    # Set when the selectable values hang off the project (issue type, sprint, person …);
    # then there is no maintainable value list.
    options_source: str = ""
    builtin: bool = False
    # Set = an addition of exactly this project; empty = applies everywhere.
    project_id: int | None = None
    options: list[OptionOut] = []
    # Selectable values that hang off the project (issue type, sprint, board column, people).
    dynamic_options: list[tuple[str, str]] = []


class TypeOut(BaseModel):
    id: int
    key: str
    name: str
    plural: str
    icon: str
    color: str
    backing: str
    project_id: int | None
    builtin: bool
    enabled: bool
    description: str
    fields: list[FieldOut] = []
    statuses: list[StatusOut] = []


class TypeIn(BaseModel):
    key: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=100)
    plural: str = ""
    icon: str = "📦"
    color: str = "#58a6ff"
    project_id: int | None = None
    description: str = ""


class TypeUpdate(BaseModel):
    name: str | None = None
    plural: str | None = None
    icon: str | None = None
    color: str | None = None
    description: str | None = None
    enabled: bool | None = None



def _admin(user: User) -> None:
    if user.global_role != GlobalRole.admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Nur ein Admin darf Artefakte pflegen")


async def _darf_feld(db: AsyncSession, user: User, project_id: int | None) -> None:
    """Who may maintain fields?

    General fields (without a project) apply everywhere, and only an admin changes those. A
    field of one's own project may be created by its owner respectively maintainer: that way
    they extend their tickets without changing those of everybody else.
    """
    if project_id is None:
        return _admin(user)
    from ..models.enums import ProjectRole
    from ..models.project import Project
    from .deps import build_access
    projekt = await db.get(Project, project_id)
    if projekt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Projekt nicht gefunden")
    access = await build_access(projekt, user, db)     # 404 on a foreign project
    if not access.has_role(ProjectRole.maintainer):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Rolle owner|maintainer erforderlich")


async def _felder(db: AsyncSession, type_id: int,
                  project_id: int | None = None) -> list[FieldOut]:
    """Fields of an artifact including the value list, the switched off ones as well, so that
    the administration can switch them on again.

    With `project_id` the selectable values that hang off the project come along as well:
    issue type, board column, sprint, responsible people, locations.
    """
    aus = []
    for f in await felder.fields_of(db, type_id, project_id, nur_aktive=False):
        optionen = await felder.options_of(db, f.id, nur_aktive=False)
        aus.append(FieldOut(
            id=f.id, key=f.key, label=f.label, kind=f.kind, multi=f.multi,
            required=f.required, order=f.order, description=f.description, enabled=f.enabled,
            source=f.source, options_source=f.options_source, builtin=f.builtin,
            project_id=f.project_id,
            options=[OptionOut.model_validate(o) for o in optionen],
            dynamic_options=await felder.dynamic_options(db, f, project_id),
        ))
    return aus


async def _out(db: AsyncSession, t: ArtifactType,
               project_id: int | None = None) -> TypeOut:
    return TypeOut(
        id=t.id, key=t.key, name=t.name, plural=t.plural or t.name, icon=t.icon, color=t.color,
        backing=t.backing, project_id=t.project_id,
        builtin=t.builtin, enabled=t.enabled, description=t.description,
        fields=await _felder(db, t.id, project_id),
        statuses=[StatusOut(id=o.id, key=o.value, label=o.label or o.value,
                            category=o.category or "in_progress", order=o.order,
                            waiting=o.waiting)
                  for o in await svc.statuses(db, t.id)],
    )


@router.get("/artifact-types", response_model=list[TypeOut])
async def list_types(
    subject: str | None = None, project_id: int | None = None,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """All artifacts with their fields and states.

    `subject=issue|hardware_asset` delivers exactly the artifact a flow with this subject
    works on, which feeds the selection in the editor. With `project_id` the fields of that
    project come along in addition; without it only the ones applying everywhere.
    """
    if subject:
        t = await svc.type_for_subject(db, subject)
        return [await _out(db, t, project_id)] if t else []
    rows = (await db.execute(select(ArtifactType).order_by(ArtifactType.id))).scalars().all()
    return [await _out(db, t, project_id) for t in rows]


class ArtifactOut(BaseModel):
    """Eine Sache in Traccoon — quer über alle Typen."""
    id: int
    type_key: str
    type_name: str
    icon: str
    title: str
    status_key: str
    status_label: str
    waiting: bool
    project_id: int | None
    # Jump to the detail: ticket key respectively unit identifier.
    ref: str | None = None
    # Filled only with `with_values=true`: {field_key: [values]}.
    values: dict[str, list] = {}


@router.get("/artifacts", response_model=list[ArtifactOut])
async def list_artifacts(
    project_id: int | None = None, type_key: str | None = None, waiting: bool = False,
    with_values: bool = False, limit: int = 200,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Cross-cutting list: tickets and hardware in one view.

    That is the actual gain of the common model: "what is pending right now?" can be asked
    without querying two separate worlds. `waiting=true` shows only what is waiting for a
    human (recorded per state in the register).
    """
    from ..models.artifact import Artifact
    from ..models.hardware import HardwareAsset
    from ..models.ticket import Issue
    from .deps import build_access

    q = select(Artifact, ArtifactType).join(ArtifactType, ArtifactType.id == Artifact.type_id)
    if project_id is not None:
        q = q.where(Artifact.project_id == project_id)
    if type_key:
        q = q.where(ArtifactType.key == type_key)
    rows = (await db.execute(q.order_by(Artifact.id.desc()).limit(limit))).all()

    # Look up the states once per type (label plus "waiting").
    zustand: dict[tuple[int, str], object] = {}
    for _, t in rows:
        if not any(k[0] == t.id for k in zustand):
            for st in await svc.statuses(db, t.id):
                zustand[(t.id, st.value)] = st

    # Visibility: project-less artifacts are free, project bound ones only with access.
    from ..models.project import Project
    erlaubt: dict[int, bool] = {}
    out: list[ArtifactOut] = []
    for a, t in rows:
        if a.project_id is not None:
            if a.project_id not in erlaubt:
                projekt = await db.get(Project, a.project_id)
                try:
                    await build_access(projekt, user, db)
                    erlaubt[a.project_id] = True
                except Exception:  # noqa: BLE001 - 404/403 means: not visible
                    erlaubt[a.project_id] = False
            if not erlaubt[a.project_id]:
                continue
        st = zustand.get((t.id, a.status_key))
        if waiting and not (st and st.waiting):
            continue
        ref = None
        if t.backing == "issue":
            issue = (await db.execute(select(Issue).where(Issue.artifact_id == a.id))).scalars().first()
            ref = issue.key if issue else None
        elif t.backing == "hardware_asset":
            asset = (await db.execute(select(HardwareAsset).where(
                HardwareAsset.artifact_id == a.id))).scalars().first()
            ref = f"HW-{asset.id}" if asset else None
        out.append(ArtifactOut(
            id=a.id, type_key=t.key, type_name=t.name, icon=t.icon, title=a.title,
            status_key=a.status_key, status_label=(st.label or st.value) if st else (a.status_key or "—"),
            waiting=bool(st and st.waiting), project_id=a.project_id, ref=ref,
        ))
    if with_values and out:
        # One collective query for the whole list instead of one per row.
        alle = await felder.values_for(db, [o.id for o in out])
        for o in out:
            o.values = alle.get(o.id, {})
    return out


@router.post("/artifact-types", response_model=TypeOut, status_code=201)
async def create_type(
    data: TypeIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    _admin(user)
    if await svc.type_by_key(db, data.key) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Typ '{data.key}' gibt es schon")
    t = ArtifactType(**data.model_dump(), backing="generic", builtin=False)
    db.add(t)
    await db.commit()
    await db.refresh(t)
    return await _out(db, t)


@router.put("/artifact-types/{tid}", response_model=TypeOut)
async def update_type(
    tid: int, data: TypeUpdate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    _admin(user)
    t = await db.get(ArtifactType, tid)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Typ nicht gefunden")
    for feld, wert in data.model_dump(exclude_unset=True).items():
        setattr(t, feld, wert)
    await db.commit()
    await db.refresh(t)
    return await _out(db, t)


@router.delete("/artifact-types/{tid}", status_code=204)
async def delete_type(
    tid: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    _admin(user)
    t = await db.get(ArtifactType, tid)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Typ nicht gefunden")
    if t.builtin:
        # Ticket and hardware hang off hard wired columns; without their entry the editor
        # would no longer know which states exist.
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Eingebaute Typen lassen sich nicht löschen (nur abschalten)")
    await db.delete(t)
    await db.commit()






# ── Felder eines Artefakts ───────────────────────────────────────────────────

class FieldIn(BaseModel):
    key: str = Field(min_length=1, max_length=40)
    label: str = Field(min_length=1, max_length=100)
    kind: str = "text"
    multi: bool = False
    required: bool = False
    order: int = 0
    description: str = ""


class FieldUpdate(BaseModel):
    label: str | None = None
    kind: str | None = None
    multi: bool | None = None
    required: bool | None = None
    order: int | None = None
    description: str | None = None
    enabled: bool | None = None


async def _get_field(db: AsyncSession, fid: int) -> ArtifactField:
    f = await db.get(ArtifactField, fid)
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Feld nicht gefunden")
    return f


@router.post("/artifact-types/{tid}/fields", response_model=TypeOut, status_code=201)
async def add_field(
    tid: int, data: FieldIn, project_id: int | None = None,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Create a field on the artifact, at any time, even when units of it already exist.

    Existing units get no row in `artifact_values` from that; the field is simply empty for
    them until somebody sets a value.
    """
    await _darf_feld(db, user, project_id)
    t = await db.get(ArtifactType, tid)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artefakt nicht gefunden")
    if data.kind not in felder.KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Unbekannter Feldtyp '{data.kind}' (erlaubt: {', '.join(felder.KINDS)})")
    # Check against the general ones as well: a project field must not cover a shipped one,
    # because then nobody would know which one is meant.
    vorhanden = [f for f in await felder.fields_of(db, tid, project_id, nur_aktive=False)
                 if f.key == data.key]
    if vorhanden:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Feld '{data.key}' gibt es hier schon")
    db.add(ArtifactField(type_id=tid, project_id=project_id, **data.model_dump()))
    await db.commit()
    await db.refresh(t)
    return await _out(db, t, project_id)


@router.put("/artifact-fields/{fid}", response_model=TypeOut)
async def update_field(
    fid: int, data: FieldUpdate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    f = await _get_field(db, fid)
    await _darf_feld(db, user, f.project_id)
    werte = data.model_dump(exclude_unset=True)
    if "kind" in werte and werte["kind"] not in felder.KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unbekannter Feldtyp '{werte['kind']}'")
    # Going back from "several" to "one" is only clean when nobody carries several values.
    if werte.get("multi") is False and f.multi:
        from sqlalchemy import func as _func
        from ..models.artifact import ArtifactValue
        zuviel = (await db.execute(
            select(_func.count()).select_from(
                select(ArtifactValue.artifact_id).where(ArtifactValue.field_id == fid)
                .group_by(ArtifactValue.artifact_id)
                .having(_func.count() > 1).subquery()))).scalar() or 0
        if zuviel:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{zuviel} Artefakt(e) tragen hier mehrere Werte — erst bereinigen, "
                "sonst gingen Werte still verloren")
    for feld_name, wert in werte.items():
        setattr(f, feld_name, wert)
    await db.commit()
    t = await db.get(ArtifactType, f.type_id)
    return await _out(db, t)


@router.delete("/artifact-fields/{fid}", status_code=204)
async def delete_field(
    fid: int, force: bool = False,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Delete a field. If somebody still carries values in it, an explicit `force=true` is
    needed, because otherwise data would disappear silently."""
    f = await _get_field(db, fid)
    await _darf_feld(db, user, f.project_id)
    if f.builtin:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Ein ausgeliefertes Feld lässt sich nicht löschen — Board, "
                            "Sprints und der KI-Lebenszyklus laufen darauf. Abschalten geht.")
    benutzt = await felder.field_usage(db, fid)
    if benutzt and not force:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Das Feld trägt an {benutzt} Stelle(n) Werte. Zum Abschalten `enabled=false` "
            "setzen, zum endgültigen Löschen mit force=true wiederholen.")
    await db.delete(f)
    await db.commit()


# ── Werteliste eines Feldes ──────────────────────────────────────────────────

class OptionIn(BaseModel):
    value: str = Field(min_length=1, max_length=200)
    label: str = ""
    color: str = ""
    order: int = 0
    category: str = ""
    waiting: bool = False


class OptionUpdate(BaseModel):
    label: str | None = None
    color: str | None = None
    order: int | None = None
    enabled: bool | None = None
    category: str | None = None
    waiting: bool | None = None


@router.post("/artifact-fields/{fid}/options", response_model=TypeOut, status_code=201)
async def add_option(
    fid: int, data: OptionIn,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    f = await _get_field(db, fid)
    await _darf_feld(db, user, f.project_id)
    if f.kind != "select":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Eine Werteliste hat nur ein Feld vom Typ „Auswahl“")
    vorhanden = (await db.execute(select(ArtifactFieldOption).where(
        ArtifactFieldOption.field_id == fid,
        ArtifactFieldOption.value == data.value))).scalars().first()
    if vorhanden is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Wert '{data.value}' steht schon in der Liste")
    db.add(ArtifactFieldOption(field_id=fid, **data.model_dump()))
    await db.commit()
    t = await db.get(ArtifactType, f.type_id)
    return await _out(db, t)


@router.put("/artifact-field-options/{oid}", response_model=OptionOut)
async def update_option(
    oid: int, data: OptionUpdate,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Label, colour and order. `value` stays immutable, because it IS the stored value."""
    _admin(user)
    o = await db.get(ArtifactFieldOption, oid)
    if o is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Wert nicht gefunden")
    for feld_name, wert in data.model_dump(exclude_unset=True).items():
        setattr(o, feld_name, wert)
    await db.commit()
    await db.refresh(o)
    return o


@router.delete("/artifact-field-options/{oid}", status_code=204)
async def delete_option(
    oid: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """A used list value is not deleted but rejected with a count."""
    _admin(user)
    o = await db.get(ArtifactFieldOption, oid)
    if o is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Wert nicht gefunden")
    benutzt = await felder.option_usage(db, oid)
    if benutzt:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Dieser Wert ist {benutzt} Artefakt(en) zugeordnet. Zum Ausblenden `enabled=false` "
            "setzen — dann bleibt er dort erhalten, ist aber nicht mehr wählbar.")
    await db.delete(o)
    await db.commit()


# ── Werte am einzelnen Artefakt ──────────────────────────────────────────────

class ValuesIn(BaseModel):
    """Values per field key. Fields not named stay untouched."""
    values: dict[str, list]


async def _artifact_access(db: AsyncSession, user: User, aid: int) -> Artifact:
    """Whoever may see the project of the artifact may maintain its fields."""
    from .deps import build_access
    from ..models.project import Project
    a = await db.get(Artifact, aid)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artefakt nicht gefunden")
    if a.project_id is not None:
        projekt = await db.get(Project, a.project_id)
        await build_access(projekt, user, db)   # 404/403 when access is missing
    return a


@router.get("/artifacts/{aid}/values")
async def read_values(
    aid: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """The field values of an artifact, including the field definitions, so that the interface
    can show empty fields as well."""
    a = await _artifact_access(db, user, aid)
    return {
        "artifact_id": a.id,
        "fields": [f.model_dump() for f in await _felder(db, a.type_id, a.project_id)],
        "values": await felder.values_of(db, a.id),
    }


@router.put("/artifacts/{aid}/values")
async def write_values(
    aid: int, data: ValuesIn,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    a = await _artifact_access(db, user, aid)
    artefakt_id = a.id
    bekannt = {f.key: f for f in await felder.fields_of(db, a.type_id, a.project_id)}

    # Check everything first, then write everything: otherwise half the change would stand
    # after an error in the third field.
    geprueft = []
    for key, werte in data.values.items():
        f = bekannt.get(key)
        if f is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"Feld '{key}' gibt es an diesem Artefakt nicht")
        try:
            geprueft.append((f, await felder.pruefe(db, f, werte, a.project_id)))
        except felder.FieldError as e:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))

    for f, zuordnung in geprueft:
        await felder.schreibe(db, artefakt_id, f, zuordnung)
    await db.commit()
    return {"artifact_id": artefakt_id, "values": await felder.values_of(db, artefakt_id)}
