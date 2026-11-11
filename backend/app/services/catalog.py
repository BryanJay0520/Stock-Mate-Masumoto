"""Supplier and ingredient administration logic (decisions D11, D19, D24).

Invariants enforced here (in addition to the database constraints):

- Suppliers are archived, never deleted.
- A supplier cannot be archived while it is the PRIMARY supplier of an
  active ingredient — reassign first.
- A supplier assignment (primary or alternative) always references an
  ACTIVE supplier.
- The junction table is exactly {primary} ∪ {alternatives} — the primary is
  always also a junction row (double-enforced by the composite FK, D24).
- Ingredient CRUD never touches stock balances; an optional opening quantity
  becomes a proper ``opening_balance`` ledger entry in the same atomic
  transaction (D23).
- Every mutation writes an audit log row.
"""

import json
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import ApiError
from app.models import (
    AuditLog,
    Ingredient,
    IngredientStockState,
    InventoryTransaction,
    Supplier,
    SupplierIngredient,
    Unit,
    User,
)
from app.models.supplier import STATUS_ACTIVE
from app.services.business_time import business_date_for, parse_day_start
from app.services.inventory import _begin_write_transaction, _run_atomic


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _audit(
    db: Session, admin: User, action: str, entity_type: str,
    entity_id: int | None, before: dict | None, after: dict | None,
) -> None:
    db.add(AuditLog(
        user_id=admin.id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_json=json.dumps(before) if before is not None else None,
        after_json=json.dumps(after) if after is not None else None,
        created_at=_now(),
    ))


def _supplier_snapshot(supplier: Supplier) -> dict:
    return {
        "name": supplier.name,
        "contact_name": supplier.contact_name,
        "phone": supplier.phone,
        "email": supplier.email,
        "notes": supplier.notes,
        "contact_method": supplier.contact_method,
        "ordering_url": supplier.ordering_url,
        "ordering_procedures": supplier.ordering_procedures,
        "status": supplier.status,
    }


def _ingredient_snapshot(ingredient: Ingredient) -> dict:
    return {
        "name": ingredient.name,
        "category": ingredient.category,
        "unit_id": ingredient.unit_id,
        "min_safety_stock": str(ingredient.min_safety_stock),
        "critical_threshold": str(ingredient.critical_threshold),
        "usage_per_serving": str(ingredient.usage_per_serving),
        "count_package_enabled": ingredient.count_package_enabled,
        "count_package_name": ingredient.count_package_name,
        "count_units_per_package": str(ingredient.count_units_per_package)
        if ingredient.count_units_per_package is not None else None,
        "count_package_version": ingredient.count_package_version,
        "primary_supplier_id": ingredient.primary_supplier_id,
        "status": ingredient.status,
    }


def _set_count_packaging(ingredient: Ingredient, data) -> None:
    """Count conversion configuration; never convert an existing balance."""
    fields = data.model_fields_set
    if not fields.intersection({"count_package_enabled", "count_package_name",
                                "count_units_per_package"}):
        return
    enabled = data.count_package_enabled
    if enabled is None:
        enabled = ingredient.count_package_enabled
    name = data.count_package_name if "count_package_name" in fields else ingredient.count_package_name
    size = (data.count_units_per_package if "count_units_per_package" in fields
            else ingredient.count_units_per_package)
    if enabled:
        name = name.strip() if name is not None else ""
        if not name or size is None or size <= 0:
            raise ApiError(400, "invalid_count_packaging",
                           "Enabled counting packages need a name and a positive size.")
    else:
        name, size = None, None
    if (ingredient.count_package_enabled, ingredient.count_package_name,
            ingredient.count_units_per_package) != (enabled, name, size):
        ingredient.count_package_enabled = enabled
        ingredient.count_package_name = name
        ingredient.count_units_per_package = size
        ingredient.count_package_version = (ingredient.count_package_version or 0) + 1


# --- Suppliers --------------------------------------------------------------

def create_supplier(db: Session, *, admin: User, data) -> Supplier:
    _begin_write_transaction(db)

    def work() -> Supplier:
        if db.scalar(select(Supplier).where(Supplier.name == data.name)):
            raise ApiError(409, "duplicate_supplier", "A supplier with that name already exists.")
        supplier = Supplier(
            name=data.name,
            contact_name=data.contact_name,
            phone=data.phone,
            email=data.email,
            notes=data.notes,
            contact_method=data.contact_method,
            ordering_url=data.ordering_url,
            ordering_procedures=data.ordering_procedures,
            status="active",
        )
        db.add(supplier)
        db.flush()
        _audit(db, admin, "supplier.create", "supplier", supplier.id, None,
               _supplier_snapshot(supplier))
        return supplier

    return _run_atomic(db, work)


