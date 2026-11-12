"""Phase 4 Step 1: migrated, isolated notification lifecycle and business rules."""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, inspect, select, text, update

from app.models import (Base, Ingredient, IngredientDemandSetting, IngredientStockState, InventoryTransaction,
                        NotificationEvent, NotificationUserState, PurchaseOrder, PurchaseOrderItem,
                        SupplierCalendarException, SupplierScheduleRule, User)
from app.models.demand import WEEKDAYS
from app.seed import DEMO_ADMIN_PASSWORD, DEMO_STAFF_PASSWORD
from app.services import notifications
from app.services.inventory import record_stock_entry
from app.services.ordering import confirm_order
from app.services.recommendations import generate_recommendations
from app.schemas.receiving import ReceiptLineIn
from app.services.receiving import receive_delivery

WEDNESDAY_REMINDER = datetime(2026, 11, 18, 12, 30, tzinfo=timezone.utc)  # 21:30 JST
FRIDAY = date(2026, 11, 20)


@pytest.fixture()
def setup(seeded_client, settings):
    with seeded_client.app.state.session_factory() as db:
        db.execute(update(PurchaseOrder).values(status="cancelled"))
        ingredient = db.scalar(select(Ingredient).where(Ingredient.name == "Korean Noodles"))
        db.add(IngredientDemandSetting(ingredient_id=ingredient.id, enabled=True, safety_stock=Decimal("60"),
               **{day: Decimal("40") if index < 5 else Decimal("60") for index, day in enumerate(WEEKDAYS)}))
        state = db.get(IngredientStockState, ingredient.id)
        state.last_counted_at = WEDNESDAY_REMINDER - timedelta(hours=1)
        state.last_transaction_at = WEDNESDAY_REMINDER - timedelta(hours=1)
        db.commit()
        return seeded_client, settings, ingredient.id, ingredient.primary_supplier_id


def evaluate(setup, now=WEDNESDAY_REMINDER):
    client, settings, *_ = setup
    with client.app.state.session_factory() as db:
        return notifications.evaluate(db, settings=settings, now_utc=now)


def events(setup, kind=None):
    with setup[0].app.state.session_factory() as db:
        stmt = select(NotificationEvent)
        if kind:
            stmt = stmt.where(NotificationEvent.type == kind)
        return db.scalars(stmt.order_by(NotificationEvent.id)).all()


def configure(setup, **changes):
    with setup[0].app.state.session_factory() as db:
        return notifications.update_settings(db, changes)


def login(client, role="admin"):
    result = client.post("/api/v1/auth/login", json={"username": role,
        "password": DEMO_ADMIN_PASSWORD if role == "admin" else DEMO_STAFF_PASSWORD})
    assert result.status_code == 200
    return {"X-CSRF-Token": result.json()["csrfToken"]}


def test_notification_migration_preserves_operational_data(setup):
    client = setup[0]
    with client.app.state.db_engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0009_notification_center"
        tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        assert {"notification_events", "notification_user_states", "notification_settings", "notification_count_rules"} <= tables
        assert conn.execute(text("PRAGMA foreign_key_check")).all() == []
        assert conn.execute(text("PRAGMA integrity_check")).scalar() == "ok"
        inspector = inspect(conn)
        for name in ("notification_events", "notification_user_states", "notification_settings", "notification_count_rules"):
            assert {column.name for column in Base.metadata.tables[name].columns} == {column["name"] for column in inspector.get_columns(name)}


