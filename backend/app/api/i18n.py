"""Translation overrides: read them for the running UI, edit them as an admin.

The shipped catalogs are bundled with the frontend, so the browser has German and English
before it talks to the server at all. This endpoint adds what a person changed at runtime
and any language that was created without a release. Keeping both apart is deliberate:
texts that belong to the code stay in the repository and go through review, while a typo
in a label should not need a deployment.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..models.i18n import UiTranslation
from ..models.user import User
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
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ungültige Sprachkennung")
    return kurz


@router.get("/locales")
async def list_locales(user: User = Depends(get_current_user),
                       db: AsyncSession = Depends(get_session)):
    """Which languages exist, and how many texts each one carries here."""
    rows = (await db.execute(
        select(UiTranslation.locale, func.count(UiTranslation.id))
        .group_by(UiTranslation.locale))).all()
    gezaehlt = {locale: anzahl for locale, anzahl in rows}
    alle = sorted(set(EINGEBAUT) | set(gezaehlt))
    return [{"locale": l, "eigene_texte": gezaehlt.get(l, 0), "eingebaut": l in EINGEBAUT}
            for l in alle]


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
        return
    if vorhanden is None:
        db.add(UiTranslation(locale=lc, key=key, text=data.text))
    else:
        vorhanden.text = data.text
    await db.commit()


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


@router.delete("/{locale}", status_code=204)
async def drop_locale(locale: str, _: User = Depends(require_admin),
                      db: AsyncSession = Depends(get_session)):
    """Drop a whole language. The shipped ones fall back to their catalog, they do not
    disappear."""
    await db.execute(delete(UiTranslation).where(UiTranslation.locale == _locale(locale)))
    await db.commit()