def update_supplier(db: Session, *, admin: User, supplier_id: int, data) -> Supplier:
    _begin_write_transaction(db)

    def work() -> Supplier:
        supplier = db.get(Supplier, supplier_id)
        if supplier is None:
            raise ApiError(404, "supplier_not_found", "Supplier not found.")
        before = _supplier_snapshot(supplier)
        if data.name is not None and data.name != supplier.name:
            clash = db.scalar(select(Supplier).where(Supplier.name == data.name))
            if clash is not None:
                raise ApiError(409, "duplicate_supplier", "A supplier with that name already exists.")
        for field in ("name", "contact_name", "phone", "email", "notes"):
            value = getattr(data, field)
            if value is not None:
                setattr(supplier, field, value)
        # Contact-instruction fields support explicit clearing via null.
        for field in ("contact_method", "ordering_url", "ordering_procedures"):
            if field in data.model_fields_set:
                setattr(supplier, field, getattr(data, field))
        _audit(db, admin, "supplier.update", "supplier", supplier.id, before,
               _supplier_snapshot(supplier))
        return supplier

    return _run_atomic(db, work)


def archive_supplier(db: Session, *, admin: User, supplier_id: int) -> Supplier:
    _begin_write_transaction(db)

    def work() -> Supplier:
        supplier = db.get(Supplier, supplier_id)
        if supplier is None:
            raise ApiError(404, "supplier_not_found", "Supplier not found.")
        if supplier.status == "archived":
            return supplier  # idempotent

        # Guard: never archive a supplier that is still PRIMARY for an
        # active ingredient (D19). Historical records stay untouched.
        blocking = db.scalars(
            select(Ingredient).where(
                Ingredient.primary_supplier_id == supplier_id,
                Ingredient.status == "active",
            )
        ).all()
        if blocking:
            names = ", ".join(sorted(i.name for i in blocking))
            raise ApiError(
                409, "archive_blocked",
                f"Supplier is the primary supplier of active ingredients: {names}. "
                "Reassign a new primary supplier for them first.",
            )

        # Guard 2 (Phase 2): never archive a supplier with OPEN purchase
        # orders — cancel or receive them first.
        from app.models.ordering import OPEN_ORDER_STATUSES, PurchaseOrder
        open_orders = db.scalars(
            select(PurchaseOrder).where(
                PurchaseOrder.supplier_id == supplier_id,
                PurchaseOrder.status.in_(OPEN_ORDER_STATUSES),
            )
        ).all()
        if open_orders:
            ids = ", ".join(f"#{o.id} ({o.status})" for o in open_orders)
            raise ApiError(
                409, "archive_blocked",
                f"Supplier has open purchase orders: {ids}. "
                "Cancel them or confirm their deliveries first.",
            )

        before = _supplier_snapshot(supplier)
        supplier.status = "archived"
        _audit(db, admin, "supplier.archive", "supplier", supplier.id, before,
               _supplier_snapshot(supplier))
        return supplier

    return _run_atomic(db, work)


def reactivate_supplier(db: Session, *, admin: User, supplier_id: int) -> Supplier:
    _begin_write_transaction(db)

    def work() -> Supplier:
        supplier = db.get(Supplier, supplier_id)
        if supplier is None:
            raise ApiError(404, "supplier_not_found", "Supplier not found.")
        if supplier.status == "active":
            return supplier  # idempotent
        before = _supplier_snapshot(supplier)
        supplier.status = "active"
        _audit(db, admin, "supplier.reactivate", "supplier", supplier.id, before,
               _supplier_snapshot(supplier))
        return supplier

    return _run_atomic(db, work)


# --- Ingredients --------------------------------------------------------------

def _validate_supplier_set(
    db: Session, primary_id: int | None, alternative_ids: list[int]
) -> set[int]:
    """All assigned suppliers must exist and be active; primary must be in the set."""
    supplier_ids = set(alternative_ids)
    if primary_id is not None:
        supplier_ids.add(primary_id)
    for supplier_id in supplier_ids:
        supplier = db.get(Supplier, supplier_id)
        if supplier is None:
            raise ApiError(400, "supplier_not_found", f"Supplier {supplier_id} does not exist.")
        if supplier.status != STATUS_ACTIVE:
            raise ApiError(
                400, "supplier_inactive",
                f"Supplier '{supplier.name}' is archived and cannot be assigned.",
            )
    return supplier_ids


