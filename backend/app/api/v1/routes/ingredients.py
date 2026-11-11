"""Ingredient administration API.

Reads for all authenticated staff; mutations admin-only + CSRF + audited.
Ingredient payloads never carry stock-mutating fields — balances belong to
the ledger service alone (D14).
"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import csrf_protect, get_current_user, require_admin
from app.database import get_db
from app.errors import ApiError
from app.models import Ingredient, User
from app.schemas.catalog import (
    IngredientCreate,
    IngredientDetailOut,
    IngredientOut,
    IngredientUpdate,
    PurchasePackagingUpdate,
)
from app.services import catalog

router = APIRouter()


def _to_out(ingredient: Ingredient) -> IngredientOut:
    return IngredientOut(
        id=ingredient.id,
        name=ingredient.name,
        category=ingredient.category,
        unit_id=ingredient.unit_id,
        unit=ingredient.unit.abbreviation if ingredient.unit else "",
        min_safety_stock=ingredient.min_safety_stock,
        critical_threshold=ingredient.critical_threshold,
        usage_per_serving=ingredient.usage_per_serving,
        count_package_enabled=ingredient.count_package_enabled,
        count_package_name=ingredient.count_package_name,
        count_units_per_package=ingredient.count_units_per_package,
        count_package_version=ingredient.count_package_version,
        status=ingredient.status,
        primary_supplier_id=ingredient.primary_supplier_id,
        supplier_ids=[link.supplier_id for link in ingredient.supplier_links],
        created_at=ingredient.created_at,
    )


@router.get("/ingredients", response_model=list[IngredientOut])
def list_ingredients(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    search: str | None = None,
    category: str | None = None,
    status: str | None = Query(default=None, pattern="^(active|archived)$"),
):
    stmt = select(Ingredient).order_by(Ingredient.name)
    if search:
        stmt = stmt.where(Ingredient.name.ilike(f"%{search}%"))
    if category:
        stmt = stmt.where(Ingredient.category == category)
    if status:
        stmt = stmt.where(Ingredient.status == status)
    return [_to_out(i) for i in db.scalars(stmt).all()]


@router.post(
    "/ingredients", status_code=201, response_model=IngredientOut,
    dependencies=[Depends(csrf_protect)],
)
def create_ingredient(
    payload: IngredientCreate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    ingredient = catalog.create_ingredient(
        db, admin=admin, data=payload, settings=request.app.state.settings
    )
    return _to_out(ingredient)


@router.get("/ingredients/{ingredient_id}", response_model=IngredientDetailOut)
def get_ingredient(
    ingredient_id: int,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ingredient = db.get(Ingredient, ingredient_id)
    if ingredient is None:
        raise ApiError(404, "ingredient_not_found", "Ingredient not found.")
    detail = IngredientDetailOut(
        **_to_out(ingredient).model_dump(),
        current_quantity=ingredient.stock_state.current_quantity,
    )
    return detail


@router.patch(
    "/ingredients/{ingredient_id}", response_model=IngredientOut,
    dependencies=[Depends(csrf_protect)],
)
def update_ingredient(
    ingredient_id: int,
    payload: IngredientUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    ingredient = catalog.update_ingredient(
        db, admin=admin, ingredient_id=ingredient_id, data=payload
    )
    return _to_out(ingredient)


@router.patch(
    "/ingredients/{ingredient_id}/suppliers/{supplier_id}/purchase-packaging",
    response_model=dict, dependencies=[Depends(csrf_protect)],
)
def update_purchase_packaging(
    ingredient_id: int, supplier_id: int, payload: PurchasePackagingUpdate,
    admin: User = Depends(require_admin), db: Session = Depends(get_db),
):
    link = catalog.update_purchase_packaging(
        db, admin=admin, ingredient_id=ingredient_id, supplier_id=supplier_id, data=payload
    )
    return {"ingredient_id": ingredient_id, "supplier_id": supplier_id,
            "package_size": format(link.package_size, ".3f") if link.package_size else None,
            "purchase_package_name": link.purchase_package_name,
            "whole_packages_only": link.whole_packages_only}


@router.post(
    "/ingredients/{ingredient_id}/archive", response_model=IngredientOut,
    dependencies=[Depends(csrf_protect)],
)
def archive_ingredient(
    ingredient_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return _to_out(catalog.archive_ingredient(db, admin=admin, ingredient_id=ingredient_id))


@router.post(
    "/ingredients/{ingredient_id}/restore", response_model=IngredientOut,
    dependencies=[Depends(csrf_protect)],
)
def restore_ingredient(
    ingredient_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return _to_out(catalog.restore_ingredient(db, admin=admin, ingredient_id=ingredient_id))
