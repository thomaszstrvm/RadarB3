from __future__ import annotations

import calendar
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def instant(value: str | datetime) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if dt.tzinfo is None:
        raise ValueError("timestamp_without_timezone")
    return dt.astimezone(UTC)


def iso(value: datetime | str | None = None) -> str:
    return instant(value or datetime.now(UTC)).isoformat(timespec="microseconds").replace("+00:00", "Z")


def session_not_after(session_date: str, cutoff: str | datetime) -> bool:
    """Compare a session's civil date to the cutoff day in Sao Paulo.

    A daily bar's UTC timestamp can be only a date marker, not a trade time.
    It must not replace the session date in this comparison.
    """
    return date.fromisoformat(session_date) <= instant(cutoff).astimezone(SAO_PAULO).date()


def news_window(as_of: str | datetime) -> tuple[str, str]:
    end = instant(as_of)
    local = end.astimezone(SAO_PAULO)
    month_index = local.year * 12 + local.month - 1 - 3
    year, month = divmod(month_index, 12)
    month += 1
    day = min(local.day, calendar.monthrange(year, month)[1])
    start = datetime.combine(date(year, month, day), time.min, SAO_PAULO)
    return iso(start), iso(end)


def publication_in_window(value: str | None, precision: str, as_of: str) -> bool:
    if not value:
        return False
    start, end = news_window(as_of)
    if precision == "date":
        parsed = date.fromisoformat(value[:10])
        return instant(start).astimezone(SAO_PAULO).date() <= parsed <= instant(end).astimezone(SAO_PAULO).date()
    return instant(start) <= instant(value) <= instant(end)
