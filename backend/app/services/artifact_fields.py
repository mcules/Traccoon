"""Felder eines Artefakts und die Werte, die ein einzelnes Exemplar davon trägt.

Nach ALMEX-Vorbild (`Artifacts → Fields → Values`): ein Artefakt (Ticket, Hardware, eigener
Typ) trägt beliebig viele Felder; ein Feld vom Typ „Auswahl" hat eine gepflegte Werteliste,
und am Feld steht, ob ein Exemplar einen oder mehrere Werte daraus tragen darf.

Die Werte hängen an `artifacts.id` — jedes Ticket und jedes Hardware-Exemplar hat dort eine
Zeile, deshalb gibt es keinen Sonderweg je Herkunft. Geschrieben wird ausschließlich über
`set_values()`: dort sitzen Typprüfung, Kardinalität und der Abgleich gegen die Werteliste.
Ein Feld darf jederzeit dazukommen — bestehende Exemplare haben dafür schlicht keine Zeile.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.artifact import ArtifactField, ArtifactFieldOption, ArtifactValue
from .builtin_fields import BUILTIN_FIELDS, STATUS_KEY

KINDS = ("text", "number", "date", "boolean", "select")


class FieldError(ValueError):
    """Eingabe passt nicht zum Feld — die API macht daraus eine 400 mit Klartext."""


async def fields_of(db: AsyncSession, type_id: int, project_id: int | None = None, *,
                    nur_aktive: bool = True) -> list[ArtifactField]:
    """Felder eines Artefakts: die überall geltenden plus die Ergänzungen des Projekts.

    Ohne `project_id` kommen nur die allgemeinen — das ist die Sicht der Administration.
    Mit Projekt sieht man, was an dessen Tickets tatsächlich dranhängt.
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


# ── Eingebaute Felder (Spalten von Ticket und Exemplar) ──────────────────────

async def detailzeile(db: AsyncSession, artefakt):
    """Die Detailtabelle hinter einem Artefakt — Ticket oder Exemplar, sonst nichts."""
    from ..models.hardware import HardwareAsset
    from ..models.ticket import Issue
    for modell in (Issue, HardwareAsset):
        row = (await db.execute(select(modell).where(
            modell.artifact_id == artefakt.id))).scalars().first()
        if row is not None:
            return row
    return None


def _aus_spalte(feld: ArtifactField, row) -> list:
    """Spaltenwert in die Listenform bringen, in der auch freie Felder geliefert werden."""
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
    """Geprüften Wert in die echte Spalte schreiben. Der Zustand nimmt dabei den Weg über
    `services.artifacts`, damit Board-Spalte, Meldung und Artefakt-Zeile mitgezogen werden."""
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
        neu_wert = int(wert)          # diese Felder halten Fremdschlüssel
    else:
        neu_wert = str(wert)
    # Spalten mit Enum-Typ vertragen den reinen Wert (SQLAlchemy wandelt),
    # bei Datums-Spalten mit Zeitanteil ergänzen wir Mitternacht UTC.
    ziel = getattr(type(row), feld.source).type
    if feld.kind == "date" and neu_wert is not None and hasattr(ziel, "timezone"):
        neu_wert = dt.datetime.combine(neu_wert, dt.time(0, 0), tzinfo=dt.timezone.utc)
    setattr(row, feld.source, neu_wert)
    await db.flush()
    return _aus_spalte(feld, row)


# ── Lesen ────────────────────────────────────────────────────────────────────

