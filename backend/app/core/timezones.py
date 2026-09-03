"""What time it is for a person.

One question, asked in three places until now — the gatekeeper, the schedule and
the calendar all had to know what "eight o'clock" means for the person whose
night, job or appointment it is. Two of them carried a copy of the answer.

It lives here because it is a leaf: it imports nothing of the application, so a
module may reach for it without pulling a service, its Redis connection and its
background state into the import graph along the way. That is not a style
preference — importing the gatekeeper from the API router for this one function
was enough to change the order in which the test session replaces its modules,
and thirteen tests in three unrelated files went red.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

# The fallback when nobody has set a zone.
STD_TZ = ZoneInfo("Europe/Berlin")


def zone_of(user) -> ZoneInfo:
    """The timezone of a person.

    An unknown zone — a typo, an old state of the data — must not blow up
    whatever asked, hence the fallback.
    """
    try:
        return ZoneInfo(getattr(user, "timezone", "") or STD_TZ.key)
    except Exception:                    # noqa: BLE001 - see the docstring
        return STD_TZ
