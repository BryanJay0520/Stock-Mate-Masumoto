"""Tests for Phase 1 Step 5: supplier & ingredient administration APIs.

Covers the requirements from the Step-5 authorization:
- supplier CRUD, duplicate validation, archive guard, reactivation
- primary/alternative supplier integrity (junction = {primary} ∪ {alternatives})
- ingredient CRUD with opening quantities via opening_balance ledger entries
- ingredient CRUD never mutates stock balances directly (D14)
- staff read-only access, admin-only mutations, unauthenticated rejection
- audit logging of administrative changes
"""

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models import AuditLog, Ingredient, InventoryTransaction, Supplier, Unit
from app.seed import DEMO_ADMIN_PASSWORD, DEMO_STAFF_PASSWORD


def _login(client, username, password):
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200
    return response.json()["csrfToken"]


def _key():
    return f"test-{uuid.uuid4()}"


@pytest.fixture()
def admin(seeded_client):
    client = TestClient(seeded_client.app)
    csrf = _login(client, "admin", DEMO_ADMIN_PASSWORD)
    return client, csrf


@pytest.fixture()
def staff(seeded_client):
    client = TestClient(seeded_client.app)
    csrf = _login(client, "staff", DEMO_STAFF_PASSWORD)
    return client, csrf


@pytest.fixture()
def ids(seeded_client):
    """Database ids of the seeded demo rows, keyed by name."""
    db = seeded_client.app.state.session_factory()
    try:
        suppliers = dict(db.execute(select(Supplier.name, Supplier.id)).all())
        ingredients = dict(db.execute(select(Ingredient.name, Ingredient.id)).all())
        units = dict(db.execute(select(Unit.abbreviation, Unit.id)).all())
        return {"suppliers": suppliers, "ingredients": ingredients, "units": units}
    finally:
        db.close()


def _post(client, csrf, url, payload):
    return client.post(url, json=payload, headers={"X-CSRF-Token": csrf})


def _patch(client, csrf, url, payload):
    return client.patch(url, json=payload, headers={"X-CSRF-Token": csrf})


# --- Suppliers: read ---------------------------------------------------------

def test_staff_can_list_and_read_suppliers(staff, ids):
    client, _ = staff
    response = client.get("/api/v1/suppliers")
    assert response.status_code == 200
    names = {row["name"] for row in response.json()}
    assert {"Tokyo Noodle Co.", "Dragon Noodle Works", "Masumoto Provisions"} <= names

    detail = client.get(f"/api/v1/suppliers/{ids['suppliers']['Tokyo Noodle Co.']}")
    assert detail.status_code == 200


def test_unauthenticated_supplier_read_rejected(seeded_client):
    client = TestClient(seeded_client.app)
    assert client.get("/api/v1/suppliers").status_code == 401
    assert client.get("/api/v1/ingredients").status_code == 401


def test_supplier_search_and_status_filter(admin):
    client, _ = admin
    response = client.get("/api/v1/suppliers", params={"search": "noodle"})
    assert response.status_code == 200
    names = {row["name"] for row in response.json()}
    assert names == {"Tokyo Noodle Co.", "Dragon Noodle Works"}

    response = client.get("/api/v1/suppliers", params={"status": "archived"})
    assert response.status_code == 200
    assert response.json() == []


def test_supplier_detail_marks_primary_ingredients(admin, ids):
    client, _ = admin
    tokyo = ids["suppliers"]["Tokyo Noodle Co."]
    detail = client.get(f"/api/v1/suppliers/{tokyo}").json()
    by_name = {row["ingredient_name"]: row for row in detail["ingredients"]}
    # Korean Noodles: Tokyo is PRIMARY (with Dragon as alternative).
    assert by_name["Korean Noodles"]["is_primary"] is True
    # Dragon's detail shows Korean Noodles as a NON-primary link.
    dragon = ids["suppliers"]["Dragon Noodle Works"]
    dragon_detail = client.get(f"/api/v1/suppliers/{dragon}").json()
    dragon_by_name = {row["ingredient_name"]: row for row in dragon_detail["ingredients"]}
    assert dragon_by_name["Korean Noodles"]["is_primary"] is False
    assert dragon_by_name["Chinese Noodles"]["is_primary"] is True


