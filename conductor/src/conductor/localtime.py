"""Display-timezone formatting. Datetimes are stored in UTC (SQLite `func.now()` is UTC; Postgres
`now()` is tz-aware) — the DB stays UTC and every value shown in the console is converted to the
operator's local wall clock here. `%Z` renders EDT/EST automatically depending on the date."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

DISPLAY_TZ = ZoneInfo("America/New_York")


def to_display(dt: datetime) -> datetime:
    if dt.tzinfo is None:  # SQLite hands back naive UTC; tag it before converting
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(DISPLAY_TZ)


def format_display(dt: datetime, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return to_display(dt).strftime(f"{fmt} %Z")
