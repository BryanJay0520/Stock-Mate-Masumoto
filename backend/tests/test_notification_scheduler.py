"""Deterministic dedicated-worker, lease, recovery, and authorization tests."""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import inspect, select, text, update

from app.models import (Ingredient, IngredientDemandSetting, IngredientStockState,
                        NotificationEvent, NotificationSetting, NotificationWorkerState,
                        PurchaseOrder, PurchaseOrderItem, SupplierCalendarException, User)
from app import notification_worker
from app.notification_worker import run_loop
from app.reset_demo import verify_demo_dataset
from app.seed import DEMO_ADMIN_PASSWORD, DEMO_STAFF_PASSWORD
from app.services import notification_scheduler, notifications
from app.services.inventory import record_stock_entry
from app.services.confidence import as_utc
from app.services.ordering import confirm_order
from app.schemas.receiving import ReceiptLineIn
from app.services.receiving import receive_delivery
from app.models.demand import WEEKDAYS

REMINDER = datetime(2026, 11, 18, 12, 30, tzinfo=timezone.utc)  # 21:30 JST


def _login(client, role="admin"):
    password = DEMO_ADMIN_PASSWORD if role == "admin" else DEMO_STAFF_PASSWORD
    response = client.post("/api/v1/auth/login", json={"username": role, "password": password})
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrfToken"]}


def _configure(client, *, count=True, **options):
    with client.app.state.session_factory() as db:
        korean = db.scalar(select(Ingredient).where(Ingredient.name == "Korean Noodles"))
        changes = {"automatic_evaluation_enabled": True, "evaluation_interval_seconds": 60,
                   "daily_count_ingredient_ids": [korean.id] if count else [], **options}
        notifications.update_settings(db, changes)
        return korean.id


def _events(client, kind=None):
    with client.app.state.session_factory() as db:
        query = select(NotificationEvent)
        if kind:
            query = query.where(NotificationEvent.type == kind)
        return list(db.scalars(query.order_by(NotificationEvent.id)))


def _tick(client, settings, when, owner="worker-one"):
    return notification_scheduler.tick(client.app.state.session_factory, settings=settings,
                                       owner_token=owner, clock=lambda: when)


def _planning(client):
    """Deterministic Korean Noodles ordering cycle from the existing seed."""
    with client.app.state.session_factory() as db:
        db.execute(update(PurchaseOrder).values(status="cancelled"))
        korean = db.scalar(select(Ingredient).where(Ingredient.name == "Korean Noodles"))
        db.add(IngredientDemandSetting(ingredient_id=korean.id, enabled=True,
            safety_stock=Decimal("60"),
            **{day: Decimal("40") if index < 5 else Decimal("60")
               for index, day in enumerate(WEEKDAYS)}))
        state = db.get(IngredientStockState, korean.id)
        state.last_counted_at = REMINDER - timedelta(hours=1)
        state.last_transaction_at = REMINDER - timedelta(hours=1)
        db.commit()
        return korean.id, korean.primary_supplier_id


def test_additive_migration_matches_scheduler_models(client):
    with client.app.state.db_engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0010_notification_scheduler"
        columns = {c["name"] for c in inspect(conn).get_columns("notification_settings")}
        assert {"automatic_evaluation_enabled", "evaluation_interval_seconds"} <= columns
        assert {c["name"] for c in inspect(conn).get_columns("notification_worker_state")} == {
            c.name for c in NotificationWorkerState.__table__.columns}
        assert conn.execute(text("PRAGMA foreign_key_check")).all() == []


def test_worker_is_disabled_by_default_and_web_startup_does_not_evaluate(seeded_client, settings):
    client = seeded_client
    assert not _events(client)
    result = _tick(client, settings, REMINDER)
    assert result["status"] == "disabled" and not _events(client)
    with client.app.state.session_factory() as db:
        assert notification_scheduler.health(db, now_utc=REMINDER)["status"] == "disabled"


