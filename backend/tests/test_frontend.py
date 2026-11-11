"""Phase 1 Step 6: frontend integration tests.

These exercise the REAL request sequences the browser UI performs (same
endpoints, payloads, headers) plus static asset serving. They are API-level
integration tests — no browser/DOM automation is available in this
environment, so no claim of rendered-UI testing is made.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.seed import DEMO_ADMIN_PASSWORD, DEMO_STAFF_PASSWORD


def _login(client, username, password):
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200
    return response.json()["csrfToken"]


def _key():
    return f"web-{uuid.uuid4()}"


@pytest.fixture()
def staff(seeded_client):
    client = TestClient(seeded_client.app)
    csrf = _login(client, "staff", DEMO_STAFF_PASSWORD)
    return client, csrf


@pytest.fixture()
def admin(seeded_client):
    client = TestClient(seeded_client.app)
    csrf = _login(client, "admin", DEMO_ADMIN_PASSWORD)
    return client, csrf


# --- Static frontend serving -------------------------------------------------

def test_index_page_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "StockMate" in response.text
    # The app shell loads every core script and page module.
    for script in ("api.js", "ui.js", "auth.js", "data.js", "app.js"):
        assert f"/js/{script}" in response.text


def test_all_frontend_assets_served(client):
    assets = [
        "/css/base.css",
        "/css/app.css",
        "/js/api.js",
        "/js/ui.js",
        "/js/auth.js",
        "/js/data.js",
        "/js/app.js",
        "/js/pages/login.js",
        "/js/pages/dashboard.js",
        "/js/pages/inventory.js",
        "/js/pages/entry.js",
        "/js/pages/suppliers.js",
        "/js/pages/ingredients.js",
        "/js/pages/history.js",
        "/js/pages/settings.js",
    ]
    for path in assets:
        response = client.get(path)
        assert response.status_code == 200, f"missing asset: {path}"


# --- Units endpoint (ingredient form dropdown) --------------------------------

def test_units_requires_authentication(client):
    assert client.get("/api/v1/units").status_code == 401


def test_units_list_available_to_staff(staff):
    client, _ = staff
    response = client.get("/api/v1/units")
    assert response.status_code == 200
    units = response.json()
    assert len(units) == 7
    assert {u["abbreviation"] for u in units} == {"ptn", "kg", "L", "slc", "sht", "pc", "pack"}
    assert all({"id", "name", "abbreviation"} <= set(u) for u in units)


# --- Dashboard data flow (exact calls the dashboard makes) ---------------------

def test_dashboard_data_flow(staff):
    client, _ = staff
    stock = client.get("/api/v1/inventory/stock").json()
    ingredients = client.get("/api/v1/ingredients").json()
    suppliers = client.get("/api/v1/suppliers").json()
    transactions = client.get("/api/v1/inventory/transactions?limit=8").json()

    assert len(stock) == 8
    assert len(ingredients) == 8
    assert len(suppliers) == 3
    assert len(transactions) == 8  # the opening balances

    sample = stock[0]
    # Fields the dashboard/inventory tables rely on.
    for field in ("ingredient_id", "ingredient_name", "category", "unit",
                  "current_quantity", "last_counted_at", "confidence", "status"):
        assert field in sample
    ingredient = ingredients[0]
    for field in ("min_safety_stock", "critical_threshold", "primary_supplier_id",
                  "supplier_ids", "unit_id"):
        assert field in ingredient


# --- Stock entry flows (exact calls the entry form makes) ----------------------

def _noodles_id(client):
    rows = client.get("/api/v1/inventory/stock").json()
    return next(r for r in rows if r["ingredient_name"] == "Korean Noodles")["ingredient_id"]


def test_used_then_remaining_entry_flow(staff):
    client, csrf = staff
    noodles = _noodles_id(client)

    used = client.post("/api/v1/inventory/entries", json={
        "ingredient_id": noodles, "mode": "used", "quantity": "10",
        "reason": "daily_count", "idempotency_key": _key(),
        "confirmed": False, "allow_negative": False,
    }, headers={"X-CSRF-Token": csrf})
    assert used.status_code == 200
    assert used.json()["transaction"]["resulting_quantity"] == "170.000"

    remaining = client.post("/api/v1/inventory/entries", json={
        "ingredient_id": noodles, "mode": "remaining", "quantity": "200",
        "reason": "recount", "idempotency_key": _key(),
        "confirmed": False, "allow_negative": False,
    }, headers={"X-CSRF-Token": csrf})
    assert remaining.status_code == 200
    body = remaining.json()["transaction"]
    assert body["type"] == "reconciliation"
    assert body["resulting_quantity"] == "200.000"
    assert body["quantity_delta"] == "30.000"

    # The physical count updated verification metadata; the UI shows it.
    stock = client.get("/api/v1/inventory/stock").json()
    row = next(r for r in stock if r["ingredient_id"] == noodles)
    assert row["current_quantity"] == "200.000"
    assert row["confidence"] == "high"
    assert row["last_counted_at"] is not None


def test_retry_with_same_key_returns_original_without_double_counting(staff):
    client, csrf = staff
    noodles = _noodles_id(client)
    key = _key()
    payload = {
        "ingredient_id": noodles, "mode": "used", "quantity": "5",
        "reason": "daily_count", "idempotency_key": key,
        "confirmed": False, "allow_negative": False,
    }
    first = client.post("/api/v1/inventory/entries", json=payload,
                        headers={"X-CSRF-Token": csrf})
    retry = client.post("/api/v1/inventory/entries", json=payload,
                        headers={"X-CSRF-Token": csrf})
    assert first.status_code == retry.status_code == 200
    assert first.json()["idempotent_replay"] is False
    assert retry.json()["idempotent_replay"] is True
    assert retry.json()["transaction"]["id"] == first.json()["transaction"]["id"]

    stock = client.get("/api/v1/inventory/stock").json()
    row = next(r for r in stock if r["ingredient_id"] == noodles)
    assert row["current_quantity"] == "175.000"  # deducted exactly once


def test_large_adjustment_confirmation_flow(staff):
    """The UI re-submits the SAME key with confirmed=true after the dialog."""
    client, csrf = staff
    noodles = _noodles_id(client)
    key = _key()
    # A large POSITIVE recount avoids the negative-stock guard, which the
    # server checks before the large-adjustment confirmation rule.
    payload = {
        "ingredient_id": noodles, "mode": "remaining", "quantity": "900",
        "reason": "recount", "idempotency_key": key,
        "confirmed": False, "allow_negative": False,
    }
    response = client.post("/api/v1/inventory/entries", json=payload,
                           headers={"X-CSRF-Token": csrf})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "confirmation_required"

    payload["confirmed"] = True
    confirmed = client.post("/api/v1/inventory/entries", json=payload,
                            headers={"X-CSRF-Token": csrf})
    assert confirmed.status_code == 200
    assert confirmed.json()["transaction"]["quantity_delta"] == "720.000"  # 900 - 180
    assert confirmed.json()["transaction"]["resulting_quantity"] == "900.000"


def test_session_expiry_after_logout_blocks_api(staff):
    client, csrf = staff
    assert client.post("/api/v1/auth/logout").status_code == 200
    # The UI's onUnauthorized handler sends the user back to login on this.
    assert client.get("/api/v1/inventory/stock").status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 401


def test_admin_user_management_flow(admin):
    """Settings page: list users, create a staff account, log in with it."""
    client, csrf = admin
    users = client.get("/api/v1/admin/users").json()["users"]
    assert {u["username"] for u in users} == {"admin", "staff"}

    created = client.post("/api/v1/admin/users", json={
        "username": "newcook", "password": "strong-pass-123", "role": "staff",
    }, headers={"X-CSRF-Token": csrf})
    assert created.status_code in (200, 201)

    fresh = TestClient(client.app)
    csrf_new = _login(fresh, "newcook", "strong-pass-123")
    assert csrf_new
    me = fresh.get("/api/v1/auth/me").json()
    assert me["user"]["role"] == "staff"
