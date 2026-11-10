"""Supplier administration API.

Reads are available to all authenticated staff (they need supplier info for
ordering work); mutations are admin-only + CSRF-protected and audited.
Suppliers are archived, never deleted (D11/D19).
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import csrf_protect, get_current_user, require_admin
from app.database import get_db
from app.errors import ApiError
from app.models import Supplier, SupplierIngredient, Ingredient, User
from app.schemas.catalog import (
    SupplierDetailOut,
    SupplierIn,
    SupplierIngredientLinkOut,
    SupplierOut,
    SupplierUpdate,
)
from app.services import catalog

router = APIRouter()


@router.get("/suppliers", response_model=list[SupplierOut])
def list_suppliers(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    search: str | None = None,
    status: str | None = Query(default=None, pattern="^(active|archived)$"),
):
    stmt = select(Supplier).order_by(Supplier.name)
    if search:
        stmt = stmt.where(Supplier.name.ilike(f"%{search}%"))
    if status:
        stmt = stmt.where(Supplier.status == status)
    return db.scalars(stmt).all()


@router.post(
    "/suppliers", status_code=201, response_model=SupplierOut,
    dependencies=[Depends(csrf_protect)],
)
def create_supplier(
    payload: SupplierIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return catalog.create_supplier(db, admin=admin, data=payload)


@router.get("/suppliers/{supplier_id}", response_model=SupplierDetailOut)
def get_supplier(
    supplier_id: int,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    supplier = db.get(Supplier, supplier_id)
    if supplier is None:
        raise ApiError(404, "supplier_not_found", "Supplier not found.")
    links = db.execute(
        select(SupplierIngredient, Ingredient)
        .join(Ingredient, SupplierIngredient.ingredient_id == Ingredient.id)
        .where(SupplierIngredient.supplier_id == supplier_id)
        .order_by(Ingredient.name)
    ).all()
    return SupplierDetailOut(
        **SupplierOut.model_validate(supplier).model_dump(),
        ingredients=[
            SupplierIngredientLinkOut(
                ingredient_id=ingredient.id,
                ingredient_name=ingredient.name,
                ingredient_status=ingredient.status,
                is_primary=ingredient.primary_supplier_id == supplier_id,
            )
            for _link, ingredient in links
        ],
    )


@router.patch(
    "/suppliers/{supplier_id}", response_model=SupplierOut,
    dependencies=[Depends(csrf_protect)],
)
def update_supplier(
    supplier_id: int,
    payload: SupplierUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return catalog.update_supplier(db, admin=admin, supplier_id=supplier_id, data=payload)


@router.post(
    "/suppliers/{supplier_id}/archive", response_model=SupplierOut,
    dependencies=[Depends(csrf_protect)],
)
def archive_supplier(
    supplier_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return catalog.archive_supplier(db, admin=admin, supplier_id=supplier_id)


@router.post(
    "/suppliers/{supplier_id}/reactivate", response_model=SupplierOut,
    dependencies=[Depends(csrf_protect)],
)
def reactivate_supplier(
    supplier_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return catalog.reactivate_supplier(db, admin=admin, supplier_id=supplier_id)
