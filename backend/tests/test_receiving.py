"""Delivery receiving tests (Phase 2, Step 3).

Core invariants (D2, D6, D36, D37):
- Physical stock increases ONLY through confirmed receipts, one immutable
  delivery_receipt ledger row per received line.
- Partial deliveries keep the outstanding remainder incoming; only receiving
  may produce partially_delivered/delivered statuses.
- Receiving is atomic, idempotent, concurrency-safe, and never refreshes the
  physical-count confidence (last_counted_at stays untouched).
- Reversals are admin-only, append-only, and cannot drive stock negative.

Seeded demo orders: Tokyo ORDERED (Korean Noodles 100 + Soup Base 20),
Masumoto CONFIRMED (Chashu 200 + Eggs 120), Dragon DRAFT (Chinese Noodles 50).
"""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models import (
    AuditLog,
    DeliveryReceipt,
    Ingredient,
    IngredientStockState,
    InventoryTransaction,
    PurchaseOrder,
    Supplier,
)
from app.seed import DEMO_ADMIN_PASSWORD, DEMO_STAFF_PASSWORD


def _login(client, username, password):
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200
    return response.json()["csrfToken"]


@pytest.fixture()
def make_client(seeded_client):
    def _make():
        return TestClient(seeded_client.app)
    return _make


@pytest.fixture()
def admin(make_client):
    client = make_client()
    return client, _login(client, "admin", DEMO_ADMIN_PASSWORD)


@pytest.fixture()
def staff(make_client):
    client = make_client()
    return client, _login(client, "staff", DEMO_STAFF_PASSWORD)


@pytest.fixture()
def ids(seeded_client):
    db = seeded_client.app.state.session_factory()
    try:
        suppliers = {s.name: s.id for s in db.scalars(select(Supplier)).all()}
        ingredients = {i.name: i.id for i in db.scalars(select(Ingredient)).all()}
        orders = {o.supplier_id: o.id for o in db.scalars(select(PurchaseOrder)).all()}
        return suppliers, ingredients, orders
    finally:
        db.close()


def _key(name):
    return f"test-receipt-{name}"


def _receive(client, csrf, order_id, lines, key, **overrides):
    payload = {"lines": lines, "idempotency_key": key,
               "received_by_staff_name": "Aiko Demo", **overrides}
    return client.post(
        f"/api/v1/purchase-orders/{order_id}/receive",
        json=payload, headers={"X-CSRF-Token": csrf},
    )


def _stock_row(client, ingredient_id):
    rows = client.get("/api/v1/inventory/stock").json()
    return next(r for r in rows if r["ingredient_id"] == ingredient_id)


def _ledger_total(db, ingredient_id):
    return db.scalar(
        select(func.coalesce(func.sum(InventoryTransaction.quantity_delta), 0)).where(
            InventoryTransaction.ingredient_id == ingredient_id
        )
    )


# --- Full and partial deliveries -------------------------------------------------

def test_full_delivery_completes_order_and_posts_stock(staff, ids, seeded_client):
    client, csrf = staff
    suppliers, ingredients, orders = ids
    chashu = ingredients["Chashu"]
    before = Decimal(_stock_row(client, chashu)["current_quantity"])

    response = _receive(client, csrf, orders[suppliers["Masumoto Provisions"]],
                        [{"ingredient_id": chashu, "quantity": "200"},
                         {"ingredient_id": ingredients["Eggs"], "quantity": "120"}],
                        _key("full"), notes="All good")
    assert response.status_code == 201
    receipt = response.json()
    assert receipt["received_by_name"] == "staff"

    order = client.get(
        f"/api/v1/purchase-orders/{orders[suppliers['Masumoto Provisions']]}"
    ).json()
    assert order["status"] == "delivered"
    assert all(i["pending_quantity"] == "0.000" for i in order["items"])

    row = _stock_row(client, chashu)
    assert Decimal(row["current_quantity"]) == before + Decimal("200")
    assert row["incoming_quantity"] == "0.000"
    assert row["incoming_status"] is None

    # Ledger: one delivery_receipt row per line, linked to the receipt.
    db = seeded_client.app.state.session_factory()
    try:
        txn = db.scalar(select(InventoryTransaction).where(
            InventoryTransaction.ingredient_id == chashu,
            InventoryTransaction.type == "delivery_receipt",
        ))
        assert txn.quantity_delta == Decimal("200.000")
        assert txn.entered_by is not None
        link = db.get(DeliveryReceipt, receipt["id"])
        assert link.items and all(
            item.inventory_transaction_id is not None for item in link.items
        )
        # Ledger and cache agree.
        assert _ledger_total(db, chashu) == db.get(
            IngredientStockState, chashu
        ).current_quantity
    finally:
        db.close()