def test_admin_settings_validation_auth_csrf_and_health_scope(seeded_client):
    client = seeded_client
    assert client.get("/api/v1/notifications/scheduler-health").status_code == 401
    staff = _login(client, "staff")
    assert client.get("/api/v1/notifications/scheduler-health").status_code == 403
    assert client.patch("/api/v1/notifications/settings", headers=staff,
                        json={"automatic_evaluation_enabled": True}).status_code == 403
    admin = _login(client)
    assert client.patch("/api/v1/notifications/settings",
                        json={"automatic_evaluation_enabled": True}).status_code == 403
    for interval in (59, 901, 0, -1):
        assert client.patch("/api/v1/notifications/settings", headers=admin,
                            json={"evaluation_interval_seconds": interval}).status_code == 422
    assert client.patch("/api/v1/notifications/settings", headers=admin,
                        json={"automatic_evaluation_enabled": True,
                              "evaluation_interval_seconds": 300}).status_code == 200
    health = client.get("/api/v1/notifications/scheduler-health").json()
    assert health["status"] == "unknown" and health["evaluation_interval_seconds"] == 300


def test_daily_exact_boundary_recovery_resolution_and_no_duplicates(seeded_client, settings, monkeypatch):
    client = seeded_client
    iid = _configure(client)
    assert _tick(client, settings, REMINDER - timedelta(minutes=1))["status"] == "evaluated"
    assert not _events(client, "daily_count")
    assert _tick(client, settings, REMINDER)["status"] == "evaluated"
    reminder = _events(client, "daily_count")
    assert len(reminder) == 1 and "Korean Noodles" in reminder[0].message
    assert _tick(client, settings, REMINDER)["status"] == "idle"
    assert len(_events(client, "daily_count")) == 1
    monkeypatch.setattr("app.services.inventory._now", lambda: REMINDER)
    with client.app.state.session_factory() as db:
        record_stock_entry(db, ingredient_id=iid, user=db.get(User, 1), mode="remaining",
            quantity=Decimal("180"), reason="daily_count", idempotency_key="scheduler-physical-count",
            confirmed=True, allow_negative=False, settings=settings)
    assert _tick(client, settings, REMINDER + timedelta(minutes=1))["status"] == "evaluated"
    assert _events(client, "daily_count")[0].status == "resolved"


def test_five_minute_interval_and_disable_are_enforced(seeded_client, settings):
    client = seeded_client
    _configure(client, evaluation_interval_seconds=300)
    assert _tick(client, settings, REMINDER)["status"] == "evaluated"
    assert _tick(client, settings, REMINDER + timedelta(minutes=4))["status"] == "idle"
    assert _tick(client, settings, REMINDER + timedelta(minutes=5))["status"] == "evaluated"
    with client.app.state.session_factory() as db:
        notifications.update_settings(db, {"automatic_evaluation_enabled": False})
    assert _tick(client, settings, REMINDER + timedelta(minutes=10))["status"] == "disabled"
    with client.app.state.session_factory() as db:
        assert notification_scheduler.health(db, now_utc=REMINDER + timedelta(minutes=10))["status"] == "disabled"


def test_worker_reuses_supplier_cycle_cutoff_and_confirmed_coverage(seeded_client, settings):
    client = seeded_client
    iid, sid = _planning(client)
    _configure(client, count=False)
    assert _tick(client, settings, REMINDER)["status"] == "evaluated"
    notice = next(e for e in _events(client, "order_cutoff") if e.business_date == date(2026, 11, 20))
    assert notice.supplier_id == sid and "2 Box" in notice.message and "120.000 pc" in notice.message
    assert "2026-11-19 14:30" in notice.message
    with client.app.state.session_factory() as db:
        po = PurchaseOrder(supplier_id=sid, status="ordered", expected_delivery_date=date(2026, 11, 20),
                           created_by=1, ordered_by=1)
        po.items = [PurchaseOrderItem(ingredient_id=iid, quantity_ordered=Decimal("120"),
                                      quantity_received=Decimal("0"))]
        db.add(po); db.commit()
        confirm_order(db, user=db.get(User, 1), order_id=po.id)
    assert _tick(client, settings, REMINDER + timedelta(minutes=1))["status"] == "evaluated"
    assert next(e for e in _events(client, "order_cutoff") if e.id == notice.id).status == "resolved"