# --- Suppliers: mutations ----------------------------------------------------

def test_admin_can_create_and_edit_supplier(admin):
    client, csrf = admin
    created = _post(client, csrf, "/api/v1/suppliers", {
        "name": "Kanto Egg Farm",
        "contact_name": "Aoi Tanaka",
        "email": "sales@kanto-egg.example",
    })
    assert created.status_code == 201
    supplier_id = created.json()["id"]

    edited = _patch(client, csrf, f"/api/v1/suppliers/{supplier_id}", {
        "phone": "+81-3-1111-2222",
    })
    assert edited.status_code == 200
    assert edited.json()["phone"] == "+81-3-1111-2222"
    assert edited.json()["name"] == "Kanto Egg Farm"  # untouched field preserved


def test_duplicate_supplier_name_rejected(admin):
    client, csrf = admin
    response = _post(client, csrf, "/api/v1/suppliers", {"name": "Tokyo Noodle Co."})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_supplier"


def test_staff_cannot_create_supplier(staff):
    client, csrf = staff
    response = _post(client, csrf, "/api/v1/suppliers", {"name": "Nope Inc."})
    assert response.status_code == 403


def test_archive_blocked_while_primary_of_active_ingredient(admin, ids):
    client, csrf = admin
    tokyo = ids["suppliers"]["Tokyo Noodle Co."]
    response = _post(client, csrf, f"/api/v1/suppliers/{tokyo}/archive", {})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "archive_blocked"
    assert "Korean Noodles" in response.json()["error"]["message"]
    # Supplier stays active.
    assert client.get(f"/api/v1/suppliers/{tokyo}").json()["status"] == "active"


def test_reassign_then_archive_then_reactivate(admin, ids):
    client, csrf = admin
    tokyo = ids["suppliers"]["Tokyo Noodle Co."]
    dragon = ids["suppliers"]["Dragon Noodle Works"]

    # Reassign every active ingredient whose primary is Tokyo to Dragon.
    # Korean Noodles keeps Tokyo as an ALTERNATIVE so we can verify below
    # that archiving never deletes historical junction links.
    for name in ("Korean Noodles", "Soup Base", "Green Onions", "Menma"):
        payload = {"primary_supplier_id": dragon}
        if name == "Korean Noodles":
            payload["alternative_supplier_ids"] = [tokyo]
        response = _patch(
            client, csrf,
            f"/api/v1/ingredients/{ids['ingredients'][name]}",
            payload,
        )
        assert response.status_code == 200
        assert response.json()["primary_supplier_id"] == dragon

    archived = _post(client, csrf, f"/api/v1/suppliers/{tokyo}/archive", {})
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"

    # Historical links are preserved: Tokyo is still linked to Korean Noodles
    # as an alternative even though the supplier is archived.
    detail = client.get(f"/api/v1/ingredients/{ids['ingredients']['Korean Noodles']}").json()
    assert tokyo in detail["supplier_ids"]

    reactivated = _post(client, csrf, f"/api/v1/suppliers/{tokyo}/reactivate", {})
    assert reactivated.status_code == 200
    assert reactivated.json()["status"] == "active"


def test_archived_alternative_supplier_keeps_junction_link(admin, ids, seeded_client):
    """Archiving a non-primary supplier must not erase its historical link."""
    client, csrf = admin
    created = _post(client, csrf, "/api/v1/suppliers", {"name": "Backup Pork Supply"})
    backup_id = created.json()["id"]
    chashu = ids["ingredients"]["Chashu"]

    # Make it an ALTERNATIVE for Chashu (primary stays Masumoto), then archive.
    assert _patch(client, csrf, f"/api/v1/ingredients/{chashu}", {
        "alternative_supplier_ids": [backup_id],
    }).status_code == 200
    response = _post(client, csrf, f"/api/v1/suppliers/{backup_id}/archive", {})
    assert response.status_code == 200

    db = seeded_client.app.state.session_factory()
    try:
        ingredient = db.get(Ingredient, chashu)
        assert backup_id in {link.supplier_id for link in ingredient.supplier_links}
    finally:
        db.close()