def _add_missing_links(db: Session, ingredient: Ingredient, supplier_ids: set[int]) -> None:
    existing = {link.supplier_id for link in ingredient.supplier_links}
    for supplier_id in supplier_ids - existing:
        db.add(SupplierIngredient(supplier_id=supplier_id, ingredient_id=ingredient.id))
    db.flush()


def _remove_extra_links(db: Session, ingredient: Ingredient, supplier_ids: set[int]) -> None:
    existing = {link.supplier_id: link for link in ingredient.supplier_links}
    for supplier_id in existing.keys() - supplier_ids:
        db.delete(existing[supplier_id])
    db.flush()


def _sync_junction(
    db: Session, ingredient: Ingredient, supplier_ids: set[int], primary_id: int | None
) -> None:
    """Make the junction exactly match the supplier set and move the primary.

    Order matters because of the composite FK (D24): the new primary's
    junction row must exist BEFORE ``primary_supplier_id`` points at it, and
    the old primary's row may only be deleted AFTER the pointer moved away.
    """
    _add_missing_links(db, ingredient, supplier_ids)
    ingredient.primary_supplier_id = primary_id
    db.flush()
    _remove_extra_links(db, ingredient, supplier_ids)


def create_ingredient(db: Session, *, admin: User, data, settings: Settings) -> Ingredient:
    _begin_write_transaction(db)

    def work() -> Ingredient:
        if db.scalar(select(Ingredient).where(Ingredient.name == data.name)):
            raise ApiError(409, "duplicate_ingredient", "An ingredient with that name already exists.")
        if db.get(Unit, data.unit_id) is None:
            raise ApiError(400, "unit_not_found", f"Unit {data.unit_id} does not exist.")
        supplier_ids = _validate_supplier_set(
            db, data.primary_supplier_id, data.alternative_supplier_ids
        )

        ingredient = Ingredient(
            name=data.name,
            category=data.category,
            unit_id=data.unit_id,
            min_safety_stock=data.min_safety_stock,
            critical_threshold=data.critical_threshold,
            usage_per_serving=data.usage_per_serving,
            status="active",
        )
        _set_count_packaging(ingredient, data)
        db.add(ingredient)
        db.flush()

        # Junction rows first, THEN the primary pointer (circular FK, D24).
        _sync_junction(db, ingredient, supplier_ids, data.primary_supplier_id)

        opening = data.opening_quantity or Decimal("0")
        state = IngredientStockState(
            ingredient_id=ingredient.id,
            current_quantity=opening,
            confidence="high" if opening > 0 else "low",
            version=1 if opening > 0 else 0,
        )
        now = _now()
        if opening > 0:
            # D23: the starting quantity is a ledger transaction, so the
            # ledger alone can always rebuild the cached balance.
            state.last_counted_at = now
            state.last_counted_by = admin.id
            state.last_transaction_at = now
            db.add(InventoryTransaction(
                ingredient_id=ingredient.id,
                type="opening_balance",
                quantity_delta=opening,
                resulting_quantity=opening,
                reason="initial_stock",
                mode=None,
                entered_by=admin.id,
                business_date=business_date_for(
                    now, settings.business_timezone,
                    parse_day_start(settings.business_day_start_time),
                ),
                created_at=now,
            ))
        db.add(state)
        db.flush()
        _audit(db, admin, "ingredient.create", "ingredient", ingredient.id, None,
               _ingredient_snapshot(ingredient))
        return ingredient

    ingredient = _run_atomic(db, work)
    # expire_on_commit=False (D15 sessions): reload so supplier_links reflects
    # the junction rows written inside the transaction.
    db.refresh(ingredient)
    return ingredient