def test_daily_count_is_aggregated_idempotent_and_resolves_only_after_physical_counts(setup, monkeypatch):
    client, settings, iid, _ = setup
    chinese = next(row.id for row in _ingredients(client) if row.name == "Chinese Noodles")
    configure(setup, daily_count_ingredient_ids=[iid, chinese])
    evaluate(setup, WEDNESDAY_REMINDER - timedelta(seconds=1))
    assert not events(setup, "daily_count")
    first = evaluate(setup)
    assert first["created"] >= 1
    reminders = events(setup, "daily_count")
    assert len(reminders) == 1 and "Chinese Noodles" in reminders[0].message and "Korean Noodles" in reminders[0].message
    assert evaluate(setup)["created"] == 0
    monkeypatch.setattr("app.services.inventory._now", lambda: WEDNESDAY_REMINDER)
    with client.app.state.session_factory() as db:
        record_stock_entry(db, ingredient_id=iid, user=db.get(User, 1), mode="remaining", quantity=Decimal("180"),
            reason="daily_count", idempotency_key="notice-count-korean", confirmed=True, allow_negative=False, settings=settings)
    evaluate(setup)
    assert "Korean Noodles" not in events(setup, "daily_count")[0].message
    with client.app.state.session_factory() as db:
        record_stock_entry(db, ingredient_id=chinese, user=db.get(User, 1), mode="remaining", quantity=Decimal("0"),
            reason="daily_count", idempotency_key="notice-count-chinese", confirmed=True, allow_negative=False, settings=settings)
    evaluate(setup)
    assert events(setup, "daily_count")[0].status == "resolved"
    login(client)
    reminder_id = events(setup, "daily_count")[0].id
    assert reminder_id in {row["id"] for row in client.get("/api/v1/notifications?view=all&limit=100").json()["items"]}
    assert reminder_id in {row["id"] for row in client.get("/api/v1/notifications?view=history").json()["items"]}
    assert reminder_id not in {row["id"] for row in client.get("/api/v1/notifications").json()["items"]}


def _ingredients(client):
    with client.app.state.session_factory() as db:
        return list(db.scalars(select(Ingredient)).all())


def test_daily_time_can_be_changed_and_used_stock_does_not_count(setup, monkeypatch):
    client, settings, iid, _ = setup
    configure(setup, daily_count_ingredient_ids=[iid], daily_count_time=time(22, 0),
              order_cutoff_enabled=False, supplier_contact_enabled=False,
              forecast_shortage_enabled=False, verify_pending_order_enabled=False,
              pending_delivery_enabled=False)
    monkeypatch.setattr("app.services.inventory._now", lambda: WEDNESDAY_REMINDER)
    with client.app.state.session_factory() as db:
        record_stock_entry(db, ingredient_id=iid, user=db.get(User, 1), mode="used", quantity=Decimal("1"),
            reason=None, idempotency_key="notice-used", confirmed=True, allow_negative=False, settings=settings)
    assert evaluate(setup)["created"] == 0
    assert evaluate(setup, WEDNESDAY_REMINDER + timedelta(minutes=30))["created"] == 1
    assert events(setup, "daily_count")[0].status == "active"


def test_supplier_cutoff_uses_cycle_reminder_and_purchase_rounding(setup):
    _, _, iid, sid = setup
    before = evaluate(setup, WEDNESDAY_REMINDER - timedelta(seconds=1))
    assert not events(setup, "order_cutoff")
    evaluate(setup)
    notice = events(setup, "order_cutoff")[0]
    assert notice.supplier_id == sid and notice.business_date == FRIDAY
    assert "2 Box" in notice.message and "120.000 pc" in notice.message
    assert "2026-11-19 14:30" in notice.message
    assert evaluate(setup)["created"] == 0
    assert len(events(setup, "order_cutoff")) == 1
    evaluate(setup, datetime(2026, 11, 19, 5, 30, 1, tzinfo=timezone.utc))
    assert events(setup, "order_cutoff")[0].status == "expired"


def test_confirmed_order_removes_extra_purchase_notice(setup):
    client, _, iid, sid = setup
    evaluate(setup)
    with client.app.state.session_factory() as db:
        po = PurchaseOrder(supplier_id=sid, status="ordered", expected_delivery_date=FRIDAY, created_by=1, ordered_by=1)
        po.items = [PurchaseOrderItem(ingredient_id=iid, quantity_ordered=Decimal("120"), quantity_received=Decimal("0"))]
        db.add(po); db.commit()
        confirm_order(db, user=db.get(User, 1), order_id=po.id)
    with client.app.state.session_factory() as db:
        row = next(r for r in generate_recommendations(db, settings=setup[1], now_utc=WEDNESDAY_REMINDER)["recommendations"] if r["ingredient_id"] == iid)
        assert row["conservative"]["required_additional_quantity"] == 0, row["existing_orders"]
    evaluate(setup)
    assert events(setup, "order_cutoff")[0].status == "resolved", [(e.status, e.message) for e in events(setup, "order_cutoff")]