# --- Ingredients -------------------------------------------------------------

def test_create_ingredient_with_opening_quantity_creates_ledger_entry(
    admin, ids, seeded_client
):
    client, csrf = admin
    created = _post(client, csrf, "/api/v1/ingredients", {
        "name": "Spicy Miso Paste",
        "category": "Soup",
        "unit_id": ids["units"]["kg"],
        "min_safety_stock": "2",
        "critical_threshold": "1",
        "usage_per_serving": "0.01",
        "primary_supplier_id": ids["suppliers"]["Masumoto Provisions"],
        "opening_quantity": "5.5",
    })
    assert created.status_code == 201
    ingredient_id = created.json()["id"]

    # Balance and ledger agree, and the ledger row is an opening_balance.
    detail = client.get(f"/api/v1/ingredients/{ingredient_id}").json()
    assert detail["current_quantity"] == "5.500"

    db = seeded_client.app.state.session_factory()
    try:
        entry = db.scalar(select(InventoryTransaction).where(
            InventoryTransaction.ingredient_id == ingredient_id
        ))
        assert entry is not None
        assert entry.type == "opening_balance"
        assert entry.quantity_delta == Decimal("5.500")
    finally:
        db.close()

    verify = client.get("/api/v1/inventory/verify").json()
    assert verify["consistent"] is True


def test_create_ingredient_with_unknown_supplier_rejected(admin, ids):
    client, csrf = admin
    response = _post(client, csrf, "/api/v1/ingredients", {
        "name": "Ghost Ingredient",
        "category": "Test",
        "unit_id": ids["units"]["pc"],
        "usage_per_serving": "1",
        "primary_supplier_id": 999999,
    })
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "supplier_not_found"


def test_create_ingredient_with_archived_supplier_rejected(admin, ids):
    client, csrf = admin
    created = _post(client, csrf, "/api/v1/suppliers", {"name": "Retired Vendor"})
    retired_id = created.json()["id"]
    # Not a primary of any ingredient -> archiving is allowed.
    assert _post(client, csrf, f"/api/v1/suppliers/{retired_id}/archive", {}).status_code == 200

    response = _post(client, csrf, "/api/v1/ingredients", {
        "name": "Wonton Skins",
        "category": "Noodles",
        "unit_id": ids["units"]["pc"],
        "usage_per_serving": "1",
        "primary_supplier_id": retired_id,
    })
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "supplier_inactive"


def test_alternative_supplier_sync_keeps_primary_in_set(admin, ids):
    client, csrf = admin
    chashu = ids["ingredients"]["Chashu"]
    tokyo = ids["suppliers"]["Tokyo Noodle Co."]
    masumoto = ids["suppliers"]["Masumoto Provisions"]

    response = _patch(client, csrf, f"/api/v1/ingredients/{chashu}", {
        "alternative_supplier_ids": [tokyo],
    })
    assert response.status_code == 200
    body = response.json()
    # Junction = {primary} ∪ {alternatives}; primary stays Masumoto.
    assert body["primary_supplier_id"] == masumoto
    assert set(body["supplier_ids"]) == {masumoto, tokyo}

    # Clearing alternatives leaves exactly the primary.
    response = _patch(client, csrf, f"/api/v1/ingredients/{chashu}", {
        "alternative_supplier_ids": [],
    })
    assert set(response.json()["supplier_ids"]) == {masumoto}


