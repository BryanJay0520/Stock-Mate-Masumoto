"""Fictional demonstration data (decision D23 and the demo-data rules).

EVERYTHING created here is explicitly fictional. It exists so the app can be
explored before real data is entered, and it must never be mistaken for real
restaurant inventory.

Seeding is idempotent: a marker row in ``app_settings`` is written in the same
transaction as the data, so re-running never duplicates anything. Each
opening stock quantity is recorded as an ``opening_balance`` ledger
transaction with a deterministic idempotency key, so the append-only ledger
can always reconstruct the cached balances from zero (D23).

Run as a CLI from the backend directory:

    python -m app.seed
"""

import json
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from argon2 import PasswordHasher
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import (
    AppSetting,
    Ingredient,
    IngredientStockState,
    InventoryTransaction,
    Supplier,
    SupplierIngredient,
    Unit,
    User,
)

SEED_MARKER_KEY = "demo_data_seeded"

# Dev-only credentials for the fictional demo accounts. NOT secrets — local
# development convenience only; real deployments must create their own users.
DEMO_ADMIN_PASSWORD = "demo-admin-password"
DEMO_STAFF_PASSWORD = "demo-staff-password"

FICTION_NOTE = "FICTIONAL DEMONSTRATION DATA — not real restaurant inventory."

# name, category, unit abbreviation, opening qty, safety, critical, usage/serving
DEMO_INGREDIENTS = [
    ("Korean Noodles",  "Noodles",  "ptn", "240", "80",  "40",  "1"),
    ("Chinese Noodles", "Noodles",  "ptn", "180", "60",  "30",  "1"),
    ("Soup Base",       "Soup",     "L",   "45",  "15",  "8",   "0.35"),
    ("Chashu",          "Toppings", "slc", "500", "160", "80",  "2"),
    ("Eggs",            "Toppings", "pc",  "360", "100", "50",  "1"),
    ("Nori",            "Toppings", "sht", "400", "120", "60",  "1"),
    ("Green Onions",    "Produce",  "kg",  "8",   "2",   "1",   "0.02"),
    ("Menma",           "Toppings", "kg",  "12",  "3",   "1.5", "0.03"),
]

# supplier index (1-based as in the requirements) -> fictional name
DEMO_SUPPLIERS = [
    ("Tokyo Noodle Co.", "Haruto Sato", "+81-3-0000-0001", "orders@tokyo-noodle.example"),
    ("Dragon Noodle Works", "Mei Chen", "+81-6-0000-0002", "sales@dragon-noodle.example"),
    ("Masumoto Provisions", "Kenji Masumoto", "+81-3-0000-0003", "hello@masumoto.example"),
]

# ingredient name -> 1-based primary supplier index (per requirements)
PRIMARY_SUPPLIER = {
    "Korean Noodles": 1,
    "Chinese Noodles": 2,
    "Soup Base": 1,
    "Chashu": 3,
    "Eggs": 3,
    "Nori": 3,
    "Green Onions": 1,
    "Menma": 1,
}

# Optional alternative suppliers (ingredient name -> list of 1-based indexes).
ALTERNATIVE_SUPPLIERS = {
    "Korean Noodles": [2],  # demonstrates primary + alternative assignment
}

DEMO_UNITS = [
    ("Portion", "ptn"),
    ("Kilogram", "kg"),
    ("Liter", "L"),
    ("Slice", "slc"),
    ("Sheet", "sht"),
    ("Piece", "pc"),
]


def seed_demo_data(session: Session, settings: Settings) -> dict:
    """Insert the fictional demo dataset. Safe to call repeatedly.

    Returns a summary dict; ``seeded`` is False when data already existed.
    """
    marker = session.get(AppSetting, SEED_MARKER_KEY)
    if marker is not None:
        return {"seeded": False, "reason": "demo data already present"}

    hasher = PasswordHasher()
    admin = User(
        username="admin",
        password_hash=hasher.hash(DEMO_ADMIN_PASSWORD),
        role="admin",
        is_active=True,
    )
    staff = User(
        username="staff",
        password_hash=hasher.hash(DEMO_STAFF_PASSWORD),
        role="staff",
        is_active=True,
    )
    session.add_all([admin, staff])

    units = {}
    for name, abbr in DEMO_UNITS:
        unit = Unit(name=name, abbreviation=abbr)
        session.add(unit)
        units[abbr] = unit

    suppliers = []
    for name, contact, phone, email in DEMO_SUPPLIERS:
        supplier = Supplier(
            name=name, contact_name=contact, phone=phone, email=email,
            notes=FICTION_NOTE, status="active",
        )
        session.add(supplier)
        suppliers.append(supplier)

    session.flush()  # assign ids before wiring relationships

    now = datetime.now(timezone.utc)
    business_today = now.astimezone(ZoneInfo(settings.business_timezone)).date()
    ingredient_count = 0

    for name, category, abbr, opening, safety, critical, usage in DEMO_INGREDIENTS:
        ingredient = Ingredient(
            name=name,
            category=category,
            unit_id=units[abbr].id,
            min_safety_stock=Decimal(safety),
            critical_threshold=Decimal(critical),
            usage_per_serving=Decimal(usage),
            status="active",
        )
        session.add(ingredient)
        session.flush()  # ingredient.id needed below (circular-safe order)

        # Junction rows first: primary + any alternatives (D19/D24).
        supplier_indexes = {PRIMARY_SUPPLIER[name], *ALTERNATIVE_SUPPLIERS.get(name, [])}
        for index in supplier_indexes:
            session.add(SupplierIngredient(
                supplier_id=suppliers[index - 1].id,
                ingredient_id=ingredient.id,
            ))
        session.flush()

        # Primary supplier may only be assigned AFTER its junction row exists.
        ingredient.primary_supplier_id = suppliers[PRIMARY_SUPPLIER[name] - 1].id

        opening_qty = Decimal(opening)
        session.add(IngredientStockState(
            ingredient_id=ingredient.id,
            current_quantity=opening_qty,
            last_counted_at=now,          # opening balance counts as the initial count
            last_counted_by=admin.id,
            last_transaction_at=now,
            unresolved_discrepancy=False,
            confidence="high",
            version=1,
        ))
        session.add(InventoryTransaction(
            ingredient_id=ingredient.id,
            type="opening_balance",       # D23: ledger can rebuild from zero
            quantity_delta=opening_qty,
            resulting_quantity=opening_qty,
            reason="initial_stock",
            mode=None,
            entered_by=admin.id,
            business_date=business_today,
            created_at=now,
            idempotency_key=f"seed:opening-balance:{ingredient.id}",
        ))
        ingredient_count += 1

    session.add(AppSetting(
        key=SEED_MARKER_KEY,
        value_json=json.dumps({"version": 1, "note": FICTION_NOTE}),
    ))
    session.commit()

    return {
        "seeded": True,
        "users": 2,
        "units": len(DEMO_UNITS),
        "suppliers": len(suppliers),
        "ingredients": ingredient_count,
        "opening_balance_transactions": ingredient_count,
    }


def main() -> None:
    """CLI entry point: migrate, then seed the development database."""
    from app.database import create_db_engine, create_session_factory
    from app.db_admin import run_migrations

    settings = get_settings()
    run_migrations(settings.database_url)

    engine = create_db_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        summary = seed_demo_data(session, settings)

    print("StockMate demo seed:", summary)
    if summary["seeded"]:
        print(f"Demo login (dev only): admin / {DEMO_ADMIN_PASSWORD}")
        print(f"                       staff / {DEMO_STAFF_PASSWORD}")
        print(FICTION_NOTE)


if __name__ == "__main__":
    main()