async def values_for(db: AsyncSession, artifact_ids: list[int]) -> dict[int, dict[str, list]]:
    """Werte mehrerer Artefakte in einem Rutsch — `{artefakt_id: {feld_key: [werte]}}`.

    Eine Abfrage statt einer je Artefakt: Listen mit 200 Einträgen sollen die Datenbank nicht
    200-mal fragen. Eingebaute Felder kommen aus der Detailtabelle, freie aus `artifact_values`.
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

    # Eingebaute Felder: je Artefakt einmal die Detailzeile holen.
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
    """Einen Eingabewert prüfen und in seine gespeicherte Form bringen."""
    if wert is None:
        return ""
    if feld.kind == "boolean":
        if isinstance(wert, bool):
            return "true" if wert else "false"
        if str(wert).strip().lower() in ("true", "1", "ja", "yes"):
            return "true"
        if str(wert).strip().lower() in ("false", "0", "nein", "no"):
            return "false"
        raise FieldError(f'„{feld.label}“ erwartet Ja oder Nein, nicht „{wert}“')
    text = str(wert).strip()
    if feld.kind == "number":
        try:
            float(text)
        except ValueError:
            raise FieldError(f'„{feld.label}“ erwartet eine Zahl, nicht „{text}“')
    elif feld.kind == "date":
        try:
            dt.date.fromisoformat(text[:10])
        except ValueError:
            raise FieldError(f'„{feld.label}“ erwartet ein Datum (JJJJ-MM-TT), nicht „{text}“')
    return text


async def pruefe(db: AsyncSession, feld: ArtifactField, werte: list,
                 project_id: int | None = None) -> list[tuple[str, int | None]]:
    """Eingaben prüfen und in ihre Speicherform bringen — ohne etwas zu schreiben.

    Getrennt vom Schreiben, damit ein Aufruf mit mehreren Feldern erst vollständig geprüft
    und dann angewandt werden kann. Sonst stünde nach dem Fehler im dritten Feld schon die
    halbe Änderung in der Datenbank, und es bräuchte ein Zurückrollen der ganzen Sitzung.
    """
    sauber = [w for w in (werte or []) if not (w is None or str(w).strip() == "")]
    if not feld.multi and len(sauber) > 1:
        raise FieldError(f'„{feld.label}“ nimmt nur einen Wert — es kamen {len(sauber)}')
    if feld.required and not sauber:
        raise FieldError(f'„{feld.label}“ ist ein Pflichtfeld')

    if feld.kind != "select":
        return [(_als_text(feld, w), None) for w in sauber]

    if feld.options_source:
        # Vorgangsart, Sprint, Board-Spalte, Person, Standort — die Liste hängt am Projekt.
        erlaubte = dict(await dynamic_options(db, feld, project_id))
        for w in sauber:
            if str(w) not in erlaubte:
                namen = ", ".join(erlaubte.values()) or "— nichts im Projekt vorhanden"
                raise FieldError(
                    f'„{w}“ ist kein gültiger Wert für „{feld.label}“ ({namen})')
        return [(str(w), None) for w in sauber]

    moeglich = {o.value: o for o in await options_of(db, feld.id)}
    zuordnung: list[tuple[str, int | None]] = []
    for w in sauber:
        text = str(w).strip()
        treffer = moeglich.get(text)
        if treffer is None:
            erlaubt = ", ".join(moeglich) or "— die Werteliste ist leer"
            raise FieldError(
                f'„{text}“ steht nicht in der Werteliste von „{feld.label}“ ({erlaubt})')
        zuordnung.append((treffer.value, treffer.id))
    return zuordnung


async def schreibe(db: AsyncSession, artifact_id: int, feld: ArtifactField,
                   zuordnung: list[tuple[str, int | None]]) -> list:
    """Geprüfte Werte festschreiben — ersetzt die bisherigen dieses Feldes vollständig.

    Ein eingebautes Feld landet in seiner echten Spalte (Board, Sprints und der
    KI-Lebenszyklus lesen unverändert dort), ein freies in `artifact_values`.
    """
    if feld.source:
        from ..models.artifact import Artifact
        artefakt = await db.get(Artifact, artifact_id)
        row = await detailzeile(db, artefakt) if artefakt else None
        if row is None:
            raise FieldError(f'„{feld.label}“ gehört zu einer Detailtabelle, die es hier nicht gibt')
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
    """Wie viele Artefakte tragen diesen Listenwert? Schützt vor stillem Datenverlust."""
    return (await db.execute(select(func.count()).select_from(ArtifactValue)
                             .where(ArtifactValue.option_id == option_id))).scalar() or 0


async def field_usage(db: AsyncSession, field_id: int) -> int:
    return (await db.execute(select(func.count()).select_from(ArtifactValue)
                             .where(ArtifactValue.field_id == field_id))).scalar() or 0


# ── Eingebaute Felder anlegen und pflegen ────────────────────────────────────

async def ensure_builtin_fields(db: AsyncSession) -> None:
    """Die eingebauten Felder von Ticket und Exemplar anlegen bzw. nachziehen.

    Idempotent: vorhandene Felder werden nur in ihrer Herkunft geradegezogen, nie in ihrer
    Beschriftung — die darf der Admin ändern. Bei den Auswahlwerten gilt dasselbe: fehlende
    kommen dazu, vorhandene behalten Beschriftung, Kategorie und „wartet".
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
                # Herkunft und Typ gehören dem Programm, die Beschriftung dem Admin.
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
    """Das Zustands-Feld eines Artefakts. Board-Spiegel, Lebenszyklus und die Auswertung
    „wartet auf einen Menschen" lesen genau dieses Feld — sein Schlüssel ist gesperrt."""
    return next((f for f in await fields_of(db, type_id, nur_aktive=False)
                 if f.key == STATUS_KEY), None)


async def status_options(db: AsyncSession, type_id: int) -> list[ArtifactFieldOption]:
    feld = await status_field(db, type_id)
    return await options_of(db, feld.id, nur_aktive=False) if feld else []


# ── Auswahlwerte, die vom Projekt abhängen ───────────────────────────────────

async def dynamic_options(db: AsyncSession, feld: ArtifactField,
                          project_id: int | None) -> list[tuple[str, str]]:
    """Auswahlwerte, die nicht im Register stehen, sondern am Projekt hängen —
    Vorgangsarten, Board-Spalten, Sprints, Mitglieder, Standorte."""
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
        # Sprints hängen am Board, nicht direkt am Projekt.
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
    """Einmalige Übernahme: die frühere Tabelle `artifact_statuses` in die Werteliste.

    Zustände waren bis zum Zusammenschluss ein eigenes Modell. Wer dort Beschriftungen,
    Kategorien oder „wartet" angepasst hatte, soll das behalten — deshalb werden die alten
    Zeilen als Werte des Feldes `status` angelegt, bevor die Tabelle fällt. Läuft die
    Übernahme ein zweites Mal, findet sie nichts mehr.
    """
    from sqlalchemy import text

    from ..models.artifact import ArtifactType
    try:
        rows = (await db.execute(text(
            "SELECT type_id, key, label, category, \"order\", waiting FROM artifact_statuses"
        ))).all()
    except Exception:      # noqa: BLE001 — Tabelle gibt es (noch) nicht: nichts zu tun
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
            "Zustände in die Werteliste übernommen: %s", uebernommen)
    return uebernommen
