"""Regression tests for the three Step-5 pre-safeguards (D27, D28, D29).

1. D27 — a correction may not override a NEWER physical reconciliation.
2. D28 — reusing an idempotency key with a DIFFERENT payload returns 409.
3. D29 — read-only requests use a deferred BEGIN (no write lock); only
   write-intent paths use BEGIN IMMEDIATE.
"""

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app.models import Ingredient
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
def noodles_id(seeded_client):
    db = seeded_client.app.state.session_factory()
    try:
        return db.scalar(select(Ingredient.id).where(Ingredient.name == "Korean Noodles"))
    finally:
        db.close()


def _entry(client, csrf, ingredient_id, mode, quantity, **overrides):
    payload = {
        "ingredient_id": ingredient_id,
        "mode": mode,
        "quantity": str(quantity),
        "reason": "daily_count",
        "idempotency_key": _key(),
        "confirmed": False,
        "allow_negative": False,
        **overrides,
    }
    return client.post(
        "/api/v1/inventory/entries", json=payload, headers={"X-CSRF-Token": csrf}
    )


def _balance(client, ingredient_id):
    rows = client.get("/api/v1/inventory/stock").json()
    return Decimal(next(r for r in rows if r["ingredient_id"] == ingredient_id)["current_quantity"])


# --- D27: corrections vs. a newer physical count -----------------------------

def test_correction_rejected_when_newer_reconciliation_exists(admin, noodles_id):
    client, csrf = admin
    original = _entry(client, csrf, noodles_id, "used", "50").json()["transaction"]
    # A physical count verifies reality AFTER the mistaken entry.
    _entry(client, csrf, noodles_id, "remaining", "180")

    response = client.post(
        f"/api/v1/inventory/transactions/{original['id']}/correct",
        json={"corrected_quantity": "5", "reason": "correction", "idempotency_key": _key()},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "correction_after_reconciliation"
    # The verified balance is untouched.
    assert _balance(client, noodles_id) == Decimal("180")


def test_correction_allowed_when_only_consumption_follows(admin, noodles_id):
    """Without a newer physical count, corrections still work as before."""
    client, csrf = admin
    original = _entry(client, csrf, noodles_id, "used", "50").json()["transaction"]
    _entry(client, csrf, noodles_id, "used", "10")
    response = client.post(
        f"/api/v1/inventory/transactions/{original['id']}/correct",
        json={"corrected_quantity": "5", "reason": "correction", "idempotency_key": _key()},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    assert _balance(client, noodles_id) == Decimal("225")  # 240 - 50 - 10 + 45


# --- D28: idempotency-key payload conflicts ------------------------------------

def test_same_key_different_quantity_returns_409(admin, noodles_id):
    client, csrf = admin
    key = _key()
    assert _entry(client, csrf, noodles_id, "used", "10", idempotency_key=key).status_code == 200
    conflict = _entry(client, csrf, noodles_id, "used", "25", idempotency_key=key)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    assert _balance(client, noodles_id) == Decimal("230")  # only the first applied


def test_same_key_identical_payload_replays(admin, noodles_id):
    client, csrf = admin
    key = _key()
    first = _entry(client, csrf, noodles_id, "used", "10", idempotency_key=key)
    second = _entry(client, csrf, noodles_id, "used", "10", idempotency_key=key)
    assert second.status_code == 200
    assert second.json()["idempotent_replay"] is True
    assert first.json()["transaction"]["id"] == second.json()["transaction"]["id"]


def test_same_key_different_correction_payload_returns_409(admin, noodles_id):
    client, csrf = admin
    original = _entry(client, csrf, noodles_id, "used", "50").json()["transaction"]
    key = _key()
    url = f"/api/v1/inventory/transactions/{original['id']}/correct"
    ok = client.post(
        url,
        json={"corrected_quantity": "5", "reason": "correction", "idempotency_key": key},
        headers={"X-CSRF-Token": csrf},
    )
    assert ok.status_code == 200
    conflict = client.post(
        url,
        json={"corrected_quantity": "7", "reason": "correction", "idempotency_key": key},
        headers={"X-CSRF-Token": csrf},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"


# --- D29: read requests never take the write lock --------------------------------

def test_read_requests_use_deferred_begin_write_uses_immediate(
    seeded_client, admin, noodles_id
):
    client, csrf = admin
    engine = seeded_client.app.state.db_engine
    modes = []

    @event.listens_for(engine, "begin")
    def probe(connection):
        # The engine's own listener records which BEGIN flavor it emitted.
        modes.append(connection.info.get("stockmate_begin_mode"))

    try:
        assert client.get("/api/v1/inventory/stock").status_code == 200
        assert client.get("/api/v1/inventory/transactions").status_code == 200
        assert set(modes) == {"deferred"}, f"read requests took write locks: {modes}"

        modes.clear()
        assert _entry(client, csrf, noodles_id, "used", "1").status_code == 200
        assert "immediate" in modes
    finally:
        event.remove(engine, "begin", probe)
