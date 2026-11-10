"""Server-side session lifecycle: create, validate, revoke (decision D7).

Cookies/constants are defined here so routes and dependencies share them.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.tokens import generate_session_token, hash_token
from app.models import User, UserSession

SESSION_COOKIE = "stockmate_session"
CSRF_COOKIE = "stockmate_csrf"
CSRF_HEADER = "x-csrf-token"


def _as_utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes; treat stored values as UTC (D5)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def create_session(db: Session, user: User, ttl_hours: int) -> tuple[str, UserSession]:
    """Create a session row and return ``(raw_token, row)``.

    Only the caller (the login route) ever sees the raw token.
    """
    token = generate_session_token()
    now = datetime.now(timezone.utc)
    row = UserSession(
        token_hash=hash_token(token),
        user_id=user.id,
        created_at=now,
        expires_at=now + timedelta(hours=ttl_hours),
    )
    db.add(row)
    db.commit()
    return token, row


def find_valid_session(db: Session, raw_token: str | None) -> UserSession | None:
    """Return the session row iff it exists, is unrevoked and unexpired."""
    if not raw_token:
        return None
    row = db.scalar(
        select(UserSession).where(UserSession.token_hash == hash_token(raw_token))
    )
    if row is None or row.revoked_at is not None:
        return None
    if _as_utc(row.expires_at) <= datetime.now(timezone.utc):
        return None
    return row


def revoke_session(db: Session, row: UserSession) -> None:
    row.revoked_at = datetime.now(timezone.utc)
    db.commit()
