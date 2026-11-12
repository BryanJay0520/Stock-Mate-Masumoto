"""Demand settings are independent, exact planning inputs, never physical stock."""

import json
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text

from app.db_admin import BACKEND_DIR, run_migrations
from app.models import AuditLog, IngredientDemandSetting
from app.seed import DEMO_ADMIN_PASSWORD, DEMO_STAFF_PASSWORD

DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
OPERATIONAL = ("ingredients", "ingredient_stock_state", "inventory_transactions",
               "supplier_ingredients", "suppliers", "supplier_schedule_rules",
               "purchase_orders", "purchase_order_items", "delivery_receipts", "delivery_receipt_items")


def login(client, role="admin"):
    result = client.post("/api/v1/auth/login", json={"username": role, "password":
                         DEMO_ADMIN_PASSWORD if role == "admin" else DEMO_STAFF_PASSWORD})
    assert result.status_code == 200
    return {"X-CSRF-Token": result.json()["csrfToken"]}


def item(client, name="Korean Noodles"):
    return next(i for i in client.get("/api/v1/ingredients").json() if i["name"] == name)


def url(ingredient):
    return f"/api/v1/ingredients/{ingredient['id']}/demand-settings"


def snapshot(client):
    with client.app.state.db_engine.connect() as connection:
        return {table: connection.execute(text(f"SELECT * FROM {table} ORDER BY rowid")).all()
                for table in OPERATIONAL}


def test_create_korean_example_and_preserve_all_operations(seeded_client):
    client = seeded_client
    headers = login(client)
    korean = item(client)
    before = snapshot(client)
    payload = {"expected_version": 0, "enabled": True, "safety_stock": "60",
               "notes": "  Restaurant planning assumption  ",
               **{day: "40" if index < 5 else "60" for index, day in enumerate(DAYS)}}
    result = client.patch(url(korean), headers=headers, json=payload)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["weekly_estimated_demand"] == "320.000"
    assert data["safety_stock"] == "60.000" and data["forecast_ready"] is True
    assert data["unit"] == "pc" and data["updated_at"].endswith("Z")
    assert data["notes"] == "Restaurant planning assumption"
    assert snapshot(client) == before
    assert client.get("/api/v1/inventory/verify").json()["consistent"] is True
    assert item(client)["count_units_per_package"] == "60.000"
    assert client.get(url(item(client, "Chinese Noodles"))).json()["configured"] is False
    with client.app.state.session_factory() as db:
        audit = db.scalar(select(AuditLog).where(AuditLog.action == "ingredient_demand.update"))
        assert audit.before_json is None
        assert json.loads(audit.after_json)["saturday"] == "60"


def test_missing_zero_partial_update_and_enable_disable(seeded_client):
    client = seeded_client
    headers = login(client)
    endpoint = url(item(client))
    empty = client.get(endpoint).json()
    assert empty["configured"] is False and empty["version"] == 0
    assert empty["monday"] is None and empty["weekly_estimated_demand"] is None
    first = client.patch(endpoint, headers=headers, json={"expected_version": 0, "monday": "0"}).json()
    assert first["monday"] == "0.000" and first["tuesday"] is None
    assert first["configured_days"] == 1 and first["forecast_ready"] is False
    second = client.patch(endpoint, headers=headers, json={
        "expected_version": 1, "tuesday": "1.125", "enabled": True,
    }).json()
    assert second["monday"] == "0.000" and second["tuesday"] == "1.125"
    assert second["weekly_estimated_demand"] is None and second["forecast_ready"] is False
    third = client.patch(endpoint, headers=headers, json={
        "expected_version": 2, "enabled": False, "tuesday": None,
    }).json()
    assert third["tuesday"] is None and third["monday"] == "0.000"
    assert third["enabled"] is False and third["forecast_ready"] is False


def test_weekday_batch_and_independent_decimal_quantities(seeded_client):
    client = seeded_client
    headers = login(client)
    egg = item(client, "Eggs")
    first = client.patch(url(egg), headers=headers, json={
        "expected_version": 0, "enabled": True, "safety_stock": "0",
        **{day: "0.125" if index < 5 else "0.001" for index, day in enumerate(DAYS)},
    }).json()
    assert first["weekly_estimated_demand"] == "0.627" and first["forecast_ready"] is True
    second = client.patch(url(egg), headers=headers, json={"expected_version": 1, "wednesday": "0"}).json()
    assert second["monday"] == "0.125" and second["wednesday"] == "0.000"
    assert second["weekly_estimated_demand"] == "0.502"
    korean = client.get(url(item(client))).json()
    assert korean["monday"] is None and korean["safety_stock"] is None
    disabled = client.patch(url(egg), headers=headers, json={"expected_version": 2, "enabled": False}).json()
    assert disabled["is_complete"] is True and disabled["forecast_ready"] is False


@pytest.mark.parametrize("field,value", [
    ("monday", "-1"), ("safety_stock", "-0.001"), ("sunday", "abc"),
    ("friday", "NaN"), ("thursday", "Infinity"), ("wednesday", "1.0001"),
    ("saturday", "100000000000"), ("enabled", None), ("notes", "x" * 1001),
    ("count_units_per_package", "10"),
])
def test_invalid_values_never_save(seeded_client, field, value):
    headers = login(seeded_client)
    endpoint = url(item(seeded_client))
    result = seeded_client.patch(endpoint, headers=headers, json={"expected_version": 0, field: value})
    assert result.status_code == 422
    assert seeded_client.get(endpoint).json()["configured"] is False


def test_permissions_csrf_and_staff_read(seeded_client):
    client = seeded_client
    assert client.get("/api/v1/ingredients/1/demand-settings").status_code == 401
    headers = login(client)
    endpoint = url(item(client))
    assert client.patch(endpoint, json={"expected_version": 0}).status_code == 403
    saved = client.patch(endpoint, headers=headers, json={"expected_version": 0, "monday": "7"})
    assert saved.status_code == 200
    staff_headers = login(client, "staff")
    assert client.get(endpoint).json()["monday"] == "7.000"
    assert client.patch(endpoint, headers=staff_headers, json={"expected_version": 1, "monday": "8"}).status_code == 403
    assert client.get("/api/v1/ingredients/999999/demand-settings").status_code == 404


def test_stale_edit_is_rejected_without_audit_or_inventory_write(seeded_client):
    client = seeded_client
    headers = login(client)
    endpoint = url(item(client))
    first = client.patch(endpoint, headers=headers, json={"expected_version": 0, "monday": "2"})
    assert first.status_code == 200
    conflict = client.patch(endpoint, headers=headers, json={"expected_version": 0, "monday": "99"})
    assert conflict.status_code == 409
    assert client.get(endpoint).json()["monday"] == "2.000"
    with client.app.state.session_factory() as db:
        assert len(db.scalars(select(AuditLog).where(AuditLog.action == "ingredient_demand.update")).all()) == 1


def test_migration_preserves_populated_database_without_creating_estimates(seeded_client, settings):
    client = seeded_client
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.downgrade(config, "0007_inventory_count_packages")
    before = snapshot(client)
    run_migrations(settings.database_url)
    assert snapshot(client) == before
    with client.app.state.session_factory() as db:
        assert db.scalars(select(IngredientDemandSetting)).all() == []
        assert db.execute(text("PRAGMA foreign_key_check")).all() == []
