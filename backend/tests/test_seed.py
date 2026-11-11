"""Seed-data tests (Phase 1, Step 2).

Verifies the fictional demo dataset: correct contents, idempotency (D23/D6),
ingredient-supplier assignments (D19), independent stock per ingredient, and
that the append-only ledger reconstructs every cached balance exactly.
"""

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import (
    Ingredient,
    IngredientStockState,
    InventoryTransaction,
    Supplier,
    SupplierIngredient,
    Unit,
    User,
)
from app.seed import seed_demo_data


@pytest.fixture()
def db(seeded_client, settings):
    session = seeded_client.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def test_seed_creates_expected_catalog(db):
    assert db.scalar(select(func.count(User.id))) == 2
    assert db.scalar(select(func.count(Unit.id))) == 7
    assert db.scalar(select(func.count(Supplier.id))) == 3
    assert db.scalar(select(func.count(Ingredient.id))) == 8


def test_korean_and_chinese_noodles_are_independent(db):
    korean = db.scalar(select(Ingredient).where(Ingredient.name == "Korean Noodles"))
    chinese = db.scalar(select(Ingredient).where(Ingredient.name == "Chinese Noodles"))

    assert korean.id != chinese.id
    assert korean.primary_supplier_id != chinese.primary_supplier_id
    assert korean.stock_state.current_quantity == Decimal("180")
    assert chinese.stock_state.current_quantity == Decimal("180")
    assert korean.unit.abbreviation == chinese.unit.abbreviation == "pc"
    assert (korean.count_package_name, korean.count_units_per_package) == ("Box", Decimal("60"))


def test_demo_counting_and_supplier_purchasing_packages(db):
    expected = {
        "Chinese Noodles": ("pc", "Box", Decimal("50")),
        "Eggs": ("pc", "Tray", Decimal("30")),
        "Soup Base": ("pack", "Case", Decimal("10")),
        "Chashu": ("pc", "Pack", Decimal("20")),
    }
    for name, (unit, package, size) in expected.items():
        ingredient = db.scalar(select(Ingredient).where(Ingredient.name == name))
        assert ingredient.unit.abbreviation == unit
        assert ingredient.count_package_enabled is True
        assert (ingredient.count_package_name, ingredient.count_units_per_package) == (package, size)
        primary = next(link for link in ingredient.supplier_links
                       if link.supplier_id == ingredient.primary_supplier_id)
        assert (primary.purchase_package_name, primary.package_size,
                primary.whole_packages_only) == (package, size, True)

    korean = db.scalar(select(Ingredient).where(Ingredient.name == "Korean Noodles"))
    sizes = {link.supplier_id: link.package_size for link in korean.supplier_links}
    assert sizes[korean.primary_supplier_id] == Decimal("60")
    assert sorted(sizes.values()) == [Decimal("50"), Decimal("60")]


def test_required_supplier_assignments(db):
    """Korean Noodles → S1, Chinese Noodles → S2, Soup Base → S1, Chashu → S3."""
    suppliers = {s.name: s for s in db.scalars(select(Supplier)).all()}
    ordered = [
        suppliers["Tokyo Noodle Co."],
        suppliers["Dragon Noodle Works"],
        suppliers["Masumoto Provisions"],
    ]
    expected = {
        "Korean Noodles": ordered[0],
        "Chinese Noodles": ordered[1],
        "Soup Base": ordered[0],
        "Chashu": ordered[2],
    }
    for name, supplier in expected.items():
        ingredient = db.scalar(select(Ingredient).where(Ingredient.name == name))
        assert ingredient.primary_supplier_id == supplier.id

        # The primary supplier must always be a junction row too (D19/D24).
        link = db.scalar(
            select(SupplierIngredient).where(
                SupplierIngredient.ingredient_id == ingredient.id,
                SupplierIngredient.supplier_id == supplier.id,
            )
        )
        assert link is not None


def test_alternative_supplier_assignment(db):
    """Korean Noodles demonstrates primary (S1) + alternative (S2)."""
    ingredient = db.scalar(select(Ingredient).where(Ingredient.name == "Korean Noodles"))
    supplier_ids = {link.supplier_id for link in ingredient.supplier_links}
    assert len(supplier_ids) == 2
    assert ingredient.primary_supplier_id in supplier_ids


def test_opening_balances_reconstruct_stock(db):
    """SUM(ledger deltas) must equal the cached balance for every ingredient (D23)."""
    ingredients = db.scalars(select(Ingredient)).all()
    assert ingredients, "expected seeded ingredients"

    for ingredient in ingredients:
        ledger_sum = db.scalar(
            select(func.coalesce(func.sum(InventoryTransaction.quantity_delta), 0)).where(
                InventoryTransaction.ingredient_id == ingredient.id
            )
        )
        assert Decimal(ledger_sum) == ingredient.stock_state.current_quantity


def test_all_opening_quantities_are_ledger_transactions(db):
    openings = db.scalars(
        select(InventoryTransaction).where(
            InventoryTransaction.type == "opening_balance"
        )
    ).all()
    assert len(openings) == 8
    for row in openings:
        assert row.quantity_delta == row.resulting_quantity
        assert row.idempotency_key  # deterministic key present


def test_seeding_is_idempotent(seeded_client, settings, db):
    before_ingredients = db.scalar(select(func.count(Ingredient.id)))
    before_transactions = db.scalar(select(func.count(InventoryTransaction.id)))
    korean = db.scalar(select(Ingredient).where(Ingredient.name == "Korean Noodles"))
    before_balance = korean.stock_state.current_quantity

    result = seed_demo_data(db, settings)

    assert result["seeded"] is False
    assert db.scalar(select(func.count(Ingredient.id))) == before_ingredients
    assert db.scalar(select(func.count(InventoryTransaction.id))) == before_transactions
    db.expire_all()
    korean_again = db.scalar(select(Ingredient).where(Ingredient.name == "Korean Noodles"))
    assert korean_again.stock_state.current_quantity == before_balance
