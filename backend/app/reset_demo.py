"""Guarded reset of the one local, fictional StockMate development dataset.

Run from ``backend`` after stopping the development server::

    python -m app.reset_demo --database /absolute/path/to/backend/stockmate.db \
        --confirm RESET_FICTIONAL_STOCKMATE_DATA

This command never drops tables or changes migrations. It preserves users,
sessions, units, and non-demo application settings. A verified SQLite backup
is made before the single transaction that replaces operational demo rows.
"""

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.config import PROJECT_ROOT, Settings, get_settings
from app.database import create_db_engine, create_session_factory
from app.models import (
    AppSetting, AuditLog, DeliveryReceipt, DeliveryReceiptItem, Ingredient, IngredientDemandSetting,
    IngredientStockState, InventoryTransaction, PurchaseOrder,
    PurchaseOrderItem, NotificationCountRule, NotificationEvent, NotificationSetting,
    NotificationUserState, NotificationWorkerState,
    Supplier, SupplierCalendarException,
    SupplierIngredient, SupplierScheduleRule, User,
)
from app.seed import (
    DEMO_INGREDIENTS, DEMO_SUPPLIERS, FICTION_NOTE, ORDER_SEED_MARKER_KEY,
    SCHEDULE_SEED_MARKER_KEY, SEED_MARKER_KEY, seed_demo_data,
)
from app.services.confidence import as_utc

CONFIRMATION = "RESET_FICTIONAL_STOCKMATE_DATA"
DEMO_MARKERS = (SEED_MARKER_KEY, SCHEDULE_SEED_MARKER_KEY, ORDER_SEED_MARKER_KEY)
OPERATIONAL_AUDIT_TYPES = (
    "supplier", "ingredient", "ingredient_stock_state", "schedule_rule",
    "schedule_exception", "purchase_order", "delivery_receipt", "ingredient_demand",
)


def verify_target(settings: Settings, requested_path: Path, confirmation: str) -> Path:
    """Refuse a production URL, override, other path, or implicit confirmation."""
    url = make_url(settings.database_url)
    expected = (PROJECT_ROOT / "backend" / "stockmate.db").resolve()
    target = requested_path.resolve()
    if confirmation != CONFIRMATION:
        raise ValueError("The exact --confirm phrase is required.")
    if not requested_path.is_absolute():
        raise ValueError("--database must be an absolute path.")
    if settings.environment != "development" or url.drivername != "sqlite":
        raise ValueError("Only the local SQLite development environment can be reset.")
    if os.getenv("STOCKMATE_DATABASE_URL") or (PROJECT_ROOT / "backend" / ".env").exists():
        raise ValueError("An environment database override or .env file is present; target is uncertain.")
    if Path(url.database or "").resolve() != expected or target != expected or not target.is_file():
        raise ValueError("--database must name the existing backend/stockmate.db exactly.")
    return target


