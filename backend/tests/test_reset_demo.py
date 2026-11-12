"""The development reset preserves auth/schema and replaces dependent demo data."""

from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from app.config import Settings
from app.models import (
    AuditLog, DeliveryReceipt, DeliveryReceiptItem, Ingredient, IngredientDemandSetting,
    InventoryTransaction, PurchaseOrder, Supplier, Unit, User, UserSession,
)
from app.reset_demo import (
    CONFIRMATION, backup_database, remove_demo_operations, verify_demo_dataset,
    verify_target,
)
from app.seed import DEMO_STAFF_PASSWORD, seed_demo_data
from app.services.inventory import verify_ledger


def test_guard_refuses_other_environment_or_database(settings, tmp_path):
    other = tmp_path / "other.db"
    other.touch()
    with pytest.raises(ValueError):
        verify_target(settings, other, CONFIRMATION)
    dev = Settings(environment="development", database_url=f"sqlite:///{other.as_posix()}")
    with pytest.raises(ValueError):
        verify_target(dev, other, CONFIRMATION)
    with pytest.raises(ValueError):
        verify_target(dev, other, "wrong phrase")


def test_backup_is_valid_and_separate(seeded_client, settings):
    source = Path(settings.database_url.removeprefix("sqlite:///"))
    backup = backup_database(source)
    try:
        assert backup != source and backup.exists()
        import sqlite3
        with sqlite3.connect(f"file:{backup}?mode=ro", uri=True) as saved:
            assert saved.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            assert saved.execute("SELECT COUNT(*) FROM ingredients").fetchone()[0] == 8
    finally:
        backup.unlink()


def test_reset_reseeds_after_receiving_without_losing_auth_or_migrations(
    seeded_client, settings,
):
    client = seeded_client
    login = client.post("/api/v1/auth/login", json={
        "username": "staff", "password": DEMO_STAFF_PASSWORD,
    })
    assert login.status_code == 200
    csrf = {"X-CSRF-Token": login.json()["csrfToken"]}
    with client.app.state.session_factory() as db:
        order = db.scalar(select(PurchaseOrder).where(PurchaseOrder.status == "confirmed"))
        egg = db.scalar(select(Ingredient).where(Ingredient.name == "Eggs"))
        order_id, egg_id = order.id, egg.id
    received = client.post(f"/api/v1/purchase-orders/{order_id}/receive", headers=csrf, json={
        "lines": [{"ingredient_id": egg_id, "quantity": "5"}],
        "idempotency_key": "reset-demo-receipt", "received_by_staff_name": "Demo Staff",
    })
    assert received.status_code == 201, received.text

    with client.app.state.session_factory() as db:
        verify_demo_dataset(db)
        db.add(IngredientDemandSetting(ingredient_id=egg_id, enabled=False, monday=Decimal("1")))
        db.flush()
        accounts = {u.username: (u.id, u.password_hash) for u in db.scalars(select(User))}
        sessions = db.scalar(select(func.count(UserSession.id)))
        units = db.scalar(select(func.count(Unit.id)))
        assert db.scalar(select(func.count(DeliveryReceipt.id))) == 1
        remove_demo_operations(db)
        summary = seed_demo_data(db, settings, commit=False)
        assert not db.execute(text("PRAGMA foreign_key_check")).all()
        db.commit()

    with client.app.state.session_factory() as db:
        assert summary["opening_balance_transactions"] == 8
        assert {u.username: (u.id, u.password_hash) for u in db.scalars(select(User))} == accounts
        assert db.scalar(select(func.count(UserSession.id))) == sessions
        assert db.scalar(select(func.count(Unit.id))) == units
        assert db.scalar(select(func.count(DeliveryReceipt.id))) == 0
        assert db.scalar(select(func.count(DeliveryReceiptItem.id))) == 0
        assert db.scalar(select(func.count(IngredientDemandSetting.ingredient_id))) == 0
        assert db.scalar(select(func.count(AuditLog.id))) == 0
        assert db.scalar(select(func.count(InventoryTransaction.id))) == 8
        assert db.scalar(select(func.count(Supplier.id))) == 3
        assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0008_ingredient_demand_settings"
        korean = db.scalar(select(Ingredient).where(Ingredient.name == "Korean Noodles"))
        assert korean.unit.abbreviation == "pc"
        assert korean.count_units_per_package == Decimal("60")
        assert korean.stock_state.current_quantity == Decimal("180")
        assert all(row["consistent"] for row in verify_ledger(db))
    assert client.get("/api/v1/suppliers").status_code == 200  # same login session
