from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.database import get_db
from app.models import User
from app.errors import ApiError
from app.schemas.forecast import ForecastCalendar
from app.services.forecast import generate_forecast

router = APIRouter()


@router.get("/forecast/calendar", response_model=ForecastCalendar)
def calendar(request: Request, days: int = Query(default=7, description="7, 14, or 30 days", json_schema_extra={"enum": [7, 14, 30]}),
             ingredient_id: int | None = Query(default=None, gt=0),
             today_remaining_usage: Decimal | None = Query(default=None, ge=0, max_digits=14, decimal_places=3),
             today_usage_ingredient_id: int | None = Query(default=None, gt=0),
             today_usage_date: date | None = None,
             user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = today_usage_ingredient_id or ingredient_id
    if today_remaining_usage is not None and target is None:
        raise ApiError(422, "missing_override_ingredient", "Select an ingredient for today's remaining usage override.")
    if today_remaining_usage is None and (today_usage_ingredient_id is not None or today_usage_date is not None):
        raise ApiError(422, "missing_usage_override", "Supply an explicit remaining usage quantity, including zero, or omit override parameters.")
    return generate_forecast(db, settings=request.app.state.settings, days=days, ingredient_id=ingredient_id,
        remaining_usage_overrides={target: today_remaining_usage} if today_remaining_usage is not None else None,
        override_business_date=today_usage_date)
