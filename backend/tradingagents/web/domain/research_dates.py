"""The US research date follows New York, independently of the host's locale."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def current_research_date(now=None):
    value = now if now is not None else datetime.now(UTC)
    if value.tzinfo is None:
        raise ValueError("research date requires an aware instant")
    return value.astimezone(ZoneInfo("America/New_York")).date().isoformat()
