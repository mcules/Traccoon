"""Globale App-Einstellungen (Key-Value, DB-persistent)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.ops import AppSetting


async def get_setting(db: AsyncSession, key: str, default: str = "") -> str:
    row = (await db.execute(select(AppSetting).where(AppSetting.key == key))).scalar_one_or_none()
    return row.value if row else default


async def set_setting(db: AsyncSession, key: str, value: str) -> None:
    row = (await db.execute(select(AppSetting).where(AppSetting.key == key))).scalar_one_or_none()
    if row is None:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value
    await db.commit()
