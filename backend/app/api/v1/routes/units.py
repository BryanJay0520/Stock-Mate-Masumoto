"""Measurement units (read-only).

The ingredient form needs the unit list to build its dropdown. Units are
reference data seeded with the demo dataset; there is deliberately no CRUD —
adding units is a migration/seed concern, not a runtime feature in Phase 1.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models import Unit, User

router = APIRouter()


@router.get("/units")
def list_units(
    _user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    units = db.scalars(select(Unit).order_by(Unit.id)).all()
    return [
        {"id": unit.id, "name": unit.name, "abbreviation": unit.abbreviation}
        for unit in units
    ]