def test_partly_covering_confirmed_order_reduces_additional_reminder(setup):
    client, _, iid, sid = setup
    with client.app.state.session_factory() as db:
        po = PurchaseOrder(supplier_id=sid, status="ordered", expected_delivery_date=FRIDAY, created_by=1, ordered_by=1)
        po.items = [PurchaseOrderItem(ingredient_id=iid, quantity_ordered=Decimal("60"), quantity_received=Decimal("0"))]
        db.add(po); db.commit()
        confirm_order(db, user=db.get(User, 1), order_id=po.id)
    evaluate(setup)
    notice = next(e for e in events(setup, "order_cutoff") if e.business_date == FRIDAY)
    assert "additional 1 Box / 60.000 pc" in notice.message
    assert not events(setup, "verify_pending_order")


def test_unconfirmed_order_warns_without_unconditional_duplicate_purchase(setup):
    client, _, iid, sid = setup
    with client.app.state.session_factory() as db:
        po = PurchaseOrder(supplier_id=sid, status="ordered", expected_delivery_date=FRIDAY, created_by=1, ordered_by=1)
        po.items = [PurchaseOrderItem(ingredient_id=iid, quantity_ordered=Decimal("120"), quantity_received=Decimal("0"))]
        db.add(po); db.commit(); oid = po.id
    evaluate(setup)
    notice = events(setup, "verify_pending_order")[0]
    assert notice.purchase_order_id == oid and notice.navigation_target == f"#/orders?order_id={oid}"
    assert "uncertain incoming: 120.000 pc" in notice.message
    assert "verify pending order before buying" in events(setup, "order_cutoff")[0].message


def test_shortage_is_critical_and_does_not_invent_regular_order_quantity(setup, monkeypatch):
    client, settings, iid, _ = setup
    with client.app.state.session_factory() as db:
        db.get(IngredientDemandSetting, iid).wednesday = Decimal("80")
        db.commit()
    monkeypatch.setattr("app.services.inventory._now", lambda: WEDNESDAY_REMINDER)
    with client.app.state.session_factory() as db:
        record_stock_entry(db, ingredient_id=iid, user=db.get(User, 1), mode="remaining", quantity=Decimal("30"),
            reason="daily_count", idempotency_key="notice-shortage-count", confirmed=True, allow_negative=False, settings=settings)
    evaluate(setup)
    alert = next(e for e in events(setup, "forecast_shortage") if e.ingredient_id == iid)
    assert alert.priority == "critical" and "50.000 pc" in alert.message
    assert "Count confidence:" in alert.message
    assert "A regular future order cannot repair an earlier shortage" in alert.message
    assert "No reliable regular-order quantity" in events(setup, "order_cutoff")[0].message


def test_shortage_notice_labels_stale_count_confidence(setup):
    client, _, iid, _ = setup
    with client.app.state.session_factory() as db:
        db.get(IngredientStockState, iid).last_counted_at = WEDNESDAY_REMINDER - timedelta(days=5)
        db.commit()
    evaluate(setup)
    notice = next(e for e in events(setup, "forecast_shortage") if e.ingredient_id == iid)
    assert "Count confidence: low (stale)" in notice.message
    assert "Verify a fresh physical count" in notice.message


