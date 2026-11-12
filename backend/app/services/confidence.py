"""Read-only confidence from the existing count facts and stored discrepancy flag."""
from datetime import datetime, timezone


def as_utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def count_confidence(state, *, now_utc: datetime, high_hours: int = 24, medium_hours: int = 72) -> dict:
    counted = as_utc(state.last_counted_at) if state and state.last_counted_at else None
    age_seconds = (now_utc - counted).total_seconds() if counted else None
    age = int(age_seconds // 3600) if age_seconds is not None and age_seconds >= 0 else None
    warnings = []
    if counted is None:
        warnings.append("No verified physical count is recorded.")
    elif age_seconds < 0:
        warnings.append("The recorded physical count is later than the forecast timestamp.")
    elif age_seconds > high_hours * 3600:
        warnings.append("The physical count is stale; verify stock before relying on this projection.")
    if state and state.unresolved_discrepancy:
        warnings.append("An unresolved stock discrepancy requires verification.")
    if age is None or (state and state.unresolved_discrepancy):
        level = "low"
    else:
        freshness = "high" if age_seconds <= high_hours * 3600 else "medium" if age_seconds <= medium_hours * 3600 else "low"
        rank = {"low": 0, "medium": 1, "high": 2}
        # Fresh timestamps never upgrade an existing low/discrepancy flag.
        level = min((freshness, state.confidence), key=lambda item: rank[item])
    return {"level": level, "last_verified_at": counted, "age_hours": age,
            "stale": age_seconds is None or age_seconds < 0 or age_seconds > high_hours * 3600,
            "high_freshness_hours": high_hours, "medium_freshness_hours": medium_hours,
            "warnings": warnings}
