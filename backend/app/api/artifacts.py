"""Artefakt-Typen und ihre Zustände — lesen für alle, ändern nur für Admins.

Der Prozess-Editor liest hier, welche Zustände ein Ablauf setzen kann; die Administration
pflegt Beschriftungen, Reihenfolge und eigene Typen.
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
    """Ein möglicher Zustand — seit dem Zusammenschluss ein Eintrag der Werteliste des
    Feldes `status`. `key` ist der gespeicherte Wert."""
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
    # Nur beim Feld `status` von Bedeutung: steuert Board-Spalte bzw. hebt hervor,
    # dass hier ein Mensch gebraucht wird.
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
    # Leer = frei angelegt; sonst die echte Spalte, in die das Feld schreibt.
    source: str = ""
    # Gesetzt, wenn die Auswahlwerte am Projekt hängen (Vorgangsart, Sprint, Person …) —
    # dann gibt es keine pflegbare Werteliste.
    options_source: str = ""
    builtin: bool = False
    # Gesetzt = Ergänzung genau dieses Projekts; leer = gilt überall.
    project_id: int | None = None
    options: list[OptionOut] = []
    # Auswahlwerte, die am Projekt hängen (Vorgangsart, Sprint, Board-Spalte, Personen).
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
    """Wer darf Felder pflegen?

    Allgemeine Felder (ohne Projekt) gelten überall — die ändert nur ein Admin. Ein Feld
    seines eigenen Projekts darf dessen Eigentümer bzw. Maintainer anlegen: so erweitert er
    seine Tickets, ohne die aller anderen zu verändern.
    """
    if project_id is None:
        return _admin(user)
    from ..models.enums import ProjectRole
    from ..models.project import Project
    from .deps import build_access
    projekt = await db.get(Project, project_id)
    if projekt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Projekt nicht gefunden")
    access = await build_access(projekt, user, db)     # 404 bei Fremdprojekt
    if not access.has_role(ProjectRole.maintainer):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Rolle owner|maintainer erforderlich")


async def _felder(db: AsyncSession, type_id: int,
                  project_id: int | None = None) -> list[FieldOut]:
    """Felder eines Artefakts samt Werteliste — auch die abgeschalteten, damit die
    Administration sie wieder einschalten kann.

    Mit `project_id` kommen auch die Auswahlwerte mit, die am Projekt hängen: Vorgangsart,
    Board-Spalte, Sprint, Zuständige, Standorte.
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
    """Alle Artefakte mit ihren Feldern und Zuständen.

    `subject=issue|hardware_asset` liefert genau das Artefakt, das ein Ablauf mit diesem
    Subjekt bearbeitet — das speist die Auswahl im Editor. Mit `project_id` kommen zusätzlich
    die Felder dieses Projekts mit; ohne nur die überall geltenden.
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
    # Sprung zum Detail: Ticket-Schlüssel bzw. Exemplar-Kennung.
    ref: str | None = None
    # Nur bei `with_values=true` gefüllt: {feld_key: [werte]}.
    values: dict[str, list] = {}


@router.get("/artifacts", response_model=list[ArtifactOut])
async def list_artifacts(
    project_id: int | None = None, type_key: str | None = None, waiting: bool = False,
    with_values: bool = False, limit: int = 200,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Übergreifende Liste: Tickets und Hardware in einer Sicht.

    Das ist der eigentliche Gewinn des gemeinsamen Modells — „was liegt gerade an?" lässt
    sich stellen, ohne zwei getrennte Welten abzufragen. `waiting=true` zeigt nur, was auf
    einen Menschen wartet (im Register je Zustand hinterlegt).
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

    # Zustände einmal je Typ nachschlagen (Beschriftung + „wartet").
    zustand: dict[tuple[int, str], object] = {}
    for _, t in rows:
        if not any(k[0] == t.id for k in zustand):
            for st in await svc.statuses(db, t.id):
                zustand[(t.id, st.value)] = st

    # Sichtbarkeit: projektlose Artefakte sind frei, projektgebundene nur bei Zugriff.
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
                except Exception:  # noqa: BLE001 — 404/403 bedeutet: nicht sichtbar
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
        # Eine Sammel-Abfrage für die ganze Liste statt einer je Zeile.
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
        # Ticket und Hardware hängen an fest verdrahteten Spalten — ohne ihren Eintrag
        # wüsste der Editor nicht mehr, welche Zustände es gibt.
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
    """Ein Feld am Artefakt anlegen — jederzeit, auch wenn es davon schon Exemplare gibt.

    Bestehende Exemplare bekommen dadurch keine Zeile in `artifact_values`; das Feld ist bei
    ihnen schlicht leer, bis jemand einen Wert setzt.
    """
    await _darf_feld(db, user, project_id)
    t = await db.get(ArtifactType, tid)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artefakt nicht gefunden")
    if data.kind not in felder.KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Unbekannter Feldtyp '{data.kind}' (erlaubt: {', '.join(felder.KINDS)})")
    # Auch gegen die allgemeinen prüfen: ein Projekt-Feld darf ein ausgeliefertes nicht
    # verdecken, sonst wüsste niemand mehr, welches gemeint ist.
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
    # Von „mehrere" auf „einer" zurück ist nur sauber, wenn niemand mehrere Werte trägt.
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
    """Feld löschen. Trägt noch jemand Werte darin, braucht es ein ausdrückliches `force=true`
    — sonst verschwänden Daten still."""
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
    """Beschriftung, Farbe und Reihenfolge — `value` bleibt unveränderlich, denn er IST der
    gespeicherte Wert."""
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
    """Ein benutzter Listenwert wird nicht gelöscht, sondern mit Anzahl abgelehnt."""
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
    """Werte je Feld-Schlüssel. Nicht genannte Felder bleiben unangetastet."""
    values: dict[str, list]


async def _artifact_access(db: AsyncSession, user: User, aid: int) -> Artifact:
    """Wer das Projekt des Artefakts sehen darf, darf seine Felder pflegen."""
    from .deps import build_access
    from ..models.project import Project
    a = await db.get(Artifact, aid)
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artefakt nicht gefunden")
    if a.project_id is not None:
        projekt = await db.get(Project, a.project_id)
        await build_access(projekt, user, db)   # 404/403 bei fehlendem Zugriff
    return a


@router.get("/artifacts/{aid}/values")
async def read_values(
    aid: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_session),
):
    """Die Feldwerte eines Artefakts — samt der Feld-Definitionen, damit die Oberfläche auch
    leere Felder anzeigen kann."""
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

    # Erst alles prüfen, dann alles schreiben: sonst stünde nach einem Fehler im dritten
    # Feld schon die halbe Änderung fest.
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