def test_api_reads_are_read_only_and_read_ack_do_not_resolve(setup):
    client = setup[0]
    configure(setup, daily_count_ingredient_ids=[setup[2]])
    evaluate(setup)
    headers = login(client, "staff")
    before = len(events(setup))
    page = client.get("/api/v1/notifications?view=unread&limit=1&offset=0")
    assert page.status_code == 200 and page.json()["total"] >= 1 and len(page.json()["items"]) == 1
    assert len(events(setup)) == before
    notification_id = next(e.id for e in events(setup, "daily_count"))
    assert client.post(f"/api/v1/notifications/{notification_id}/read").status_code == 403
    assert client.post(f"/api/v1/notifications/{notification_id}/read", headers=headers).status_code == 200
    assert client.post(f"/api/v1/notifications/{notification_id}/acknowledge", headers=headers).status_code == 200
    with client.app.state.session_factory() as db:
        assert db.get(NotificationEvent, notification_id).status == "active"
        state = db.scalar(select(NotificationUserState).where(NotificationUserState.notification_id == notification_id,
                                                             NotificationUserState.user_id == 2))
        assert state.read_at and state.acknowledged_at
    admin_headers = login(client, "admin")
    assert client.get("/api/v1/notifications/unread-count").json()["unread_count"] >= 1
    assert client.post("/api/v1/notifications/mark-all-read", headers=admin_headers).status_code == 200
    assert client.get("/api/v1/notifications/unread-count").json()["unread_count"] == 0


def test_settings_admin_only_and_evaluate_requires_csrf(setup):
    client = setup[0]
    assert client.get("/api/v1/notifications").status_code == 401
    staff = login(client, "staff")
    assert client.patch("/api/v1/notifications/settings", json={"daily_count_enabled": False}, headers=staff).status_code == 403
    assert client.post("/api/v1/notifications/evaluate").status_code == 403
    assert client.post("/api/v1/notifications/evaluate", headers=staff).status_code == 200
    admin = login(client, "admin")
    assert client.patch("/api/v1/notifications/settings", json={"daily_count_enabled": False}, headers=admin).status_code == 200
    assert client.get("/api/v1/notifications/settings").json()["daily_count_enabled"] is False


def test_evaluation_does_not_touch_operational_tables(setup):
    client = setup[0]
    names = ("ingredient_stock_state", "inventory_transactions", "purchase_orders", "purchase_order_items", "delivery_receipts", "delivery_receipt_items")
    def snapshot():
        with client.app.state.db_engine.connect() as conn:
            return {name: conn.execute(text(f'SELECT * FROM "{name}" ORDER BY rowid')).all() for name in names}
    before = snapshot()
    evaluate(setup)
    assert snapshot() == before
    with client.app.state.db_engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_key_check")).all() == []
        assert conn.execute(text("PRAGMA quick_check")).scalar() == "ok"
        drift = conn.execute(text("SELECT s.ingredient_id FROM ingredient_stock_state s LEFT JOIN "
            "(SELECT ingredient_id, SUM(quantity_delta) AS balance FROM inventory_transactions GROUP BY ingredient_id) t "
            "ON t.ingredient_id=s.ingredient_id WHERE s.current_quantity <> COALESCE(t.balance, 0)")).all()
        assert drift == []


def test_contact_reminder_uses_required_rule_deadline_and_does_not_place_order(setup):
    client, _, iid, sid = setup
    instant = datetime(2026, 11, 24, 1, 30, tzinfo=timezone.utc)  # Tuesday 10:30 JST
    evaluate(setup, instant)
    contact = next(e for e in events(setup, "supplier_contact") if e.business_date == date(2026, 11, 25))
    assert contact.supplier_id == sid and "Tuesday before 11:00" in contact.message
    assert "2026-11-24 11:00" in contact.message and contact.navigation_target == f"#/suppliers?supplier_id={sid}"
    with client.app.state.session_factory() as db:
        assert all(order.status == "cancelled" for order in db.scalars(select(PurchaseOrder)))
        row = db.get(NotificationEvent, contact.id)
        assert row.status == "active"
        notifications.acknowledge(db, user_id=1, notification_id=contact.id)
        assert db.get(NotificationEvent, contact.id).status == "active"


def test_holiday_exception_prevents_actionable_order_cutoff(setup):
    client, _, _, sid = setup
    with client.app.state.session_factory() as db:
        db.add(SupplierCalendarException(supplier_id=sid, date=FRIDAY,
                                         type="delivery_cancelled", is_active=True))
        db.commit()
    evaluate(setup)
    assert not any(e.supplier_id == sid and e.business_date == FRIDAY for e in events(setup, "order_cutoff"))