def test_ingredient_update_ignores_stock_fields(admin, ids):
    """Even if a client sends a stock field, balances stay ledger-owned (D14)."""
    client, csrf = admin
    noodles = ids["ingredients"]["Korean Noodles"]
    before = client.get(f"/api/v1/ingredients/{noodles}").json()["current_quantity"]

    response = _patch(client, csrf, f"/api/v1/ingredients/{noodles}", {
        "category": "Noodles (fresh)",
        "current_quantity": "9999",  # not part of the schema -> ignored
    })
    assert response.status_code == 200
    after = client.get(f"/api/v1/ingredients/{noodles}").json()
    assert after["category"] == "Noodles (fresh)"
    assert after["current_quantity"] == before


def test_duplicate_ingredient_name_rejected(admin, ids):
    client, csrf = admin
    response = _post(client, csrf, "/api/v1/ingredients", {
        "name": "Korean Noodles",
        "category": "Noodles",
        "unit_id": ids["units"]["ptn"],
        "usage_per_serving": "1",
    })
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "duplicate_ingredient"


def test_archived_ingredient_blocks_stock_entry_until_restored(admin, ids):
    client, csrf = admin
    nori = ids["ingredients"]["Nori"]

    assert _post(client, csrf, f"/api/v1/ingredients/{nori}/archive", {}).status_code == 200

    entry = _post(client, csrf, "/api/v1/inventory/entries", {
        "ingredient_id": nori,
        "mode": "used",
        "quantity": "10",
        "reason": "daily_count",
        "idempotency_key": _key(),
        "confirmed": False,
        "allow_negative": False,
    })
    assert entry.status_code == 400
    assert entry.json()["error"]["code"] == "ingredient_inactive"

    assert _post(client, csrf, f"/api/v1/ingredients/{nori}/restore", {}).status_code == 200
    entry = _post(client, csrf, "/api/v1/inventory/entries", {
        "ingredient_id": nori,
        "mode": "used",
        "quantity": "10",
        "reason": "daily_count",
        "idempotency_key": _key(),
        "confirmed": False,
        "allow_negative": False,
    })
    assert entry.status_code == 200  # restored ingredient accepts entries again


def test_ingredient_search_and_filters(admin):
    client, _ = admin
    response = client.get("/api/v1/ingredients", params={"search": "noodles"})
    names = {row["name"] for row in response.json()}
    assert names == {"Korean Noodles", "Chinese Noodles"}

    response = client.get("/api/v1/ingredients", params={"category": "Toppings"})
    assert {row["category"] for row in response.json()} == {"Toppings"}


def test_staff_cannot_modify_ingredients(staff, ids):
    client, csrf = staff
    response = _patch(
        client, csrf,
        f"/api/v1/ingredients/{ids['ingredients']['Eggs']}",
        {"category": "Hacked"},
    )
    assert response.status_code == 403
    response = _post(
        client, csrf,
        f"/api/v1/ingredients/{ids['ingredients']['Eggs']}/archive", {},
    )
    assert response.status_code == 403


# --- Audit logging -----------------------------------------------------------

def test_admin_mutations_are_audit_logged(admin, ids, seeded_client):
    client, csrf = admin
    created = _post(client, csrf, "/api/v1/suppliers", {"name": "Audit Test Supplier"})
    supplier_id = created.json()["id"]
    _patch(client, csrf, f"/api/v1/suppliers/{supplier_id}", {"phone": "1"})
    _post(client, csrf, f"/api/v1/suppliers/{supplier_id}/archive", {})
    _patch(
        client, csrf,
        f"/api/v1/ingredients/{ids['ingredients']['Menma']}",
        {"min_safety_stock": "4"},
    )

    db = seeded_client.app.state.session_factory()
    try:
        rows = db.scalars(
            select(AuditLog).where(AuditLog.entity_id == supplier_id)
        ).all()
        actions = {row.action for row in rows}
        assert {"supplier.create", "supplier.update", "supplier.archive"} <= actions
        ingredient_update = db.scalar(select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "ingredient.update",
            AuditLog.entity_id == ids["ingredients"]["Menma"],
        ))
        assert ingredient_update >= 1
    finally:
        db.close()
