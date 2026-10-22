"""Local-time helpers. Data is stored in UTC; "today" and "yesterday" mean the user's local day."""

from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

from app.config.settings import get_settings


def local_tz() -> tzinfo:
    name = get_settings().timezone
    if name:
        return ZoneInfo(name)
    return datetime.now().astimezone().tzinfo or timezone.utc  # the machine's zone


def local_day_start(now: datetime, days_ago: int = 0) -> datetime:
    """UTC instant of local midnight `days_ago` days before `now`'s local date."""
    local = now.astimezone(local_tz())
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)
    # Recompute the offset at that midnight (DST-safe for named zones).
    midnight = midnight.replace(tzinfo=None).replace(tzinfo=local_tz())
    return midnight.astimezone(timezone.utc)
