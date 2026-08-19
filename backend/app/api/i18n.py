"""Translation overrides: read them for the running UI, edit them as an admin.

The shipped catalogs are bundled with the frontend, so the browser has German and English
before it talks to the server at all. This endpoint adds what a person changed at runtime
and any language that was created without a release. Keeping both apart is deliberate:
texts that belong to the code stay in the repository and go through review, while a typo
in a label should not need a deployment.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.fehler import Fehler
from ..db import get_session
from ..models.i18n import UiLocale, UiTranslation
from ..models.user import User
from ..services import i18n as server_texte
from .deps import get_current_user, require_admin

router = APIRouter(prefix="/i18n", tags=["i18n"])

# The languages that ship with the frontend. Everything else exists because somebody
# created it here.
EINGEBAUT = ("de", "en")


class TextIn(BaseModel):
    text: str = Field(max_length=4000)


class ImportIn(BaseModel):
    """A whole catalog at once, as it comes out of the export."""
    texte: dict[str, str]
    ersetzen: bool = False   # true wipes what is not in the payload


def _locale(roh: str) -> str:
    kurz = (roh or "").strip().lower().replace("_", "-")[:10]
    if not kurz or not kurz.replace("-", "").isalnum():
        raise Fehler(status.HTTP_400_BAD_REQUEST, "err.invalid_language_code",
                     "Invalid language code")
    return kurz


NAMEN = {"de": "Deutsch", "en": "English"}


class LocaleIn(BaseModel):
    locale: str = Field(min_length=2, max_length=10)
    name: str = Field(default="", max_length=80)


class LocaleUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    enabled: bool | None = None


@router.get("/locales")
async def list_locales(user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    """Which languages exist, what they are called, and how many texts each carries here.

    Shipped languages need no row of their own: they exist because their catalog is bundled
    with the application. A row appears as soon as somebody renames one, switches it off or
    creates a language that was never shipped.
    """
    rows = (await db.execute(
        select(UiTranslation.locale, func.count(UiTranslation.id))
        .group_by(UiTranslation.locale))).all()
    gezaehlt = {locale: anzahl for locale, anzahl in rows}
    eigene = {r.locale: r for r in (await db.execute(select(UiLocale))).scalars().all()}
    alle = sorted(set(EINGEBAUT) | set(gezaehlt) | set(eigene))
    return [{"locale": l,
             "name": (eigene[l].name if l in eigene and eigene[l].name else NAMEN.get(l, l)),
             "eigene_texte": gezaehlt.get(l, 0),
             "eingebaut": l in EINGEBAUT,
             "enabled": eigene[l].enabled if l in eigene else True}
            for l in alle]


@router.post("/locales", status_code=201)
async def create_locale(data: LocaleIn, _: User = Depends(require_admin),
                        db: AsyncSession = Depends(get_session)):
    """Create a language. It exists from now on, even before its first text."""
    lc = _locale(data.locale)
    if (await db.execute(select(UiLocale).where(UiLocale.locale == lc))).scalar_one_or_none():
        raise Fehler(status.HTTP_409_CONFLICT, "err.language_already_exists",
                     "This language already exists")
    db.add(UiLocale(locale=lc, name=data.name.strip() or lc.upper()))
    await db.commit()
    return {"locale": lc}


@router.put("/locales/{locale}", status_code=204)
async def update_locale(locale: str, data: LocaleUpdate, _: User = Depends(require_admin),
                        db: AsyncSession = Depends(get_session)):
    """Rename a language or switch it off. Switching off hides it from the picker; the
    texts stay, so turning it back on loses nothing."""
    lc = _locale(locale)
    zeile = (await db.execute(select(UiLocale).where(UiLocale.locale == lc))).scalar_one_or_none()
    if zeile is None:
        zeile = UiLocale(locale=lc, name=NAMEN.get(lc, lc.upper()))
        db.add(zeile)
    if data.name is not None:
        zeile.name = data.name.strip() or NAMEN.get(lc, lc.upper())
    if data.enabled is not None:
        if lc == "de" and not data.enabled:
            raise Fehler(status.HTTP_400_BAD_REQUEST, "err.source_language_cannot_switched_off",
                         "The source language cannot be switched off")
        zeile.enabled = data.enabled
    await db.commit()


@router.get("/server-katalog")
async def server_katalog(locale: str = "", _: User = Depends(get_current_user)):
    """The German texts the server itself writes: notifications, the setup checklist.

    The browser knows only its own catalog. Without this list the admin area could not offer
    those texts for translation, and they would stay German forever while the rest of the
    interface switches. `locale` adds the shipped translation of that language, otherwise the
    admin area would count every one of these texts as untranslated.
    """
    lc = _locale(locale) if locale else ""
    return {"texte": server_texte.quelle(),
            "ausgeliefert": dict(server_texte.KATALOG.get(lc, {})) if lc else {}}


@router.get("/{locale}")
async def overrides(locale: str, user: User = Depends(get_current_user),
                    db: AsyncSession = Depends(get_session)):
    """The overrides of one language as a flat map, ready to merge in the browser."""
    lc = _locale(locale)
    rows = (await db.execute(select(UiTranslation).where(
        UiTranslation.locale == lc))).scalars().all()
    return {"locale": lc, "texte": {r.key: r.text for r in rows if r.text}}


@router.put("/{locale}/{key:path}", status_code=204)
async def set_text(locale: str, key: str, data: TextIn,
                   _: User = Depends(require_admin),
                   db: AsyncSession = Depends(get_session)):
    """Set one text. An empty text removes the override, which restores the shipped one."""
    lc = _locale(locale)
    vorhanden = (await db.execute(select(UiTranslation).where(
        UiTranslation.locale == lc, UiTranslation.key == key))).scalar_one_or_none()
    if not data.text.strip():
        if vorhanden is not None:
            await db.delete(vorhanden)
            await db.commit()
            server_texte.verwerfen()
        return
    if vorhanden is None:
        db.add(UiTranslation(locale=lc, key=key, text=data.text))
    else:
        vorhanden.text = data.text
    await db.commit()
    server_texte.verwerfen()


@router.post("/{locale}/import")
async def import_texts(locale: str, data: ImportIn, _: User = Depends(require_admin),
                       db: AsyncSession = Depends(get_session)):
    """Take a whole catalog. Meant for the round trip through a translation tool."""
    lc = _locale(locale)
    if data.ersetzen:
        await db.execute(delete(UiTranslation).where(UiTranslation.locale == lc))
    vorhanden = {r.key: r for r in (await db.execute(select(UiTranslation).where(
        UiTranslation.locale == lc))).scalars().all()}
    geschrieben = 0
    for key, text in (data.texte or {}).items():
        if not isinstance(text, str) or not text.strip():
            continue
        if key in vorhanden:
            vorhanden[key].text = text
        else:
            db.add(UiTranslation(locale=lc, key=str(key)[:200], text=text[:4000]))
        geschrieben += 1
    await db.commit()
    return {"locale": lc, "uebernommen": geschrieben}


@router.delete("/locales/{locale}", status_code=204)
async def drop_locale(locale: str, _: User = Depends(require_admin),
                      db: AsyncSession = Depends(get_session)):
    """Drop a whole language with its texts.

    A shipped language does not disappear: its catalog comes with the application, only the
    changes made here are gone. Whoever still has it selected falls back to the shipped
    texts, and for a language that was never shipped, to the source language.
    """
    lc = _locale(locale)
    if lc == "de":
        raise Fehler(status.HTTP_400_BAD_REQUEST, "err.source_language_stays",
                     "The source language stays")
    await db.execute(delete(UiTranslation).where(UiTranslation.locale == lc))
    await db.execute(delete(UiLocale).where(UiLocale.locale == lc))
    await db.commit()
