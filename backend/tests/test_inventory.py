"""Inventory ledger tests (Phase 1, Step 4).

Covers both entry modes, idempotency, REAL concurrent submissions, negative
guards, large-adjustment confirmation, corrections, and ledger verify/repair.
All 37 pre-existing tests must stay green.

Demo data: Korean Noodles (id resolved by name) starts at 180 pieces.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import Ingredient, IngredientStockState, InventoryTransaction
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
def make_client(seeded_client):
    """Independent TestClients sharing the same app/db but NOT cookies —
    so admin and staff sessions never overwrite each other."""
    def _make():
        return TestClient(seeded_client.app)
    return _make


@pytest.fixture()
def admin(make_client):
    client = make_client()
    csrf = _login(client, "admin", DEMO_ADMIN_PASSWORD)
    return client, csrf


@pytest.fixture()
def staff(make_client):
    client = make_client()
    csrf = _login(client, "staff", DEMO_STAFF_PASSWORD)
    return client, csrf


@pytest.fixture()
def noodles_id(seeded_client):
    db = seeded_client.app.state.session_factory()
    try:
        row = db.scalar(select(Ingredient).where(Ingredient.name == "Korean Noodles"))
        return row.id
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


# --- Mode A: remaining stock (reconciliation) ------------------------------

def test_remaining_stock_reconciles_and_marks_verification(admin, noodles_id):
    client, csrf = admin
    response = _entry(client, csrf, noodles_id, "remaining", "200")
    assert response.status_code == 200
    body = response.json()
    transaction = body["transaction"]
    assert body["idempotent_replay"] is False
    assert transaction["type"] == "reconciliation"
    assert transaction["mode"] == "remaining"
    assert Decimal(transaction["quantity_delta"]) == Decimal("20")   # 200 - 180
    assert Decimal(transaction["resulting_quantity"]) == Decimal("200")
    assert Decimal(transaction["previous_quantity"]) == Decimal("180")
    assert _balance(client, noodles_id) == Decimal("200")

    rows = client.get("/api/v1/inventory/stock").json()
    state = next(r for r in rows if r["ingredient_id"] == noodles_id)
    assert state["last_counted_at"] is not None     # physical verification (D3)
    assert state["confidence"] == "high"


def test_used_stock_deducts_without_verification(admin, noodles_id):
    client, csrf = admin
    before = client.get("/api/v1/inventory/stock").json()
    counted_before = next(r for r in before if r["ingredient_id"] == noodles_id)["last_counted_at"]

    response = _entry(client, csrf, noodles_id, "used", "35")
    assert response.status_code == 200
    transaction = response.json()["transaction"]
    assert transaction["type"] == "consumption"
    assert transaction["mode"] == "used"
    assert Decimal(transaction["quantity_delta"]) == Decimal("-35")
    assert Decimal(transaction["resulting_quantity"]) == Decimal("145")

    after = client.get("/api/v1/inventory/stock").json()
    state = next(r for r in after if r["ingredient_id"] == noodles_id)
    assert state["last_counted_at"] == counted_before  # NOT a verification (D3)


# --- Idempotency ------------------------------------------------------------

def test_duplicate_submission_applies_once(admin, noodles_id):
    client, csrf = admin
    payload_key = _key()
    first = _entry(client, csrf, noodles_id, "used", "10", idempotency_key=payload_key)
    second = _entry(client, csrf, noodles_id, "used", "10", idempotency_key=payload_key)
    assert first.status_code == 200 == second.status_code
    assert second.json()["idempotent_replay"] is True
    assert first.json()["transaction"]["id"] == second.json()["transaction"]["id"]
    assert _balance(client, noodles_id) == Decimal("170")  # 180 - 10 exactly once


# --- Concurrency (D15) --------------------------------------------------------

def test_concurrent_entries_never_lose_updates(admin, noodles_id):
    """8 parallel "used 5" submissions: final balance must be exactly 140."""
    client, csrf = admin

    def submit(index):
        return client.post(
            "/api/v1/inventory/entries",
            json={
                "ingredient_id": noodles_id,
                "mode": "used",
                "quantity": "5",
                "reason": "daily_count",
                "idempotency_key": _key(),
                "confirmed": False,
                "allow_negative": False,
            },
            headers={"X-CSRF-Token": csrf},
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(submit, range(8)))

    assert all(r.status_code == 200 for r in responses)
    assert _balance(client, noodles_id) == Decimal("140")  # 180 - 8×5, no lost updates

    history = client.get(
        "/api/v1/inventory/transactions",
        params={"ingredient_id": noodles_id, "type": "consumption"},
    ).json()
    assert len(history) == 8


# --- Validation ---------------------------------------------------------------

def test_negative_stock_prevented_for_staff(admin, noodles_id):
    client, csrf = admin
    response = _entry(client, csrf, noodles_id, "used", "999")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "negative_stock"
    assert _balance(client, noodles_id) == Decimal("180")  # unchanged


def test_admin_allow_negative_override(admin, noodles_id):
    client, csrf = admin
    response = _entry(
        client, csrf, noodles_id, "used", "999",
        allow_negative=True, confirmed=True,
    )
    assert response.status_code == 200
    assert _balance(client, noodles_id) == Decimal("-819")


def test_large_adjustment_requires_confirmation(admin, noodles_id):
    client, csrf = admin
    # 1000 - 180 = +820 delta, above the 500 threshold -> confirmation required.
    response = _entry(client, csrf, noodles_id, "remaining", "1000")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "confirmation_required"
    assert _balance(client, noodles_id) == Decimal("180")  # not applied

    confirmed = _entry(client, csrf, noodles_id, "remaining", "1000", confirmed=True)
    assert confirmed.status_code == 200
    assert _balance(client, noodles_id) == Decimal("1000")


def test_invalid_quantities_rejected(admin, noodles_id):
    client, csrf = admin
    assert _entry(client, csrf, noodles_id, "used", "0").status_code == 400
    assert _entry(client, csrf, noodles_id, "used", "-5").status_code == 422
    assert _entry(client, csrf, noodles_id, "used", "1.0001").status_code == 422
    assert _entry(client, csrf, noodles_id, "used", "abc").status_code == 422


def test_archived_ingredient_rejected(admin, noodles_id):
    client, csrf = admin
    db = client.app.state.session_factory()
    ingredient = db.get(Ingredient, noodles_id)
    ingredient.status = "archived"
    db.commit()
    db.close()
    response = _entry(client, csrf, noodles_id, "used", "1")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "ingredient_inactive"


# --- Auth & permissions ---------------------------------------------------------

def test_unauthenticated_cannot_post_entries(make_client, noodles_id):
    anonymous = make_client()  # never logged in
    response = anonymous.post(
        "/api/v1/inventory/entries",
        json={
            "ingredient_id": noodles_id, "mode": "used", "quantity": "1",
            "reason": "daily_count", "idempotency_key": _key(),
        },
    )
    assert response.status_code == 401


def test_staff_can_post_but_not_verify_or_repair(staff, noodles_id):
    client, csrf = staff
    assert _entry(client, csrf, noodles_id, "used", "1").status_code == 200
    assert client.get("/api/v1/inventory/verify").status_code == 403
    assert client.post(
        "/api/v1/inventory/verify/repair", headers={"X-CSRF-Token": csrf}
    ).status_code == 403


# --- Corrections -----------------------------------------------------------------

def _post_entry(client, csrf, ingredient_id, mode, quantity):
    response = _entry(client, csrf, ingredient_id, mode, quantity)
    assert response.status_code == 200
    return response.json()["transaction"]


def test_correction_fixes_used_entry_without_erasing_it(staff, noodles_id):
    client, csrf = staff
    original = _post_entry(client, csrf, noodles_id, "used", "50")   # oops: meant 5
    assert _balance(client, noodles_id) == Decimal("130")

    response = client.post(
        f"/api/v1/inventory/transactions/{original['id']}/correct",
        json={
            "corrected_quantity": "5",
            "reason": "correction",
            "idempotency_key": _key(),
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200
    correction = response.json()["transaction"]
    assert correction["type"] == "correction"
    assert Decimal(correction["quantity_delta"]) == Decimal("45")     # -5 - (-50)
    assert correction["supersedes_id"] == original["id"]
    assert _balance(client, noodles_id) == Decimal("175")

    # The ORIGINAL row is untouched (immutable ledger, D2).
    history = client.get("/api/v1/inventory/transactions",
                         params={"ingredient_id": noodles_id}).json()
    original_row = next(r for r in history if r["id"] == original["id"])
    assert Decimal(original_row["quantity_delta"]) == Decimal("-50")


def test_correction_stays_consistent_after_later_entries(staff, noodles_id):
    client, csrf = staff
    original = _post_entry(client, csrf, noodles_id, "used", "50")     # balance 130
    _post_entry(client, csrf, noodles_id, "used", "10")                # balance 120
    client.post(
        f"/api/v1/inventory/transactions/{original['id']}/correct",
        json={"corrected_quantity": "5", "reason": "correction", "idempotency_key": _key()},
        headers={"X-CSRF-Token": csrf},
    )
    # Correction compensates the CURRENT balance: 120 + 45 = 165.
    assert _balance(client, noodles_id) == Decimal("165")

    # And the ledger still reconstructs the cache exactly.
    db = client.app.state.session_factory()
    ledger_sum = db.scalar(
        select(__import__("sqlalchemy").func.coalesce(
            __import__("sqlalchemy").func.sum(InventoryTransaction.quantity_delta), 0
        )).where(InventoryTransaction.ingredient_id == noodles_id)
    )
    state = db.get(IngredientStockState, noodles_id)
    assert Decimal(ledger_sum) == state.current_quantity
    db.close()


def test_double_correction_prevented(staff, noodles_id):
    client, csrf = staff
    original = _post_entry(client, csrf, noodles_id, "used", "50")
    url = f"/api/v1/inventory/transactions/{original['id']}/correct"
    payload = {"corrected_quantity": "5", "reason": "correction", "idempotency_key": _key()}
    assert client.post(url, json=payload, headers={"X-CSRF-Token": csrf}).status_code == 200
    second = client.post(
        url, json={**payload, "idempotency_key": _key()}, headers={"X-CSRF-Token": csrf}
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "already_corrected"


def test_staff_cannot_correct_others_entries(admin, staff, noodles_id):
    admin_client, admin_csrf = admin
    staff_client, staff_csrf = staff
    original = _post_entry(admin_client, admin_csrf, noodles_id, "used", "10")
    response = staff_client.post(
        f"/api/v1/inventory/transactions/{original['id']}/correct",
        json={"corrected_quantity": "5", "reason": "correction", "idempotency_key": _key()},
        headers={"X-CSRF-Token": staff_csrf},
    )
    assert response.status_code == 403


def test_admin_can_correct_any_entry(admin, staff, noodles_id):
    admin_client, admin_csrf = admin
    staff_client, staff_csrf = staff
    original = _post_entry(staff_client, staff_csrf, noodles_id, "used", "10")
    response = admin_client.post(
        f"/api/v1/inventory/transactions/{original['id']}/correct",
        json={"corrected_quantity": "5", "reason": "correction", "idempotency_key": _key()},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert response.status_code == 200


# --- Ledger verification & repair --------------------------------------------------

def test_verify_reports_consistent_ledger(admin):
    client, _ = admin
    body = client.get("/api/v1/inventory/verify").json()
    assert body["consistent"] is True
    assert len(body["rows"]) == 8


def test_repair_fixes_drift_and_is_audited(admin, noodles_id):
    client, csrf = admin
    # Simulate manual DB tampering: cache drifts from the ledger.
    db = client.app.state.session_factory()
    state = db.get(IngredientStockState, noodles_id)
    state.current_quantity = Decimal("999")
    db.commit()
    db.close()

    verify = client.get("/api/v1/inventory/verify").json()
    assert verify["consistent"] is False

    repair = client.post(
        "/api/v1/inventory/verify/repair", headers={"X-CSRF-Token": csrf}
    )
    assert repair.status_code == 200
    body = repair.json()
    assert body["drifted"] == 1
    assert noodles_id in body["repaired_ingredient_ids"]
    assert _balance(client, noodles_id) == Decimal("180")  # restored FROM ledger

    # Transactions untouched, discrepancy flagged, audit row written.
    history = client.get("/api/v1/inventory/transactions",
                         params={"ingredient_id": noodles_id}).json()
    assert len(history) == 1  # only the opening balance
    db = client.app.state.session_factory()
    state = db.get(IngredientStockState, noodles_id)
    assert state.unresolved_discrepancy is True
    assert state.confidence == "low"
    from app.models import AuditLog
    audit = db.scalar(
        select(AuditLog).where(AuditLog.action == "ledger.repair")
    )
    assert audit is not None
    db.close()


# --- Business dates ---------------------------------------------------------------

def test_business_date_respects_timezone_and_boundary():
    from datetime import datetime, time, timezone
    from app.services.business_time import business_date_for

    # 2026-01-05 17:00 UTC = 2026-01-06 02:00 JST. Midnight boundary -> Jan 6.
    moment = datetime(2026, 1, 5, 17, 0, tzinfo=timezone.utc)
    assert business_date_for(moment, "Asia/Tokyo", time(0, 0)).isoformat() == "2026-01-06"
    # With a 04:00 boundary, 02:00 JST still belongs to the PREVIOUS business day.
    assert business_date_for(moment, "Asia/Tokyo", time(4, 0)).isoformat() == "2026-01-05"