def test_partial_delivery_keeps_outstanding(staff, ids):
    client, csrf = staff
    suppliers, ingredients, orders = ids
    chashu = ingredients["Chashu"]
    before = Decimal(_stock_row(client, chashu)["current_quantity"])

    response = _receive(client, csrf, orders[suppliers["Masumoto Provisions"]],
                        [{"ingredient_id": chashu, "quantity": "80"}],
                        _key("partial"))
    assert response.status_code == 201

    order = client.get(
        f"/api/v1/purchase-orders/{orders[suppliers['Masumoto Provisions']]}"
    ).json()
    assert order["status"] == "partially_delivered"
    item = next(i for i in order["items"] if i["ingredient_id"] == chashu)
    assert item["quantity_received"] == "80.000"
    assert item["pending_quantity"] == "120.000"

    row = _stock_row(client, chashu)
    assert Decimal(row["current_quantity"]) == before + Decimal("80")
    # The outstanding 120 stays incoming (confirmed bucket).
    assert row["incoming_confirmed_quantity"] == "120.000"


def test_multiple_partial_receipts_accumulate(staff, ids):
    client, csrf = staff
    suppliers, ingredients, orders = ids
    order_id = orders[suppliers["Masumoto Provisions"]]
    chashu = ingredients["Chashu"]
    before = Decimal(_stock_row(client, chashu)["current_quantity"])

    assert _receive(client, csrf, order_id,
                    [{"ingredient_id": chashu, "quantity": "80"}],
                    _key("multi-1")).status_code == 201
    assert _receive(client, csrf, order_id,
                    [{"ingredient_id": chashu, "quantity": "120"},
                     {"ingredient_id": ingredients["Eggs"], "quantity": "120"}],
                    _key("multi-2")).status_code == 201

    order = client.get(f"/api/v1/purchase-orders/{order_id}").json()
    assert order["status"] == "delivered"
    assert Decimal(_stock_row(client, chashu)["current_quantity"]) == before + Decimal("200")

    receipts = client.get(f"/api/v1/purchase-orders/{order_id}/receipts").json()
    assert len(receipts) == 2
    assert [r["receipt_date"] for r in receipts]


def test_over_receiving_rejected(staff, ids, seeded_client):
    client, csrf = staff
    suppliers, ingredients, orders = ids
    order_id = orders[suppliers["Masumoto Provisions"]]
    chashu = ingredients["Chashu"]
    before = Decimal(_stock_row(client, chashu)["current_quantity"])

    response = _receive(client, csrf, order_id,
                        [{"ingredient_id": chashu, "quantity": "201"}],
                        _key("over"))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "over_receive"
    # Atomic: nothing was posted.
    assert Decimal(_stock_row(client, chashu)["current_quantity"]) == before
    db = seeded_client.app.state.session_factory()
    try:
        assert db.scalar(select(func.count()).select_from(DeliveryReceipt)) == 0
    finally:
        db.close()


