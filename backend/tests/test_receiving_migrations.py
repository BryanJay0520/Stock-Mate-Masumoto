"""Upgrade populated legacy receipts without changing their history."""

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.db_admin import BACKEND_DIR, run_migrations


def test_populated_receipts_survive_additive_upgrades(seeded_client, settings):
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.downgrade(config, "0004_delivery_receipts")
    engine = seeded_client.app.state.db_engine
    with engine.begin() as connection:
        order_id, item_id, ingredient_id = connection.execute(text(
            "SELECT order_id, id, ingredient_id FROM purchase_order_items LIMIT 1"
        )).one()
        user_id = connection.execute(text("SELECT id FROM users LIMIT 1")).scalar_one()
        connection.execute(text(
            "INSERT INTO delivery_receipts "
            "(id, purchase_order_id, received_by, received_at, receipt_date, notes, "
            "idempotency_key, created_at) VALUES "
            "(1, :order_id, :user_id, '2026-11-10 12:00:00', '2026-11-10', "
            "'Legacy receipt', 'legacy-receipt-key', '2026-11-10 12:00:00')"
        ), {"order_id": order_id, "user_id": user_id})
        connection.execute(text(
            "INSERT INTO delivery_receipt_items "
            "(receipt_id, purchase_order_item_id, ingredient_id, quantity) "
            "VALUES (1, :item_id, :ingredient_id, 3)"
        ), {"item_id": item_id, "ingredient_id": ingredient_id})
    run_migrations(settings.database_url)
    with engine.connect() as connection:
        receipt = connection.execute(text("SELECT * FROM delivery_receipts")).mappings().one()
        assert receipt["notes"] == "Legacy receipt"
        assert receipt["received_by"] == user_id
        assert receipt["received_by_staff_name"] == ""  # unknown, never invented
        item = connection.execute(text("SELECT * FROM delivery_receipt_items")).mappings().one()
        assert item["quantity"] == 3
        assert item["quantity_rejected"] == 0
        assert connection.execute(text("PRAGMA foreign_key_check")).all() == []
        assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0006_receipt_staff_name"