def test_worker_unconfirmed_order_warning_and_expired_cutoff(seeded_client, settings):
    client = seeded_client
    iid, sid = _planning(client)
    _configure(client, count=False)
    with client.app.state.session_factory() as db:
        po = PurchaseOrder(supplier_id=sid, status="ordered", expected_delivery_date=date(2026, 11, 20),
                           created_by=1, ordered_by=1)
        po.items = [PurchaseOrderItem(ingredient_id=iid, quantity_ordered=Decimal("120"),
                                      quantity_received=Decimal("0"))]
        db.add(po); db.commit()
    _tick(client, settings, REMINDER)
    assert _events(client, "verify_pending_order")
    notice = next(e for e in _events(client, "order_cutoff") if e.business_date == date(2026, 11, 20))
    assert "verify pending order before buying" in notice.message
    after_cutoff = datetime(2026, 11, 19, 5, 31, tzinfo=timezone.utc)
    _tick(client, settings, after_cutoff)
    assert next(e for e in _events(client, "order_cutoff") if e.id == notice.id).status == "expired"
    assert not any(e.status == "active" and e.business_date == date(2026, 11, 20)
                   for e in _events(client, "order_cutoff"))


def test_worker_holiday_supersedes_the_supplier_cycle(seeded_client, settings):
    client = seeded_client
    _, sid = _planning(client)
    _configure(client, count=False)
    _tick(client, settings, REMINDER)
    notice = next(e for e in _events(client, "order_cutoff") if e.business_date == date(2026, 11, 20))
    with client.app.state.session_factory() as db:
        db.add(SupplierCalendarException(supplier_id=sid, date=date(2026, 11, 20),
                                         type="delivery_cancelled", is_active=True))
        db.commit()
    _tick(client, settings, REMINDER + timedelta(minutes=1))
    assert next(e for e in _events(client, "order_cutoff") if e.id == notice.id).status == "superseded"


def test_worker_reuses_critical_shortage_and_required_contact(seeded_client, settings, monkeypatch):
    client = seeded_client
    iid, _ = _planning(client)
    _configure(client, count=False)
    with client.app.state.session_factory() as db:
        db.get(IngredientDemandSetting, iid).wednesday = Decimal("80")
        db.commit()
    monkeypatch.setattr("app.services.inventory._now", lambda: REMINDER)
    with client.app.state.session_factory() as db:
        record_stock_entry(db, ingredient_id=iid, user=db.get(User, 1), mode="remaining",
            quantity=Decimal("30"), reason="daily_count", idempotency_key="scheduler-shortage",
            confirmed=True, allow_negative=False, settings=settings)
    _tick(client, settings, REMINDER)
    alert = next(e for e in _events(client, "forecast_shortage") if e.ingredient_id == iid)
    assert alert.priority == "critical" and "50.000 pc" in alert.message
    assert "No reliable regular-order quantity" in _events(client, "order_cutoff")[0].message
    _tick(client, settings, datetime(2026, 11, 24, 1, 0, tzinfo=timezone.utc))
    assert any("2026-11-24 11:00" in e.message for e in _events(client, "supplier_contact"))


def test_worker_partial_receipt_keeps_only_accepted_quantity(seeded_client, settings):
    client = seeded_client
    iid, sid = _planning(client)
    _configure(client, count=False)
    with client.app.state.session_factory() as db:
        po = PurchaseOrder(supplier_id=sid, status="ordered", expected_delivery_date=date(2026, 11, 18),
                           created_by=1, ordered_by=1)
        po.items = [PurchaseOrderItem(ingredient_id=iid, quantity_ordered=Decimal("100"),
                                      quantity_received=Decimal("0"))]
        db.add(po); db.commit(); oid = po.id
        receive_delivery(db, user=db.get(User, 1), order_id=oid,
            lines=[ReceiptLineIn(ingredient_id=iid, quantity=Decimal("80"), quantity_rejected=Decimal("5"))],
            receipt_date=date(2026, 11, 18), notes=None, idempotency_key="scheduler-partial",
            received_by_staff_name="Test Receiver", settings=settings)
        stock_before = db.get(IngredientStockState, iid).current_quantity
    _tick(client, settings, REMINDER)
    notice = next(e for e in _events(client, "pending_delivery") if e.purchase_order_id == oid)
    assert "20.000 pc" in notice.message
    with client.app.state.session_factory() as db:
        assert db.get(IngredientStockState, iid).current_quantity == stock_before


