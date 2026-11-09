"""Schema and referential-integrity tests (Phase 1, Step 2).

These tests exercise the REAL database through the ORM, proving the
constraints required by decisions D14, D19 and D24 actually hold — including
SQLite foreign-key enforcement, the composite primary-supplier FK, unique
constraints, and Decimal quantity round-trips.
"""

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import Ingredient, Supplier, SupplierIngredient, Unit


@pytest.fixture()
def db(client):
    """A raw ORM session against the test app's (empty, migrated) database."""
    session = client.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def unit(db):
    unit = Unit(name="Portion", abbreviation="ptn")
    db.add(unit)
    db.commit()
    return unit


def _make_ingredient(name: str, unit_id: int) -> Ingredient:
    return Ingredient(
        name=name, category="Noodles", unit_id=unit_id,
        min_safety_stock=0, critical_threshold=0, usage_per_serving=1, status="active",
    )


def test_sqlite_foreign_keys_are_enforced(db):
    """PRAGMA foreign_keys must be ON (D24) — an orphan junction row fails."""
    result = db.execute(text("PRAGMA foreign_keys")).scalar()
    assert result == 1

    db.add(SupplierIngredient(supplier_id=999, ingredient_id=999))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_junction_pair_is_unique(db, unit):
    supplier = Supplier(name="S1", status="active")
    ingredient = _make_ingredient("Noodles", unit.id)
    db.add_all([supplier, ingredient])
    db.commit()
    db.add(SupplierIngredient(supplier_id=supplier.id, ingredient_id=ingredient.id))
    db.commit()

    db.add(SupplierIngredient(supplier_id=supplier.id, ingredient_id=ingredient.id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_primary_supplier_must_be_a_junction_row(db, unit):
    """The composite FK (D24): assigning a non-junction supplier must fail."""
    supplier = Supplier(name="S1", status="active")
    other_supplier = Supplier(name="S2", status="active")
    ingredient = _make_ingredient("Noodles", unit.id)
    db.add_all([supplier, other_supplier, ingredient])
    db.commit()
    db.add(SupplierIngredient(supplier_id=supplier.id, ingredient_id=ingredient.id))
    db.commit()

    # S2 has NO junction row for this ingredient -> composite FK must reject.
    ingredient.primary_supplier_id = other_supplier.id
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    # S1 HAS a junction row -> assignment succeeds.
    ingredient.primary_supplier_id = supplier.id
    db.commit()
    assert ingredient.primary_supplier_id == supplier.id


def test_primary_supplier_can_be_null(db, unit):
    ingredient = Ingredient(
        name="Mystery", category="Other", unit_id=unit.id,
        min_safety_stock=0, critical_threshold=0, usage_per_serving=1,
        status="active", primary_supplier_id=None,
    )
    db.add(ingredient)
    db.commit()
    assert ingredient.id is not None


def test_decimal_quantities_round_trip(db, unit):
    """NUMERIC(14,3) values come back as exact Decimals (D4)."""
    ingredient = Ingredient(
        name="Soup", category="Soup", unit_id=unit.id,
        min_safety_stock=Decimal("15.500"), critical_threshold=Decimal("8.250"),
        usage_per_serving=Decimal("0.350"), status="active",
    )
    db.add(ingredient)
    db.commit()
    db.refresh(ingredient)
    assert isinstance(ingredient.usage_per_serving, Decimal)
    assert ingredient.usage_per_serving == Decimal("0.350")


def test_ingredient_name_is_unique(db, unit):
    db.add(_make_ingredient("Noodles", unit.id))
    db.commit()
    db.add(_make_ingredient("Noodles", unit.id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_migration_matches_models(client):
    """No model drift: Alembic autogenerate finds zero pending schema changes."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.database import Base
    from app import models  # noqa: F401  (registers all tables)

    engine = client.app.state.db_engine
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        differences = compare_metadata(context, Base.metadata)
    assert differences == []