def update_ingredient(db: Session, *, admin: User, ingredient_id: int, data) -> Ingredient:
    _begin_write_transaction(db)

    def work() -> Ingredient:
        ingredient = db.get(Ingredient, ingredient_id)
        if ingredient is None:
            raise ApiError(404, "ingredient_not_found", "Ingredient not found.")
        before = _ingredient_snapshot(ingredient)

        if data.name is not None and data.name != ingredient.name:
            clash = db.scalar(select(Ingredient).where(Ingredient.name == data.name))
            if clash is not None:
                raise ApiError(409, "duplicate_ingredient", "An ingredient with that name already exists.")
            ingredient.name = data.name
        if data.category is not None:
            ingredient.category = data.category
        if data.unit_id is not None:
            if db.get(Unit, data.unit_id) is None:
                raise ApiError(400, "unit_not_found", f"Unit {data.unit_id} does not exist.")
            if data.unit_id != ingredient.unit_id and db.scalar(
                select(InventoryTransaction.id).where(
                    InventoryTransaction.ingredient_id == ingredient.id).limit(1)
            ) is not None:
                raise ApiError(409, "unit_change_with_history",
                               "The stock unit cannot change after stock history exists.")
            ingredient.unit_id = data.unit_id
        if data.min_safety_stock is not None:
            ingredient.min_safety_stock = data.min_safety_stock
        if data.critical_threshold is not None:
            ingredient.critical_threshold = data.critical_threshold
        if data.usage_per_serving is not None:
            ingredient.usage_per_serving = data.usage_per_serving
        _set_count_packaging(ingredient, data)

        # Supplier reassignment: primary changes only via this validated path.
        if data.primary_supplier_id is not None or data.alternative_supplier_ids is not None:
            primary = (
                data.primary_supplier_id
                if data.primary_supplier_id is not None
                else ingredient.primary_supplier_id
            )
            alternatives = (
                data.alternative_supplier_ids
                if data.alternative_supplier_ids is not None
                else [l.supplier_id for l in ingredient.supplier_links
                      if l.supplier_id != ingredient.primary_supplier_id]
            )
            supplier_ids = _validate_supplier_set(db, primary, alternatives)
            _sync_junction(db, ingredient, supplier_ids, primary)

        _audit(db, admin, "ingredient.update", "ingredient", ingredient.id, before,
               _ingredient_snapshot(ingredient))
        return ingredient

    ingredient = _run_atomic(db, work)
    db.refresh(ingredient)  # see create_ingredient: refresh stale relationships
    return ingredient


def update_purchase_packaging(db: Session, *, admin: User, ingredient_id: int,
                              supplier_id: int, data) -> SupplierIngredient:
    """Store a supplier's buying multiple without touching count settings or stock."""
    _begin_write_transaction(db)

    def work() -> SupplierIngredient:
        link = db.get(SupplierIngredient, (supplier_id, ingredient_id))
        if link is None:
            raise ApiError(404, "supplier_link_not_found",
                           "This supplier is not assigned to the ingredient.")
        name = data.purchase_package_name.strip() if data.purchase_package_name else None
        if data.package_size is None:
            if name or data.whole_packages_only:
                raise ApiError(400, "invalid_purchase_packaging",
                               "A package size is required for whole-package purchasing.")
        elif not name:
            raise ApiError(400, "invalid_purchase_packaging",
                           "A purchasing package name is required with its size.")
        before = {"package_size": str(link.package_size) if link.package_size else None,
                  "purchase_package_name": link.purchase_package_name,
                  "whole_packages_only": link.whole_packages_only}
        link.package_size = data.package_size
        link.purchase_package_name = name
        link.whole_packages_only = data.whole_packages_only
        after = {"package_size": str(link.package_size) if link.package_size else None,
                 "purchase_package_name": link.purchase_package_name,
                 "whole_packages_only": link.whole_packages_only}
        _audit(db, admin, "supplier_ingredient.purchase_packaging", "ingredient",
               ingredient_id, before, {"supplier_id": supplier_id, **after})
        return link

    return _run_atomic(db, work)


def _set_ingredient_status(db: Session, *, admin: User, ingredient_id: int,
                           status: str, action: str) -> Ingredient:
    _begin_write_transaction(db)

    def work() -> Ingredient:
        ingredient = db.get(Ingredient, ingredient_id)
        if ingredient is None:
            raise ApiError(404, "ingredient_not_found", "Ingredient not found.")
        if ingredient.status == status:
            return ingredient  # idempotent
        before = _ingredient_snapshot(ingredient)
        ingredient.status = status
        _audit(db, admin, action, "ingredient", ingredient.id, before,
               _ingredient_snapshot(ingredient))
        return ingredient

    return _run_atomic(db, work)


def archive_ingredient(db: Session, *, admin: User, ingredient_id: int) -> Ingredient:
    return _set_ingredient_status(
        db, admin=admin, ingredient_id=ingredient_id,
        status="archived", action="ingredient.archive",
    )


def restore_ingredient(db: Session, *, admin: User, ingredient_id: int) -> Ingredient:
    return _set_ingredient_status(
        db, admin=admin, ingredient_id=ingredient_id,
        status="active", action="ingredient.restore",
    )
