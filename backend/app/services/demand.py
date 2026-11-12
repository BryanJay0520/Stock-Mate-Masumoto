"""Editable demand inputs. Never writes inventory or computes future stock."""

from datetime import timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.errors import ApiError
from app.models import Ingredient, IngredientDemandSetting, User
from app.models.demand import QUANTITY_FIELDS, WEEKDAYS
from app.services.catalog import _audit
from app.services.inventory import _begin_write_transaction, _run_atomic


def get_ingredient(db: Session, ingredient_id: int) -> Ingredient:
    ingredient = db.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise ApiError(404, "ingredient_not_found", "Ingredient does not exist.")
    return ingredient


def settings_view(ingredient: Ingredient, row: IngredientDemandSetting | None) -> dict:
    quantities = {field: getattr(row, field) if row else None for field in QUANTITY_FIELDS}
    daily = [quantities[day] for day in WEEKDAYS]
    configured_days = sum(value is not None for value in daily)
    complete = configured_days == 7 and quantities["safety_stock"] is not None
    updated = row.updated_at if row else None
    if updated is not None and updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return {
        "ingredient_id": ingredient.id, "unit": ingredient.unit.abbreviation,
        "configured": row is not None, "enabled": row.enabled if row else False,
        **quantities, "notes": row.notes if row else None,
        "version": row.version if row else 0, "updated_at": updated,
        "configured_days": configured_days,
        "weekly_estimated_demand": sum(daily, Decimal("0.000")) if configured_days == 7 else None,
        "is_complete": complete,
        "forecast_ready": bool(row and row.enabled and complete and ingredient.status == "active"),
    }


def _snapshot(row: IngredientDemandSetting) -> dict:
    return {"enabled": row.enabled, "notes": row.notes, "version": row.version,
            **{field: str(getattr(row, field)) if getattr(row, field) is not None else None
               for field in QUANTITY_FIELDS}}


def update_settings(db: Session, *, ingredient_id: int, admin: User, data) -> dict:
    _begin_write_transaction(db)

    def work():
        ingredient = get_ingredient(db, ingredient_id)
        row = db.get(IngredientDemandSetting, ingredient_id)
        if data.expected_version != (row.version if row else 0):
            raise ApiError(409, "demand_settings_changed",
                           "Forecast settings changed since you opened them. Reopen the form to review the latest values.")
        before = _snapshot(row) if row else None
        if row is None:
            row = IngredientDemandSetting(ingredient_id=ingredient_id, enabled=False, version=1)
            db.add(row)
        else:
            row.version += 1
        for field in data.model_fields_set - {"expected_version"}:
            value = getattr(data, field)
            if field == "notes" and value is not None:
                value = value.strip() or None
            setattr(row, field, value)
        db.flush()
        _audit(db, admin, "ingredient_demand.update", "ingredient_demand", ingredient_id,
               before, _snapshot(row))
        return settings_view(ingredient, row)

    return _run_atomic(db, work)
