"""Shared column helpers used by several models."""

from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column


def utcnow() -> datetime:
    """Single place that defines "now" — always UTC (decision D5)."""
    return datetime.now(timezone.utc)


class TimestampMixin:
    """Adds created_at / updated_at columns (UTC) to a model."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