def test_restart_after_reminder_catches_current_day_without_replaying_old_days(seeded_client, settings):
    client = seeded_client
    _configure(client)
    assert _tick(client, settings, REMINDER + timedelta(minutes=30))["status"] == "evaluated"
    assert len(_events(client, "daily_count")) == 1
    # A second process takes over after the first lease expires. The prior
    # business-day reminder is historical, and only today's condition is made.
    later = REMINDER + timedelta(days=2, minutes=30)
    assert _tick(client, settings, later, owner="worker-two")["status"] == "evaluated"
    counts = _events(client, "daily_count")
    assert len(counts) == 2 and counts[0].status == "expired" and counts[1].status == "active"
    assert counts[1].business_date == date(2026, 11, 20)


def test_two_workers_share_one_lease_and_stale_owner_is_fenced(seeded_client, settings):
    client = seeded_client
    _configure(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda owner: _tick(client, settings, REMINDER, owner),
                                ("worker-a", "worker-b")))
    assert sorted(row["status"] for row in results) == ["busy", "evaluated"]
    assert len(_events(client, "daily_count")) == 1
    with client.app.state.session_factory() as db:
        state = db.get(NotificationWorkerState, 1)
        original = state.owner_token
    takeover_time = REMINDER + timedelta(seconds=notification_scheduler.LEASE_SECONDS + 1)
    assert notification_scheduler.claim(client.app.state.session_factory,
        owner_token="replacement", now_utc=takeover_time) == "due"
    with client.app.state.session_factory() as db:
        with pytest.raises(notifications.WorkerLeaseLost):
            notifications.evaluate(db, settings=settings, now_utc=takeover_time,
                                   worker_owner_token=original)
    assert len(_events(client, "daily_count")) == 1


def test_manual_refresh_and_worker_share_event_identity(seeded_client, settings):
    client = seeded_client
    _configure(client)
    def automatic():
        return _tick(client, settings, REMINDER, owner="worker")
    def manual():
        with client.app.state.session_factory() as db:
            return notifications.evaluate(db, settings=settings, now_utc=REMINDER)
    with ThreadPoolExecutor(max_workers=2) as pool:
        automatic_result = pool.submit(automatic)
        manual_result = pool.submit(manual)
        automatic_result.result()
        manual_result.result()
    assert len(_events(client, "daily_count")) == 1
    assert len({event.dedupe_key for event in _events(client)}) == len(_events(client))


def test_failure_preserves_success_and_recovers_with_backoff(seeded_client, settings, monkeypatch):
    client = seeded_client
    _configure(client)
    assert _tick(client, settings, REMINDER)["status"] == "evaluated"
    original = notifications.evaluate
    def broken(*args, **kwargs):
        raise RuntimeError("simulated worker failure")
    monkeypatch.setattr(notifications, "evaluate", broken)
    failure_at = REMINDER + timedelta(minutes=1)
    with pytest.raises(RuntimeError):
        _tick(client, settings, failure_at)
    with client.app.state.session_factory() as db:
        health = notification_scheduler.health(db, now_utc=failure_at)
        assert health["status"] == "error" and health["last_success_at"] == REMINDER
        assert health["last_error_code"] == "RuntimeError"
    monkeypatch.setattr(notifications, "evaluate", original)
    assert _tick(client, settings, failure_at + timedelta(minutes=1))["status"] == "evaluated"
    with client.app.state.session_factory() as db:
        assert notification_scheduler.health(db, now_utc=failure_at + timedelta(minutes=1))["status"] == "running"


def test_worker_retry_delay_is_bounded_and_resets_after_success(monkeypatch):
    attempts = []
    delays = []
    def fake_tick(*_args, **_kwargs):
        attempts.append(True)
        if len(attempts) <= 5:
            raise RuntimeError("temporary database failure")
        return {"status": "waiting"}
    class StopAfterRecovery:
        def is_set(self): return len(delays) == 6
        def wait(self, seconds): delays.append(seconds)
    monkeypatch.setattr(notification_worker, "tick", fake_tick)
    monkeypatch.setattr(notification_worker, "release", lambda *_args, **_kwargs: None)
    run_loop(object(), settings=object(), stop=StopAfterRecovery(), owner_token="retry-test")
    assert delays == [30, 60, 120, 240, 300, 30]


