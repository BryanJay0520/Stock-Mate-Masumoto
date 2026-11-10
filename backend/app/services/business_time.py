"""Business-time helpers (decisions D5 / D18).

The database stores UTC. Operational records carry a ``business_date`` that
follows the configured business timezone AND the configurable day boundary:
with ``business_day_start_time = "04:00"``, 02:30 on Tuesday wall-clock still
belongs to Monday's business date (the restaurant is open past midnight).
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def parse_day_start(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def business_date_for(
    moment_utc: datetime, timezone_name: str, day_start: time
) -> date:
    """Map a UTC instant to the business date it belongs to."""
    if moment_utc.tzinfo is None:
        moment_utc = moment_utc.replace(tzinfo=timezone.utc)
    local = moment_utc.astimezone(ZoneInfo(timezone_name))
    local_date = local.date()
    if local.time() < day_start:
        local_date -= timedelta(days=1)
    return local_date