def test_new_holiday_supersedes_existing_order_reminder(setup):
    client, _, _, sid = setup
    evaluate(setup)
    notice = next(e for e in events(setup, "order_cutoff") if e.business_date == FRIDAY)
    with client.app.state.session_factory() as db:
        db.add(SupplierCalendarException(supplier_id=sid, date=FRIDAY,
                                         type="delivery_cancelled", is_active=True))
        db.commit()
    evaluate(setup)
    updated = next(e for e in events(setup, "order_cutoff") if e.id == notice.id)
    assert updated.status == "superseded" and updated.closed_at and updated.resolved_at is None


def test_cancelled_contact_cycle_is_superseded_not_falsely_resolved(setup):
    client, _, _, sid = setup
    instant = datetime(2026, 11, 24, 1, 30, tzinfo=timezone.utc)
    evaluate(setup, instant)
    notice = next(e for e in events(setup, "supplier_contact") if e.business_date == date(2026, 11, 25))
    with client.app.state.session_factory() as db:
        db.add(SupplierCalendarException(supplier_id=sid, date=date(2026, 11, 25),
                                         type="delivery_cancelled", is_active=True))
        db.commit()
    evaluate(setup, instant)
    updated = next(e for e in events(setup, "supplier_contact") if e.id == notice.id)
    assert updated.status == "superseded" and updated.resolved_at is None


def test_supplier_rule_times_and_cycle_identities_remain_independent(setup):
    client, _, _, _ = setup
    with client.app.state.session_factory() as db:
        chinese = db.scalar(select(Ingredient).where(Ingredient.name == "Chinese Noodles"))
        db.add(IngredientDemandSetting(ingredient_id=chinese.id, enabled=True, safety_stock=Decimal("20"),
            **{day: Decimal("40") for day in WEEKDAYS}))
        dragon_rule = db.scalar(select(SupplierScheduleRule).where(
            SupplierScheduleRule.supplier_id == chinese.primary_supplier_id,
            SupplierScheduleRule.delivery_weekday == 1))
        dragon_rule.reminder_days_before = 6
        dragon_rule.reminder_time = time(21, 30)
        db.commit()
    evaluate(setup)
    reminders = events(setup, "order_cutoff")
    assert len({e.supplier_id for e in reminders}) >= 2
    assert len({e.delivery_cycle_key for e in reminders}) == len(reminders)