def verify_demo_dataset(session: Session) -> dict[str, int]:
    """Do not erase records unless the catalog and accounts are known demo data."""
    marker_rows = session.scalars(select(AppSetting).where(AppSetting.key.in_(DEMO_MARKERS))).all()
    if {row.key for row in marker_rows} != set(DEMO_MARKERS):
        raise ValueError("Demo seed markers are missing.")
    if any(json.loads(row.value_json).get("note") != FICTION_NOTE for row in marker_rows):
        raise ValueError("Demo seed markers do not identify fictional data.")

    users = session.scalars(select(User)).all()
    if {(u.username, u.role) for u in users} != {("admin", "admin"), ("staff", "staff")}:
        raise ValueError("Unknown user accounts exist; refusing the reset.")
    suppliers = session.scalars(select(Supplier)).all()
    if {s.name for s in suppliers} != {row[0] for row in DEMO_SUPPLIERS} or any(
        s.notes != FICTION_NOTE for s in suppliers
    ):
        raise ValueError("Unknown or non-fictional suppliers exist; refusing the reset.")
    ingredients = session.scalars(select(Ingredient)).all()
    if {i.name for i in ingredients} != {row[0] for row in DEMO_INGREDIENTS}:
        raise ValueError("Unknown ingredients exist; refusing the reset.")
    other_audits = session.scalars(
        select(AuditLog.id).where(AuditLog.entity_type.not_in(OPERATIONAL_AUDIT_TYPES))
    ).all()
    if other_audits:
        raise ValueError("Unknown audit entities exist; refusing the reset.")
    notification_policy = session.get(NotificationSetting, 1)
    if notification_policy and notification_policy.automatic_evaluation_enabled:
        raise ValueError("Disable automatic notification evaluation before a demo reset.")
    worker = session.get(NotificationWorkerState, 1)
    if (worker and worker.owner_token and worker.lease_expires_at and
            as_utc(worker.lease_expires_at) > datetime.now(timezone.utc)):
        raise ValueError("A notification worker lease is active; stop the worker before a demo reset.")
    revision = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    from app.db_admin import BACKEND_DIR
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    if revision != ScriptDirectory.from_config(config).get_current_head():
        raise ValueError("Database must already be at the current migration head.")
    violations = session.execute(text("PRAGMA foreign_key_check")).all()
    if violations:
        raise ValueError("Existing database has foreign-key violations.")
    return {"users": len(users), "suppliers": len(suppliers), "ingredients": len(ingredients)}


def backup_database(source: Path) -> Path:
    backup_dir = source.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    backup = backup_dir / (
        f"stockmate-before-demo-reset-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.db"
    )
    if backup.exists():
        raise FileExistsError(backup)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as original:
        with sqlite3.connect(backup) as saved:
            original.backup(saved)
    with sqlite3.connect(f"file:{backup}?mode=ro", uri=True) as saved:
        if saved.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("Backup failed SQLite integrity check.")
        if saved.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Backup has foreign-key violations.")
    return backup


def remove_demo_operations(session: Session) -> None:
    """Delete dependent demo rows in FK order without touching auth or schema."""
    # Notices reference demo ingredients, suppliers and orders. Preserve the
    # administrator's notification_settings but clear ingredient applicability.
    session.execute(delete(NotificationWorkerState))
    session.execute(delete(NotificationUserState))
    session.execute(delete(NotificationEvent))
    session.execute(delete(NotificationCountRule))
    session.execute(delete(DeliveryReceiptItem))
    session.execute(delete(DeliveryReceipt))
    session.execute(delete(PurchaseOrderItem))
    session.execute(delete(PurchaseOrder))
    session.execute(delete(InventoryTransaction))
    session.execute(delete(IngredientStockState))
    session.execute(delete(SupplierCalendarException))
    session.execute(delete(SupplierScheduleRule))
    session.execute(delete(IngredientDemandSetting))
    # The composite primary-supplier FK points back to supplier_ingredients.
    session.execute(update(Ingredient).values(primary_supplier_id=None))
    session.execute(delete(SupplierIngredient))
    session.execute(delete(Ingredient))
    session.execute(delete(Supplier))
    session.execute(delete(AuditLog).where(AuditLog.entity_type.in_(OPERATIONAL_AUDIT_TYPES)))
    session.execute(delete(AppSetting).where(AppSetting.key.in_(DEMO_MARKERS)))
    session.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True, help="Exact absolute local dev DB path")
    parser.add_argument("--confirm", required=True, help=f"Type {CONFIRMATION}")
    args = parser.parse_args()
    settings = get_settings()
    target = verify_target(settings, args.database, args.confirm)
    engine = create_db_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        before = verify_demo_dataset(session)
    backup = backup_database(target)
    with session_factory() as session:
        try:
            verify_demo_dataset(session)
            remove_demo_operations(session)
            summary = seed_demo_data(session, settings, commit=False)
            if session.execute(text("PRAGMA foreign_key_check")).all():
                raise RuntimeError("Reseed produced foreign-key violations.")
            session.commit()
        except Exception:
            session.rollback()
            raise
    print("Verified development database:", target)
    print("Verified backup:", backup)
    print("Preserved accounts:", before["users"])
    print("Reseeded fictional data:", summary)


if __name__ == "__main__":
    main()
