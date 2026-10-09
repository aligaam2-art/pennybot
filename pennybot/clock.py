"""Market clock: US session times shown in the local (display) time zone.

All exchange logic uses America/New_York, so US daylight saving changes
are handled automatically by zoneinfo.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

PREMARKET_OPEN = time(4, 0)
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
AFTERHOURS_CLOSE = time(20, 0)


def now_et() -> datetime:
    return datetime.now(ET)


def session(at: datetime | None = None) -> str:
    """Return 'premarket', 'regular', 'afterhours' or 'closed' (holidays not detected)."""
    t = (at or now_et()).astimezone(ET)
    if t.weekday() >= 5:
        return "closed"
    tod = t.time()
    if PREMARKET_OPEN <= tod < REGULAR_OPEN:
        return "premarket"
    if REGULAR_OPEN <= tod < REGULAR_CLOSE:
        return "regular"
    if REGULAR_CLOSE <= tod < AFTERHOURS_CLOSE:
        return "afterhours"
    return "closed"


def session_times_local(display_tz: str, on: datetime | None = None) -> dict[str, datetime]:
    """Key US session times for the given day, converted to the display time zone."""
    tz = ZoneInfo(display_tz)
    day = (on or now_et()).astimezone(ET).date()

    def local(t: time) -> datetime:
        return datetime.combine(day, t, tzinfo=ET).astimezone(tz)

    open_ = local(REGULAR_OPEN)
    return {
        "premarket_opens": local(PREMARKET_OPEN),
        "premarket_scan_from": open_ - timedelta(minutes=60),
        "premarket_scan_to": open_ - timedelta(minutes=45),
        "market_opens": open_,
        "market_closes": local(REGULAR_CLOSE),
    }


def fmt_local(dt: datetime, display_tz: str) -> str:
    return dt.astimezone(ZoneInfo(display_tz)).strftime("%a %d %b %H:%M")
