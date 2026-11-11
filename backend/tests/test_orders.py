"""Purchase order and incoming-stock tests (Phase 2, Step 2).

Core invariant (D36): available stock comes ONLY from the inventory ledger;
orders contribute pending incoming quantities by status — drafts and
cancelled orders never count, ordered/delayed are unconfirmed, confirmed/
partially delivered are confirmed. Projected = available + incoming, always
labeled and never confused with physical stock.

Seeded demo orders: Tokyo Noodle Co. has an ORDERED order (Korean Noodles
100 + Soup Base 20), Masumoto a CONFIRMED order (Chashu 200 + Eggs 120),
Dragon a DRAFT (Chinese Noodles 50 — never counted). Nori has no seeded
orders, so math tests use it.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import AuditLog, Ingredient, PurchaseOrder, PurchaseOrderItem, Supplier
from app.seed import DEMO_ADMIN_PASSWORD, DEMO_STAFF_PASSWORD

FUTURE = (date(2026, 11, 11) + timedelta(days=14)).isoformat()


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
        return suppliers, ingredients
    finally:
        db.close()


def _create(client, csrf, supplier_id, items, **overrides):
    payload = {
        "supplier_id": supplier_id,
        "expected_delivery_date": FUTURE,
        "items": items,
        **overrides,
    }
    return client.post(
        "/api/v1/purchase-orders", json=payload, headers={"X-CSRF-Token": csrf}
    )


def _nori_order_payload(ingredient_id, quantity="50"):
    return [{"ingredient_id": ingredient_id, "quantity_ordered": quantity}]


def _stock_row(client, ingredient_id):
    rows = client.get("/api/v1/inventory/stock").json()
    return next(r for r in rows if r["ingredient_id"] == ingredient_id)


def _transition(client, csrf, order_id, action):
    return client.post(
        f"/api/v1/purchase-orders/{order_id}/{action}",
        headers={"X-CSRF-Token": csrf},
    )


# --- Seeded demo orders ------------------------------------------------------

def test_seeded_orders_show_ordered_and_confirmed_buckets(staff, ids):
    client, _ = staff
    _, ingredients = ids
    noodles = _stock_row(client, ingredients["Korean Noodles"])
    assert noodles["incoming_quantity"] == "100.000"
    assert noodles["incoming_ordered_quantity"] == "100.000"
    assert noodles["incoming_confirmed_quantity"] == "0.000"
    assert noodles["incoming_status"] == "ordered"
    assert noodles["expected_delivery"] is not None
    assert Decimal(noodles["projected_quantity"]) == (
        Decimal(noodles["current_quantity"]) + Decimal("100")
    )
    chashu = _stock_row(client, ingredients["Chashu"])
    assert chashu["incoming_confirmed_quantity"] == "200.000"
    assert chashu["incoming_status"] == "confirmed"


def test_seeded_draft_order_never_counts_as_supply(staff, ids):
    client, _ = staff
    _, ingredients = ids
    chinese = _stock_row(client, ingredients["Chinese Noodles"])
    assert chinese["incoming_quantity"] == "0.000"
    assert chinese["incoming_status"] is None
    assert chinese["expected_delivery"] is None


# --- Status transitions drive incoming stock -------------------------------------

def test_draft_then_ordered_lifecycle(staff, ids):
    """Draft: no incoming. Ordered: unconfirmed incoming. Available unchanged."""
    client, csrf = staff
    suppliers, ingredients = ids
    nori = ingredients["Nori"]
    before = Decimal(_stock_row(client, nori)["current_quantity"])

    created = _create(client, csrf, suppliers["Masumoto Provisions"],
                      _nori_order_payload(nori, "50"))
    assert created.status_code == 201
    order = created.json()
    assert order["status"] == "draft"
    assert order["ordered_at"] is None

    row = _stock_row(client, nori)
    assert row["incoming_quantity"] == "0.000"  # draft is never supply
    assert Decimal(row["current_quantity"]) == before

    ordered = _transition(client, csrf, order["id"], "mark-ordered")
    assert ordered.status_code == 200
    assert ordered.json()["status"] == "ordered"
    assert ordered.json()["ordered_at"] is not None
    assert ordered.json()["ordered_by_name"] == "staff"

    row = _stock_row(client, nori)
    assert row["incoming_quantity"] == "50.000"
    assert row["incoming_ordered_quantity"] == "50.000"
    assert row["incoming_confirmed_quantity"] == "0.000"
    assert row["incoming_status"] == "ordered"
    assert Decimal(row["current_quantity"]) == before  # stock untouched
    assert Decimal(row["projected_quantity"]) == before + Decimal("50")


def test_confirm_moves_quantity_to_confirmed_bucket(staff, ids):
    client, csrf = staff
    suppliers, ingredients = ids
    nori = ingredients["Nori"]
    order = _create(client, csrf, suppliers["Masumoto Provisions"],
                    _nori_order_payload(nori, "30")).json()
    _transition(client, csrf, order["id"], "mark-ordered")
    confirmed = _transition(client, csrf, order["id"], "confirm")
    assert confirmed.status_code == 200
    row = _stock_row(client, nori)
    assert row["incoming_ordered_quantity"] == "0.000"
    assert row["incoming_confirmed_quantity"] == "30.000"
    assert row["incoming_status"] == "confirmed"


def test_cancel_removes_incoming(staff, admin, ids):
    staff_client, staff_csrf = staff
    admin_client, admin_csrf = admin
    suppliers, ingredients = ids
    nori = ingredients["Nori"]
    order = _create(staff_client, staff_csrf, suppliers["Masumoto Provisions"],
                    _nori_order_payload(nori, "25")).json()
    _transition(staff_client, staff_csrf, order["id"], "mark-ordered")
    assert _stock_row(staff_client, nori)["incoming_quantity"] == "25.000"
    cancelled = _transition(admin_client, admin_csrf, order["id"], "cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert _stock_row(staff_client, nori)["incoming_quantity"] == "0.000"


def test_delayed_stays_incoming_with_delayed_status(staff, ids):
    client, csrf = staff
    suppliers, ingredients = ids
    nori = ingredients["Nori"]
    order = _create(client, csrf, suppliers["Masumoto Provisions"],
                    _nori_order_payload(nori, "40")).json()
    _transition(client, csrf, order["id"], "mark-ordered")
    delayed = _transition(client, csrf, order["id"], "delay")
    assert delayed.status_code == 200
    row = _stock_row(client, nori)
    assert row["incoming_quantity"] == "40.000"
    assert row["incoming_status"] == "delayed"
    # Confirming a delayed order is possible again.
    assert _transition(client, csrf, order["id"], "confirm").status_code == 200


def test_multiple_orders_sum_without_double_counting(staff, admin, ids):
    staff_client, staff_csrf = staff
    admin_client, admin_csrf = admin
    suppliers, ingredients = ids
    nori = ingredients["Nori"]
    first = _create(staff_client, staff_csrf, suppliers["Masumoto Provisions"],
                    _nori_order_payload(nori, "10")).json()
    second = _create(admin_client, admin_csrf, suppliers["Masumoto Provisions"],
                     _nori_order_payload(nori, "15")).json()
    for order in (first, second):
        _transition(staff_client, staff_csrf, order["id"], "mark-ordered")
    row = _stock_row(staff_client, nori)
    assert row["incoming_quantity"] == "25.000"  # 10 + 15, exactly once each


def test_received_quantity_reduces_pending(staff, ids, seeded_client):
    """Step 3 will set quantity_received; the pending math must honor it."""
    client, csrf = staff
    suppliers, ingredients = ids
    nori = ingredients["Nori"]
    order = _create(client, csrf, suppliers["Masumoto Provisions"],
                    _nori_order_payload(nori, "50")).json()
    _transition(client, csrf, order["id"], "confirm")

    db = seeded_client.app.state.session_factory()
    try:
        item = db.scalar(select(PurchaseOrderItem).where(
            PurchaseOrderItem.order_id == order["id"]))
        item.quantity_received = Decimal("20")
        # partial delivery status is set by the receipt flow; simulate it
        db.scalar(select(PurchaseOrder).where(
            PurchaseOrder.id == order["id"])).status = "partially_delivered"
        db.commit()
    finally:
        db.close()

    row = _stock_row(client, nori)
    assert row["incoming_quantity"] == "30.000"
    assert row["incoming_confirmed_quantity"] == "30.000"
    assert row["incoming_status"] == "confirmed"


def test_transitions_are_idempotent_and_invalid_ones_409(staff, ids):
    client, csrf = staff
    suppliers, ingredients = ids
    order = _create(client, csrf, suppliers["Masumoto Provisions"],
                    _nori_order_payload(ingredients["Nori"])).json()
    # confirm from draft is invalid
    assert _transition(client, csrf, order["id"], "confirm").status_code == 409
    # double mark-ordered: idempotent
    assert _transition(client, csrf, order["id"], "mark-ordered").status_code == 200
    again = _transition(client, csrf, order["id"], "mark-ordered")
    assert again.status_code == 200
    assert again.json()["status"] == "ordered"


# --- Editing drafts --------------------------------------------------------------

def test_draft_can_be_edited(staff, ids):
    client, csrf = staff
    suppliers, ingredients = ids
    order = _create(client, csrf, suppliers["Masumoto Provisions"],
                    _nori_order_payload(ingredients["Nori"], "10")).json()
    updated = client.patch(
        f"/api/v1/purchase-orders/{order['id']}",
        json={"expected_delivery_date": FUTURE,
              "items": _nori_order_payload(ingredients["Eggs"], "5"),
              "notes": "Switched to eggs"},
        headers={"X-CSRF-Token": csrf},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["notes"] == "Switched to eggs"
    assert body["items"][0]["ingredient_id"] == ingredients["Eggs"]
    assert body["items"][0]["quantity_ordered"] == "5.000"


def test_non_draft_cannot_be_edited(staff, ids):
    client, csrf = staff
    suppliers, ingredients = ids
    order = _create(client, csrf, suppliers["Masumoto Provisions"],
                    _nori_order_payload(ingredients["Nori"])).json()
    _transition(client, csrf, order["id"], "mark-ordered")
    response = client.patch(
        f"/api/v1/purchase-orders/{order['id']}",
        json={"notes": "too late"}, headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "order_not_draft"


# --- Validation --------------------------------------------------------------------

def test_duplicate_ingredient_lines_rejected(staff, ids):
    client, csrf = staff
    suppliers, ingredients = ids
    nori = ingredients["Nori"]
    response = _create(client, csrf, suppliers["Masumoto Provisions"], [
        {"ingredient_id": nori, "quantity_ordered": "10"},
        {"ingredient_id": nori, "quantity_ordered": "5"},
    ])
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "duplicate_item"


def test_ingredient_must_be_supplied_by_the_supplier(staff, ids):
    """Chashu is a Masumoto item; Tokyo Noodle cannot order it."""
    client, csrf = staff
    suppliers, ingredients = ids
    response = _create(client, csrf, suppliers["Tokyo Noodle Co."],
                       _nori_order_payload(ingredients["Chashu"]))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "ingredient_not_supplied"


def test_order_for_archived_supplier_rejected(admin, ids):
    client, csrf = admin
    suppliers, ingredients = ids
    created = client.post("/api/v1/suppliers", json={"name": "Gone Supplier"},
                          headers={"X-CSRF-Token": csrf}).json()
    client.post(f"/api/v1/suppliers/{created['id']}/archive",
                headers={"X-CSRF-Token": csrf})
    response = _create(client, csrf, created["id"],
                       _nori_order_payload(ingredients["Nori"]))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "supplier_inactive"


def test_invalid_payloads_rejected(staff, ids):
    client, csrf = staff
    suppliers, ingredients = ids
    assert _create(client, csrf, suppliers["Masumoto Provisions"],
                   []).status_code == 422  # no items
    assert _create(client, csrf, suppliers["Masumoto Provisions"],
                   _nori_order_payload(ingredients["Nori"], "0")).status_code == 422
    assert _create(client, csrf, suppliers["Masumoto Provisions"],
                   _nori_order_payload(ingredients["Nori"], "-5")).status_code == 422
    assert _create(client, csrf, 9999,
                   _nori_order_payload(ingredients["Nori"])).status_code == 404


# --- RBAC ---------------------------------------------------------------------------

def test_staff_cannot_cancel_orders(staff, ids):
    client, csrf = staff
    suppliers, ingredients = ids
    order = _create(client, csrf, suppliers["Masumoto Provisions"],
                    _nori_order_payload(ingredients["Nori"])).json()
    assert _transition(client, csrf, order["id"], "cancel").status_code == 403


def test_unauthenticated_access_rejected(seeded_client, ids):
    client = TestClient(seeded_client.app)
    assert client.get("/api/v1/purchase-orders").status_code == 401
    assert client.post("/api/v1/purchase-orders", json={}).status_code == 401


def test_mutations_require_csrf(staff, ids):
    client, _ = staff  # logged in, no CSRF header
    suppliers, ingredients = ids
    response = client.post("/api/v1/purchase-orders", json={
        "supplier_id": suppliers["Masumoto Provisions"],
        "expected_delivery_date": FUTURE,
        "items": _nori_order_payload(ingredients["Nori"]),
    })
    assert response.status_code == 403


# --- Archive guard --------------------------------------------------------------------

def test_supplier_with_open_order_cannot_be_archived(staff, admin, ids):
    staff_client, staff_csrf = staff
    admin_client, admin_csrf = admin
    suppliers, ingredients = ids
    # A supplier that is NOT the primary of any active ingredient, so the
    # order guard (not the ingredient guard) is what blocks archiving.
    supplier = admin_client.post(
        "/api/v1/suppliers", json={"name": "Order Block Foods"},
        headers={"X-CSRF-Token": admin_csrf},
    ).json()
    linked = admin_client.patch(
        f"/api/v1/ingredients/{ingredients['Nori']}",
        json={"alternative_supplier_ids": [supplier["id"]]},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert linked.status_code == 200

    order = _create(staff_client, staff_csrf, supplier["id"],
                    _nori_order_payload(ingredients["Nori"])).json()
    _transition(staff_client, staff_csrf, order["id"], "mark-ordered")
    blocked = admin_client.post(
        f"/api/v1/suppliers/{supplier['id']}/archive",
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "archive_blocked"
    assert "purchase orders" in blocked.json()["error"]["message"]
    # Cancelling the order unblocks archiving.
    _transition(admin_client, admin_csrf, order["id"], "cancel")
    unblocked = admin_client.post(
        f"/api/v1/suppliers/{supplier['id']}/archive",
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert unblocked.status_code == 200


# --- Audit -----------------------------------------------------------------------------

def test_order_lifecycle_is_audited(staff, admin, ids, seeded_client):
    staff_client, staff_csrf = staff
    admin_client, admin_csrf = admin
    suppliers, ingredients = ids
    order = _create(staff_client, staff_csrf, suppliers["Masumoto Provisions"],
                    _nori_order_payload(ingredients["Nori"])).json()
    _transition(staff_client, staff_csrf, order["id"], "mark-ordered")
    _transition(staff_client, staff_csrf, order["id"], "confirm")
    _transition(admin_client, admin_csrf, order["id"], "cancel")

    db = seeded_client.app.state.session_factory()
    try:
        actions = db.scalars(
            select(AuditLog.action).where(
                AuditLog.entity_type == "purchase_order",
                AuditLog.entity_id == order["id"],
            ).order_by(AuditLog.id)
        ).all()
    finally:
        db.close()
    assert actions == ["order.create", "order.mark_ordered", "order.confirm",
                       "order.cancel"]


# --- Listing ------------------------------------------------------------------------------

def test_list_filters_by_status_and_supplier(staff, ids):
    client, csrf = staff
    suppliers, ingredients = ids
    order = _create(client, csrf, suppliers["Masumoto Provisions"],
                    _nori_order_payload(ingredients["Nori"])).json()
    _transition(client, csrf, order["id"], "mark-ordered")

    ordered = client.get("/api/v1/purchase-orders?status=ordered").json()
    assert any(o["id"] == order["id"] for o in ordered)
    drafts = client.get("/api/v1/purchase-orders?status=draft").json()
    assert all(o["status"] == "draft" for o in drafts)
    multi = client.get("/api/v1/purchase-orders?status=ordered,confirmed").json()
    assert {o["status"] for o in multi} <= {"ordered", "confirmed"}
    by_supplier = client.get(
        f"/api/v1/purchase-orders?supplier_id={suppliers['Masumoto Provisions']}"
    ).json()
    assert all(o["supplier_id"] == suppliers["Masumoto Provisions"] for o in by_supplier)
    detail = client.get(f"/api/v1/purchase-orders/{order['id']}").json()
    assert detail["supplier_name"] == "Masumoto Provisions"
    assert detail["items"][0]["pending_quantity"] == detail["items"][0]["quantity_ordered"]
