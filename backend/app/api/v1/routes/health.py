"""Health check endpoint.

Used by developers (and later by deployment monitoring) to confirm the app
is running and can reach the database. Returns 503 if the database check
fails so problems are visible instead of silent.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db

router = APIRouter()


@router.get("/health")
def health_check(request: Request, response: Response, db: Session = Depends(get_db)):
    settings = request.app.state.settings

    database_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        database_ok = False

    if not database_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ok" if database_ok else "degraded",
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.environment,
        "database": "ok" if database_ok else "unavailable",
        "businessTimezone": settings.business_timezone,
        "businessDayStartTime": settings.business_day_start_time,
        "serverTimeUtc": datetime.now(timezone.utc).isoformat(),
    }