def test_pre_delivery_shortage_alert_survives_positive_closing_balance(setup, monkeypatch):
    client, settings, iid, sid = setup
    friday_morning = datetime(2026, 11, 20, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.inventory._now", lambda: friday_morning)
    with client.app.state.session_factory() as db:
        record_stock_entry(db, ingredient_id=iid, user=db.get(User, 1), mode="remaining", quantity=Decimal("20"),
            reason="daily_count", idempotency_key="notice-predelivery-count", confirmed=True, allow_negative=False, settings=settings)
        order = PurchaseOrder(supplier_id=sid, status="ordered", expected_delivery_date=FRIDAY, created_by=1, ordered_by=1)
        order.items = [PurchaseOrderItem(ingredient_id=iid, quantity_ordered=Decimal("120"), quantity_received=Decimal("0"))]
        db.add(order); db.commit()
        confirm_order(db, user=db.get(User, 1), order_id=order.id)
    evaluate(setup, friday_morning)
    alert = next(e for e in events(setup, "forecast_shortage") if e.ingredient_id == iid)
    assert "pre-delivery shortage risk of 20.000 pc" in alert.message
    assert "arrival time is unknown" in alert.message


def test_missing_demand_does_not_invent_risk_or_resolve_prior_unknown(setup):
    client, _, iid, _ = setup
    evaluate(setup)
    existing = next(e for e in events(setup, "forecast_shortage") if e.ingredient_id == iid)
    with client.app.state.session_factory() as db:
        db.get(IngredientDemandSetting, iid).enabled = False
        db.commit()
    evaluate(setup)
    assert next(e for e in events(setup, "forecast_shortage") if e.id == existing.id).status == "active"


def test_partial_receipt_rejected_goods_remain_outstanding_then_resolve(setup):
    client, settings, iid, sid = setup
    with client.app.state.session_factory() as db:
        po = PurchaseOrder(supplier_id=sid, status="ordered", expected_delivery_date=WEDNESDAY_REMINDER.date(), created_by=1, ordered_by=1)
        po.items = [PurchaseOrderItem(ingredient_id=iid, quantity_ordered=Decimal("100"), quantity_received=Decimal("0"))]
        db.add(po); db.commit(); oid = po.id
        receive_delivery(db, user=db.get(User, 1), order_id=oid,
            lines=[ReceiptLineIn(ingredient_id=iid, quantity=Decimal("80"), quantity_rejected=Decimal("5"))],
            receipt_date=date(2026, 11, 18), notes=None, idempotency_key="notice-partial-receipt",
            received_by_staff_name="Test Receiver", settings=settings)
    evaluate(setup)
    event = next(e for e in events(setup, "pending_delivery") if e.purchase_order_id == oid)
    assert "20.000 pc" in event.message and "80.000 pc" not in event.message
    with client.app.state.session_factory() as db:
        receive_delivery(db, user=db.get(User, 1), order_id=oid,
            lines=[ReceiptLineIn(ingredient_id=iid, quantity=Decimal("20"))],
            receipt_date=date(2026, 11, 18), notes=None, idempotency_key="notice-final-receipt",
            received_by_staff_name="Test Receiver", settings=settings)
    evaluate(setup)
    assert next(e for e in events(setup, "pending_delivery") if e.id == event.id).status == "resolved"


def test_delayed_delivery_preserves_expected_date_and_is_not_moved_to_today(setup):
    client, _, iid, sid = setup
    old_date = date(2026, 11, 17)
    with client.app.state.session_factory() as db:
        po = PurchaseOrder(supplier_id=sid, status="delayed", expected_delivery_date=old_date, created_by=1, ordered_by=1)
        po.items = [PurchaseOrderItem(ingredient_id=iid, quantity_ordered=Decimal("20"), quantity_received=Decimal("0"))]
        db.add(po); db.commit(); oid = po.id
    evaluate(setup)
    event = next(e for e in events(setup, "pending_delivery") if e.purchase_order_id == oid)
    assert event.priority == "high" and event.business_date == old_date
    assert str(old_date) in event.message and "No new arrival date is assumed" in event.message


def test_repeated_and_concurrent_evaluation_share_one_event_identity(setup):
    configure(setup, daily_count_ingredient_ids=[setup[2]])
    first = evaluate(setup)
    assert first["created"] >= 1
    def run(_):
        return evaluate(setup)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(run, range(3)))
    assert all(r["created"] == 0 for r in results)
    keys = [row.dedupe_key for row in events(setup)]
    assert len(keys) == len(set(keys))
    with setup[0].app.state.db_engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_key_check")).all() == []


def test_pagination_filters_and_shared_account_read_state(setup):
    client = setup[0]
    configure(setup, daily_count_ingredient_ids=[setup[2]])
    evaluate(setup)
    headers = login(client, "staff")
    all_items = client.get("/api/v1/notifications?limit=1").json()
    assert all_items["total"] >= 2 and len(all_items["items"]) == 1
    assert client.get("/api/v1/notifications?limit=1&offset=1").json()["items"][0]["id"] != all_items["items"][0]["id"]
    assert client.get("/api/v1/notifications?view=urgent").status_code == 200
    assert client.get("/api/v1/notifications?view=history").json()["total"] == 0
    assert client.get("/api/v1/notifications?limit=101").status_code == 422
    nid = all_items["items"][0]["id"]
    assert client.post(f"/api/v1/notifications/{nid}/read", headers=headers).status_code == 200
    # A second session of the same shared account sees the same read state.
    assert next(row for row in client.get("/api/v1/notifications").json()["items"] if row["id"] == nid)["read_at"]
