"""Globale App-Einstellungen (Key-Value, DB-persistent)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.ops import AppSetting

# Spacing (px) between the nodes when the process editor runs "arrange". A single value for
# horizontal as for vertical: even distances read more calmly than the earlier values, which
# differed by direction.
LAYOUT_GAP_KEY = "workflow_layout_gap"
LAYOUT_GAP_DEFAULT = 40


async def get_layout_gap(db: AsyncSession) -> int:
    raw = await get_setting(db, LAYOUT_GAP_KEY, "")
    return int(raw) if raw.isdigit() and 8 <= int(raw) <= 400 else LAYOUT_GAP_DEFAULT


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
