"""Measurements for the demo — runs INSIDE the backend container.

The metric series are written by the flow action `metric_record`; there is no endpoint for
them, and inventing one for a screenshot would be the wrong way round. So this piece runs
where the service lives:

    docker compose -f docs/demo/compose.yml exec -T backend python - < docs/demo/seed_metrics.py
"""
import asyncio
import datetime as dt
import math

from sqlalchemy import select

from app.db import SessionLocal
from app.models.user import User
from app.services import metrics


async def main() -> None:
    async with SessionLocal() as db:
        ada = (await db.execute(select(User).where(User.username == "ada"))).scalars().first()
        now = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
        # A tank that empties: 92 % down to 41 % over four weeks, with the noise of a real sensor.
        for day in range(28, -1, -1):
            level = 92 - (28 - day) * 1.8 + 1.2 * math.sin(day / 2.3)
            await metrics.record(db, ada.id, "heating.oil", round(level, 1),
                                 name="Heating oil", unit="%",
                                 ts=now - dt.timedelta(days=day))
        # And a second one that stays where it is — for the comparison in the list.
        for hour in range(72, -1, -3):
            await metrics.record(db, ada.id, "server.room_temperature",
                                 round(21.4 + 1.1 * math.sin(hour / 5.0), 1),
                                 name="Server room", unit="°C",
                                 ts=now - dt.timedelta(hours=hour))
        await db.commit()
    print("metrics: heating.oil, server.room_temperature")


asyncio.run(main())