def test_evaluation_failure_rolls_back_and_retry_keeps_one_event(seeded_client, settings, monkeypatch):
    client = seeded_client
    _configure(client)
    original = notifications._persist
    def fail_after_staging(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("simulated crash before commit")
    monkeypatch.setattr(notifications, "_persist", fail_after_staging)
    with pytest.raises(RuntimeError):
        _tick(client, settings, REMINDER)
    assert not _events(client)
    with client.app.state.session_factory() as db:
        state = db.get(NotificationWorkerState, 1)
        assert state.last_success_at is None and as_utc(state.last_failure_at) == REMINDER
    monkeypatch.setattr(notifications, "_persist", original)
    assert _tick(client, settings, REMINDER + timedelta(seconds=30))["status"] == "evaluated"
    assert len(_events(client, "daily_count")) == 1


def test_health_uses_heartbeat_freshness_not_enabled_flag(seeded_client, settings):
    client = seeded_client
    _configure(client)
    notification_scheduler.claim(client.app.state.session_factory, owner_token="worker", now_utc=REMINDER)
    with client.app.state.session_factory() as db:
        assert notification_scheduler.health(db, now_utc=REMINDER)["status"] == "running"
        assert notification_scheduler.health(db, now_utc=REMINDER + timedelta(seconds=100))["status"] == "stopped"


def test_graceful_loop_shutdown_releases_lease(seeded_client, settings):
    client = seeded_client
    _configure(client)
    class StopAfterWait:
        def __init__(self): self.done = False
        def is_set(self): return self.done
        def wait(self, _seconds): self.done = True
    run_loop(client.app.state.session_factory, settings=settings, stop=StopAfterWait(),
             owner_token="graceful", clock=lambda: REMINDER)
    with client.app.state.session_factory() as db:
        state = db.get(NotificationWorkerState, 1)
        assert state.owner_token is None and state.lease_expires_at is None
        assert notification_scheduler.health(db, now_utc=REMINDER)["status"] == "stopped"


def test_guarded_demo_reset_rejects_enabled_or_live_worker(seeded_client):
    client = seeded_client
    _configure(client)
    with client.app.state.session_factory() as db:
        with pytest.raises(ValueError, match="Disable automatic"):
            verify_demo_dataset(db)
        db.rollback()
        notifications.update_settings(db, {"automatic_evaluation_enabled": False})
    notification_scheduler.claim(client.app.state.session_factory,
        owner_token="live", now_utc=datetime.now(timezone.utc))
    with client.app.state.session_factory() as db:
        with pytest.raises(ValueError, match="lease is active"):
            verify_demo_dataset(db)


def test_worker_runs_without_browser_and_preserves_operational_tables(seeded_client, settings):
    client = seeded_client
    _configure(client)
    with client.app.state.session_factory() as db:
        notifications.update_settings(db, {"daily_count_time": time(0, 0)})
    with client.app.state.db_engine.connect() as conn:
        before = {name: conn.execute(text(f'SELECT * FROM "{name}" ORDER BY rowid')).all()
                  for name in ("inventory_transactions", "ingredient_stock_state",
                               "purchase_orders", "purchase_order_items", "delivery_receipts")}
    env = {**os.environ, "STOCKMATE_DATABASE_URL": settings.database_url,
           "STOCKMATE_ENVIRONMENT": "test", "STOCKMATE_SECRET_KEY": "test-secret"}
    # The real CLI has no TestClient, cookie, browser, or login credential.
    run = subprocess.run([sys.executable, "-m", "app.notification_worker", "--once"],
                         env=env, capture_output=True, text=True, timeout=45)
    assert run.returncode == 0, run.stderr
    assert _events(client, "daily_count") or _events(client)
    with client.app.state.db_engine.connect() as conn:
        after = {name: conn.execute(text(f'SELECT * FROM "{name}" ORDER BY rowid')).all()
                 for name in before}
        assert after == before
        assert conn.execute(text("PRAGMA foreign_key_check")).all() == []