def test_receive_rejects_draft_cancelled_and_delivered(staff, admin, ids):
    staff_client, staff_csrf = staff
    admin_client, admin_csrf = admin
    suppliers, ingredients, orders = ids
    chashu = ingredients["Chashu"]

    draft = orders[suppliers["Dragon Noodle Works"]]
    response = _receive(staff_client, staff_csrf, draft,
                        [{"ingredient_id": ingredients["Chinese Noodles"],
                          "quantity": "50"}], _key("draft"))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_receivable"

    order_id = orders[suppliers["Masumoto Provisions"]]
    assert _receive(staff_client, staff_csrf, order_id,
                    [{"ingredient_id": chashu, "quantity": "200"},
                     {"ingredient_id": ingredients["Eggs"], "quantity": "120"}],
                    _key("fill")).status_code == 201
    delivered = _receive(staff_client, staff_csrf, order_id,
                         [{"ingredient_id": chashu, "quantity": "1"}],
                         _key("delivered"))
    assert delivered.status_code == 409
    assert delivered.json()["error"]["code"] == "not_receivable"

    # Cancel the (still merely ordered) Tokyo order, then receiving must fail.
    cancelled_order = orders[suppliers["Tokyo Noodle Co."]]
    cancel = admin_client.post(
        f"/api/v1/purchase-orders/{cancelled_order}/cancel",
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert cancel.status_code == 200
    response = _receive(staff_client, staff_csrf, cancelled_order,
                        [{"ingredient_id": ingredients["Korean Noodles"],
                          "quantity": "10"}], _key("cancelled"))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_receivable"


def test_idempotent_replay_returns_original_without_double_posting(staff, ids):
    client, csrf = staff
    suppliers, ingredients, orders = ids
    order_id = orders[suppliers["Masumoto Provisions"]]
    chashu = ingredients["Chashu"]
    before = Decimal(_stock_row(client, chashu)["current_quantity"])
    lines = [{"ingredient_id": chashu, "quantity": "50"}]

    first = _receive(client, csrf, order_id, lines, _key("replay"))
    replay = _receive(client, csrf, order_id, lines, _key("replay"))
    assert first.status_code == 201 and replay.status_code == 201
    assert replay.json()["id"] == first.json()["id"]
    # Stock increased exactly once.
    assert Decimal(_stock_row(client, chashu)["current_quantity"]) == before + Decimal("50")

    # Same key, different payload → loud conflict, never silent replay.
    conflict = _receive(client, csrf, order_id,
                        [{"ingredient_id": chashu, "quantity": "51"}],
                        _key("replay"))
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"


@pytest.mark.parametrize("same_key", [False, True])
def test_concurrent_receipts_cannot_over_receive(staff, make_client, ids, same_key):
    """Two simultaneous receipts for the same outstanding quantity: exactly
    one wins; the other is rejected — received never exceeds ordered."""
    import threading

    client, csrf = staff
    suppliers, ingredients, orders = ids
    order_id = orders[suppliers["Masumoto Provisions"]]
    chashu = ingredients["Chashu"]
    lines = [{"ingredient_id": chashu, "quantity": "200"}]
    results = []

    # Log in BOTH clients before racing: login writes a session row, which
    # would otherwise contend with the receive transaction's write lock.
    prepared = []
    for i in range(2):
        other = make_client()
        prepared.append((other, _login(other, "staff", DEMO_STAFF_PASSWORD), i))

    barrier = threading.Barrier(2)

    def attempt(other, other_csrf, i):
        barrier.wait(timeout=10)
        results.append(
            _receive(other, other_csrf, order_id, lines,
                     _key("race-shared" if same_key else f"race-{i}")).status_code
        )

    threads = [threading.Thread(target=attempt, args=args) for args in prepared]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ([201, 201] if same_key else [201, 409])

    order = client.get(f"/api/v1/purchase-orders/{order_id}").json()
    item = next(i for i in order["items"] if i["ingredient_id"] == chashu)
    assert item["quantity_received"] == "200.000"


def test_receiving_does_not_refresh_count_confidence(staff, ids, seeded_client):
    """A receipt is not a physical count: last_counted_at and confidence
    must stay exactly as they were (D3 separation)."""
    client, csrf = staff
    suppliers, ingredients, orders = ids
    chashu = ingredients["Chashu"]
    before = _stock_row(client, chashu)

    assert _receive(client, csrf, orders[suppliers["Masumoto Provisions"]],
                    [{"ingredient_id": chashu, "quantity": "10"}],
                    _key("confidence")).status_code == 201
    after = _stock_row(client, chashu)
    assert after["last_counted_at"] == before["last_counted_at"]
    assert after["confidence"] == before["confidence"]


def test_duplicate_ingredient_lines_in_one_receipt_rejected(staff, ids):
    client, csrf = staff
    suppliers, ingredients, orders = ids
    response = _receive(client, csrf, orders[suppliers["Masumoto Provisions"]],
                        [{"ingredient_id": ingredients["Chashu"], "quantity": "10"},
                         {"ingredient_id": ingredients["Chashu"], "quantity": "5"}],
                        _key("dup"))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "duplicate_item"


def test_receipts_listing_and_audit(staff, ids, seeded_client):
    client, csrf = staff
    suppliers, ingredients, orders = ids
    order_id = orders[suppliers["Masumoto Provisions"]]
    created = _receive(client, csrf, order_id,
                       [{"ingredient_id": ingredients["Chashu"], "quantity": "30"}],
                       _key("audit"), notes="Box damaged")
    assert created.status_code == 201

    receipts = client.get(f"/api/v1/purchase-orders/{order_id}/receipts").json()
    assert len(receipts) == 1
    assert receipts[0]["notes"] == "Box damaged"
    assert receipts[0]["is_reversal"] is False
    assert receipts[0]["reversed_by_id"] is None
    assert receipts[0]["items"][0]["ingredient_name"] == "Chashu"

    db = seeded_client.app.state.session_factory()
    try:
        actions = db.scalars(
            select(AuditLog.action).where(
                AuditLog.entity_id == created.json()["id"],
                AuditLog.entity_type == "delivery_receipt",
            )
        ).all()
    finally:
        db.close()
    assert "receipt.create" in actions


# --- Reversals -----------------------------------------------------------------

def test_admin_can_reverse_a_receipt(staff, admin, ids):
    staff_client, staff_csrf = staff
    admin_client, admin_csrf = admin
    suppliers, ingredients, orders = ids
    order_id = orders[suppliers["Masumoto Provisions"]]
    chashu = ingredients["Chashu"]
    before = Decimal(_stock_row(staff_client, chashu)["current_quantity"])

    receipt = _receive(staff_client, staff_csrf, order_id,
                       [{"ingredient_id": chashu, "quantity": "80"}],
                       _key("reverse-me")).json()
    assert Decimal(_stock_row(staff_client, chashu)["current_quantity"]) == before + Decimal("80")

    reversal = admin_client.post(
        f"/api/v1/receipts/{receipt['id']}/reverse",
        json={"idempotency_key": _key("reversal"), "notes": "Counted wrong",
              "received_by_staff_name": "Manager Demo"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert reversal.status_code == 201
    body = reversal.json()
    assert body["is_reversal"] is True
    assert body["reversal_of_id"] == receipt["id"]
    assert body["items"][0]["quantity"] == "-80.000"

    # Stock and outstanding are back where they started. The order fell back
    # to 'ordered', so the quantity moved from the confirmed to the ordered
    # incoming bucket — but it is still incoming.
    row = _stock_row(staff_client, chashu)
    assert Decimal(row["current_quantity"]) == before
    assert row["incoming_quantity"] == "200.000"
    assert row["incoming_status"] == "ordered"
    # Nothing remains received → the order is factually 'ordered' again.
    order = staff_client.get(f"/api/v1/purchase-orders/{order_id}").json()
    assert order["status"] == "ordered"
    # Reversal is visible and the original is marked.
    receipts = staff_client.get(f"/api/v1/purchase-orders/{order_id}/receipts").json()
    original = next(r for r in receipts if r["id"] == receipt["id"])
    assert original["reversed_by_id"] == body["id"]

    # Double reversal is blocked.
    again = admin_client.post(
        f"/api/v1/receipts/{receipt['id']}/reverse",
        json={"idempotency_key": _key("reversal-2"),
              "received_by_staff_name": "Manager Demo"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "already_reversed"
    # Reversing the reversal is blocked.
    rev_rev = admin_client.post(
        f"/api/v1/receipts/{body['id']}/reverse",
        json={"idempotency_key": _key("reversal-3"),
              "received_by_staff_name": "Manager Demo"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert rev_rev.status_code == 400
    assert rev_rev.json()["error"]["code"] == "cannot_reverse_reversal"


def test_reversal_of_fully_received_order_reopens_it(staff, admin, ids):
    staff_client, staff_csrf = staff
    admin_client, admin_csrf = admin
    suppliers, ingredients, orders = ids
    order_id = orders[suppliers["Masumoto Provisions"]]
    receipt = _receive(staff_client, staff_csrf, order_id,
                       [{"ingredient_id": ingredients["Chashu"], "quantity": "200"},
                        {"ingredient_id": ingredients["Eggs"], "quantity": "120"}],
                       _key("full-for-reversal")).json()
    order = staff_client.get(f"/api/v1/purchase-orders/{order_id}").json()
    assert order["status"] == "delivered"

    admin_client.post(
        f"/api/v1/receipts/{receipt['id']}/reverse",
        json={"idempotency_key": _key("reopen"),
              "received_by_staff_name": "Manager Demo"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    order = staff_client.get(f"/api/v1/purchase-orders/{order_id}").json()
    # Zero received after the reversal → back to 'ordered' (always factual).
    assert order["status"] == "ordered"
    assert all(i["quantity_received"] == "0.000" for i in order["items"])


def test_staff_cannot_reverse_receipts(staff, ids):
    client, csrf = staff
    suppliers, ingredients, orders = ids
    receipt = _receive(client, csrf, orders[suppliers["Masumoto Provisions"]],
                       [{"ingredient_id": ingredients["Chashu"], "quantity": "10"}],
                       _key("staff-receipt")).json()
    response = client.post(
        f"/api/v1/receipts/{receipt['id']}/reverse",
        json={"idempotency_key": _key("staff-reversal"),
              "received_by_staff_name": "Staff Demo"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 403


def test_reversal_refused_when_stock_already_consumed(staff, admin, ids):
    staff_client, staff_csrf = staff
    admin_client, admin_csrf = admin
    suppliers, ingredients, orders = ids
    chashu = ingredients["Chashu"]
    receipt = _receive(staff_client, staff_csrf,
                       orders[suppliers["Masumoto Provisions"]],
                       [{"ingredient_id": chashu, "quantity": "50"}],
                       _key("consume-me")).json()
    # Consume more than the current balance so the reversal would go negative.
    row = _stock_row(staff_client, chashu)
    total = Decimal(row["current_quantity"])
    entry = staff_client.post(
        "/api/v1/inventory/entries",
        json={"ingredient_id": chashu, "mode": "used",
              "quantity": str(total), "reason": "daily_count",
              "idempotency_key": "test-consume-all", "confirmed": True},
        headers={"X-CSRF-Token": staff_csrf},
    )
    assert entry.status_code == 200
    response = admin_client.post(
        f"/api/v1/receipts/{receipt['id']}/reverse",
        json={"idempotency_key": _key("negative-reversal"),
              "received_by_staff_name": "Manager Demo"},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "insufficient_stock"


# --- RBAC / validation ---------------------------------------------------------

def test_receive_requires_auth_and_csrf(seeded_client, staff, ids):
    _suppliers, ingredients, orders = ids
    order_id = list(orders.values())[0]
    anon = TestClient(seeded_client.app)
    assert anon.post(f"/api/v1/purchase-orders/{order_id}/receive",
                     json={}).status_code == 401
    client, _csrf = staff  # logged in, but no CSRF header
    response = client.post(
        f"/api/v1/purchase-orders/{order_id}/receive",
        json={"lines": [{"ingredient_id": ingredients["Chashu"], "quantity": "1"}],
              "idempotency_key": _key("csrf")},
    )
    assert response.status_code == 403


def test_receive_unknown_order_and_item_validation(staff, ids):
    client, csrf = staff
    suppliers, ingredients, orders = ids
    assert _receive(client, csrf, 9999,
                    [{"ingredient_id": ingredients["Chashu"], "quantity": "1"}],
                    _key("unknown-order")).status_code == 404
    # Korean Noodles is not part of Masumoto's order.
    response = _receive(client, csrf, orders[suppliers["Masumoto Provisions"]],
                        [{"ingredient_id": ingredients["Korean Noodles"],
                          "quantity": "1"}], _key("unknown-item"))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "item_not_in_order"
    # All-zero lines are rejected by the service (schema allows 0 accepted
    # when goods are rejected instead).
    response = _receive(client, csrf, orders[suppliers["Masumoto Provisions"]],
                        [{"ingredient_id": ingredients["Chashu"], "quantity": "0"}],
                        _key("zero"))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_quantity"


# --- Damaged / rejected quantities ------------------------------------------------

def test_damaged_quantities_never_enter_stock_or_fulfill(staff, ids):
    """Rejected goods are recorded for audit but do not increase stock and
    do NOT reduce the supplier's outstanding obligation."""
    client, csrf = staff
    suppliers, ingredients, orders = ids
    order_id = orders[suppliers["Masumoto Provisions"]]
    chashu = ingredients["Chashu"]
    before = Decimal(_stock_row(client, chashu)["current_quantity"])

    response = _receive(client, csrf, order_id,
                        [{"ingredient_id": chashu, "quantity": "45",
                          "quantity_rejected": "5"}],
                        _key("damaged"), notes="5 packs damaged in transit")
    assert response.status_code == 201
    item = response.json()["items"][0]
    assert item["quantity"] == "45.000"
    assert item["quantity_rejected"] == "5.000"

    row = _stock_row(client, chashu)
    # Only the accepted 45 entered stock.
    assert Decimal(row["current_quantity"]) == before + Decimal("45")
    order = client.get(f"/api/v1/purchase-orders/{order_id}").json()
    order_item = next(i for i in order["items"] if i["ingredient_id"] == chashu)
    assert order_item["quantity_received"] == "45.000"
    # Outstanding = 200 - 45 accepted; the 5 rejected are STILL owed.
    assert order_item["pending_quantity"] == "155.000"


def test_fully_rejected_line_posts_no_stock(staff, ids, seeded_client):
    client, csrf = staff
    suppliers, ingredients, orders = ids
    order_id = orders[suppliers["Masumoto Provisions"]]
    chashu = ingredients["Chashu"]
    before = Decimal(_stock_row(client, chashu)["current_quantity"])

    response = _receive(client, csrf, order_id,
                        [{"ingredient_id": chashu, "quantity": "0",
                          "quantity_rejected": "10"}],
                        _key("all-rejected"), notes="Entire crate spoiled")
    assert response.status_code == 201
    assert response.json()["items"][0]["inventory_transaction_id"] is None
    assert Decimal(_stock_row(client, chashu)["current_quantity"]) == before
    db = seeded_client.app.state.session_factory()
    try:
        assert db.scalar(select(func.count()).select_from(InventoryTransaction).where(
            InventoryTransaction.type == "delivery_receipt"
        )) == 0
    finally:
        db.close()


# --- Atomic rollback --------------------------------------------------------------

def test_atomic_rollback_when_one_line_fails(staff, ids, seeded_client):
    """A failing second line must roll back the first line completely:
    no stock change, no receipt, no ledger rows."""
    client, csrf = staff
    suppliers, ingredients, orders = ids
    order_id = orders[suppliers["Masumoto Provisions"]]
    chashu = ingredients["Chashu"]
    eggs = ingredients["Eggs"]
    before_chashu = Decimal(_stock_row(client, chashu)["current_quantity"])
    before_eggs = Decimal(_stock_row(client, eggs)["current_quantity"])

    response = _receive(client, csrf, order_id,
                        [{"ingredient_id": chashu, "quantity": "10"},
                         {"ingredient_id": eggs, "quantity": "121"}],  # 1 too many
                        _key("rollback"))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "over_receive"

    assert Decimal(_stock_row(client, chashu)["current_quantity"]) == before_chashu
    assert Decimal(_stock_row(client, eggs)["current_quantity"]) == before_eggs
    db = seeded_client.app.state.session_factory()
    try:
        assert db.scalar(select(func.count()).select_from(DeliveryReceipt)) == 0
        assert db.scalar(select(func.count()).select_from(InventoryTransaction).where(
            InventoryTransaction.type == "delivery_receipt"
        )) == 0
        order = db.get(PurchaseOrder, order_id)
        assert order.status == "confirmed"
        assert all(item.quantity_received == 0 for item in order.items)
    finally:
        db.close()


# --- Acceptance scenario (spec): 100 ordered, 80 then 20 received -------------------

def test_acceptance_scenario_partial_then_full(staff, ids):
    """Korean Noodles 100 ordered: receipt of 80 → partially delivered,
    stock +80, incoming -80; receipt of the remaining 20 → delivered."""
    client, csrf = staff
    suppliers, ingredients, _orders = ids
    noodles = ingredients["Korean Noodles"]
    created = client.post(
        "/api/v1/purchase-orders",
        json={"supplier_id": suppliers["Tokyo Noodle Co."],
              "expected_delivery_date": "2026-11-25",
              "items": [{"ingredient_id": noodles, "quantity_ordered": "100"}]},
        headers={"X-CSRF-Token": csrf},
    ).json()
    client.post(f"/api/v1/purchase-orders/{created['id']}/mark-ordered",
                headers={"X-CSRF-Token": csrf})

    stock_before = Decimal(_stock_row(client, noodles)["current_quantity"])
    incoming_before = Decimal(_stock_row(client, noodles)["incoming_quantity"])

    first = _receive(client, csrf, created["id"],
                     [{"ingredient_id": noodles, "quantity": "80"}],
                     _key("scenario-80"))
    assert first.status_code == 201
    order = client.get(f"/api/v1/purchase-orders/{created['id']}").json()
    assert order["status"] == "partially_delivered"
    item = order["items"][0]
    assert item["quantity_received"] == "80.000"
    assert item["pending_quantity"] == "20.000"
    row = _stock_row(client, noodles)
    assert Decimal(row["current_quantity"]) == stock_before + Decimal("80")
    assert Decimal(row["incoming_quantity"]) == incoming_before - Decimal("80")

    second = _receive(client, csrf, created["id"],
                      [{"ingredient_id": noodles, "quantity": "20"}],
                      _key("scenario-20"))
    assert second.status_code == 201
    order = client.get(f"/api/v1/purchase-orders/{created['id']}").json()
    assert order["status"] == "delivered"
    row = _stock_row(client, noodles)
    assert Decimal(row["current_quantity"]) == stock_before + Decimal("100")
    assert Decimal(row["incoming_quantity"]) == incoming_before - Decimal("100")


# --- Received By (staff name) — final review requirement -------------------------

def test_staff_name_is_required_and_stripped(staff, ids):
    client, csrf = staff
    suppliers, ingredients, orders = ids
    order_id = orders[suppliers["Masumoto Provisions"]]
    line = [{"ingredient_id": ingredients["Chashu"], "quantity": "5"}]

    missing = client.post(
        f"/api/v1/purchase-orders/{order_id}/receive",
        json={"lines": line, "idempotency_key": _key("name-missing")},
        headers={"X-CSRF-Token": csrf},
    )
    assert missing.status_code == 422

    for blank in ("", "   "):
        response = _receive(client, csrf, order_id, line, _key("name-blank"),
                            received_by_staff_name=blank)
        assert response.status_code == 422, f"blank name {blank!r} accepted"


def test_staff_name_is_persisted_and_account_id_kept(staff, ids, seeded_client):
    client, csrf = staff
    suppliers, ingredients, orders = ids
    order_id = orders[suppliers["Masumoto Provisions"]]

    receipt = _receive(client, csrf, order_id,
                       [{"ingredient_id": ingredients["Chashu"], "quantity": "5"}],
                       _key("name-persist"),
                       received_by_staff_name="  Kenji Demo  ").json()
    assert receipt["received_by_staff_name"] == "Kenji Demo"  # stripped
    assert receipt["received_by_name"] == "staff"  # account, for internal audit

    db = seeded_client.app.state.session_factory()
    try:
        row = db.get(DeliveryReceipt, receipt["id"])
        assert row.received_by_staff_name == "Kenji Demo"
        assert row.received_by == receipt["received_by"]
    finally:
        db.close()


def test_partial_receipts_can_have_different_receivers(staff, ids):
    client, csrf = staff
    suppliers, ingredients, orders = ids
    order_id = orders[suppliers["Masumoto Provisions"]]
    chashu = ingredients["Chashu"]

    first = _receive(client, csrf, order_id,
                     [{"ingredient_id": chashu, "quantity": "10"}],
                     _key("name-1"), received_by_staff_name="Kenji Demo").json()
    second = _receive(client, csrf, order_id,
                      [{"ingredient_id": chashu, "quantity": "10"}],
                      _key("name-2"), received_by_staff_name="Aiko Demo").json()
    receipts = client.get(f"/api/v1/purchase-orders/{order_id}/receipts").json()
    by_id = {r["id"]: r for r in receipts}
    assert by_id[first["id"]]["received_by_staff_name"] == "Kenji Demo"
    assert by_id[second["id"]]["received_by_staff_name"] == "Aiko Demo"
    assert by_id[first["id"]]["items"][0]["outstanding_quantity"] == "190.000"
    assert by_id[second["id"]]["items"][0]["outstanding_quantity"] == "180.000"


def test_reversal_preserves_original_staff_name(staff, admin, ids):
    staff_client, staff_csrf = staff
    admin_client, admin_csrf = admin
    suppliers, ingredients, orders = ids
    order_id = orders[suppliers["Masumoto Provisions"]]

    receipt = _receive(staff_client, staff_csrf, order_id,
                       [{"ingredient_id": ingredients["Chashu"], "quantity": "5"}],
                       _key("name-reversal"), received_by_staff_name="Kenji Demo").json()
    reversal = admin_client.post(
        f"/api/v1/receipts/{receipt['id']}/reverse",
        json={"idempotency_key": _key("name-reversal-rev"),
              "received_by_staff_name": "Manager Demo"},
        headers={"X-CSRF-Token": admin_csrf},
    ).json()
    assert reversal["received_by_staff_name"] == "Manager Demo"

    # History preserves BOTH names; the original is never rewritten.
    receipts = staff_client.get(f"/api/v1/purchase-orders/{order_id}/receipts").json()
    original = next(r for r in receipts if r["id"] == receipt["id"])
    assert original["received_by_staff_name"] == "Kenji Demo"
    assert original["reversed_by_id"] == reversal["id"]


@pytest.mark.parametrize("changed", [
    {"received_by_staff_name": "Another employee"},
    {"notes": "Different notes"},
])
def test_replay_rejects_changed_identity_or_notes(staff, ids, changed):
    client, csrf = staff
    suppliers, ingredients, orders = ids
    order_id = orders[suppliers["Masumoto Provisions"]]
    lines = [{"ingredient_id": ingredients["Chashu"], "quantity": "5"}]
    first = _receive(client, csrf, order_id, lines, _key("metadata"))
    assert first.status_code == 201
    replay = _receive(client, csrf, order_id, lines, _key("metadata"), **changed)
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "idempotency_conflict"
    assert len(client.get(f"/api/v1/purchase-orders/{order_id}/receipts").json()) == 1


def test_implicit_date_replay_survives_business_day_change(staff, ids, monkeypatch):
    from datetime import datetime, timezone
    from app.services import receiving

    client, csrf = staff
    suppliers, ingredients, orders = ids
    order_id = orders[suppliers["Masumoto Provisions"]]
    lines = [{"ingredient_id": ingredients["Chashu"], "quantity": "5"}]
    monkeypatch.setattr(receiving, "_now", lambda: datetime(2026, 11, 10, tzinfo=timezone.utc))
    first = _receive(client, csrf, order_id, lines, _key("midnight"))
    monkeypatch.setattr(receiving, "_now", lambda: datetime(2026, 11, 11, tzinfo=timezone.utc))
    replay = _receive(client, csrf, order_id, lines, _key("midnight"))
    assert first.status_code == replay.status_code == 201
    assert first.json()["id"] == replay.json()["id"]


def test_arrived_total_cannot_exceed_outstanding(staff, ids):
    client, csrf = staff
    suppliers, ingredients, orders = ids
    order_id = orders[suppliers["Masumoto Provisions"]]
    response = _receive(client, csrf, order_id,
                        [{"ingredient_id": ingredients["Chashu"], "quantity": "195",
                          "quantity_rejected": "6"}], _key("arrived-over"))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "over_receive"


def test_reversal_retry_checks_identity_and_preserves_historical_outstanding(staff, admin, ids):
    client, csrf = staff
    admin_client, admin_csrf = admin
    suppliers, ingredients, orders = ids
    order_id = orders[suppliers["Masumoto Provisions"]]
    receipt = _receive(client, csrf, order_id,
                       [{"ingredient_id": ingredients["Chashu"], "quantity": "75",
                         "quantity_rejected": "5"}], _key("history-original")).json()
    payload = {"idempotency_key": _key("history-reversal"),
               "received_by_staff_name": "Manager Demo"}
    url = f"/api/v1/receipts/{receipt['id']}/reverse"
    headers = {"X-CSRF-Token": admin_csrf}
    reversed_response = admin_client.post(url, json=payload, headers=headers)
    assert reversed_response.status_code == 201
    replay = admin_client.post(url, json=payload, headers=headers)
    assert replay.json()["id"] == reversed_response.json()["id"]
    for change in ({"received_by_staff_name": "Other Manager"}, {"notes": "Changed"}):
        assert admin_client.post(url, json={**payload, **change}, headers=headers).status_code == 409
    receipts = client.get(f"/api/v1/purchase-orders/{order_id}/receipts").json()
    assert receipts[0]["items"][0]["outstanding_quantity"] == "125.000"
    assert receipts[1]["items"][0]["outstanding_quantity"] == "200.000"
    assert receipts[1]["items"][0]["quantity_rejected"] == "-5.000"
    assert receipts[0]["received_at"].endswith("Z")
