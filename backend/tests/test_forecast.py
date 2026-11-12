"""Pinned-clock forecast acceptance, delivery eligibility, and no-write contracts."""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, text, update

from app.models import (Ingredient, IngredientDemandSetting, IngredientStockState,
    PurchaseOrder, PurchaseOrderItem, SupplierCalendarException, SupplierIngredient, SupplierScheduleRule, User)
from app.models.demand import WEEKDAYS
from app.seed import DEMO_ADMIN_PASSWORD, DEMO_STAFF_PASSWORD
from app.services import forecast
from app.services.inventory import record_stock_entry
from app.services.ordering import confirm_order, delay_order

AS_OF = datetime(2026, 11, 15, 3, tzinfo=timezone.utc)  # Sunday noon in Tokyo; baseline Monday 16th
FRIDAY = date(2026, 11, 20)


@pytest.fixture()
def plan(seeded_client, settings):
    client = seeded_client
    with client.app.state.session_factory() as db:
        db.execute(update(PurchaseOrder).values(status="cancelled"))
        korean = db.scalar(select(Ingredient).where(Ingredient.name == "Korean Noodles"))
        db.add(IngredientDemandSetting(ingredient_id=korean.id, enabled=True, safety_stock=Decimal("60"),
               **{day: Decimal("40") if index < 5 else Decimal("60") for index, day in enumerate(WEEKDAYS)}))
        state = db.get(IngredientStockState, korean.id)
        state.last_counted_at = AS_OF - timedelta(hours=1)
        state.last_transaction_at = AS_OF - timedelta(hours=1)
        db.commit()
        return client, settings, korean.id, korean.primary_supplier_id


def add_order(plan, status="confirmed", expected_date=FRIDAY, quantity="120", with_confirmation_evidence=True):
    client, _, iid, supplier_id = plan
    with client.app.state.session_factory() as db:
        order = PurchaseOrder(supplier_id=supplier_id, status="ordered" if status == "confirmed" and with_confirmation_evidence else status,
            expected_delivery_date=expected_date, created_by=1, ordered_by=1)
        order.items = [PurchaseOrderItem(ingredient_id=iid, quantity_ordered=Decimal(quantity), quantity_received=Decimal("0"))]
        db.add(order); db.commit()
        if status == "confirmed" and with_confirmation_evidence:
            confirm_order(db, user=db.get(User, 1), order_id=order.id)
        return order.id


def calculate(plan, days=7, now=AS_OF, settings=None, ingredient_id=None, today_usage=Decimal("0")):
    client, default_settings, iid, _ = plan
    with client.app.state.session_factory() as db:
        return forecast.generate_forecast(db, settings=settings or default_settings, days=days,
            ingredient_id=ingredient_id or iid, now_utc=now,
            # Existing next-week acceptance fixtures explicitly assume operations
            # have finished today. Default-policy regressions pass None below.
            remaining_usage_overrides={ingredient_id or iid: today_usage} if today_usage is not None else None)


def login(client, role="admin"):
    response = client.post("/api/v1/auth/login", json={"username": role, "password":
        DEMO_ADMIN_PASSWORD if role == "admin" else DEMO_STAFF_PASSWORD})
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrfToken"]}


def snapshot(client):
    with client.app.state.db_engine.connect() as connection:
        tables = connection.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")).scalars().all()
        return {table: connection.execute(text(f"SELECT * FROM [{table}] ORDER BY rowid")).all() for table in tables}


def test_korean_acceptance_with_friday_delivery(plan):
    add_order(plan)
    data = calculate(plan)
    item = data["ingredients"][0]
    rows = item["conservative"]["days"]
    assert data["start_date"] == date(2026, 11, 16)
    assert [row["closing_balance"] for row in rows] == [Decimal(n) for n in (140, 100, 60, 20, 100, 40, -20)]
    assert [row["estimated_consumption"] for row in rows] == [Decimal(n) for n in (40, 40, 40, 40, 40, 60, 60)]
    assert [row["status"] for row in rows] == ["enough", "enough", "enough", "low", "enough", "low", "shortage"]
    assert rows[4]["eligible_incoming"] == 120
    assert rows[4]["pre_delivery_balance"] == -20
    assert rows[4]["pre_delivery_shortage"] == 20 and rows[4]["pre_delivery_shortage_risk"] is True
    assert rows[-1]["unmet_demand"] == 20
    assert item["conservative"]["first_shortage_date"] == date(2026, 11, 22)
    assert item["confidence"]["level"] == "high"
    assert item["expected"]["days"] == rows


def test_without_delivery_shortage_friday_and_unmet_demand_carries(plan):
    rows = calculate(plan)["ingredients"][0]["conservative"]["days"]
    assert rows[4]["closing_balance"] == -20
    assert rows[5]["opening_balance"] == -20 and rows[5]["closing_balance"] == -80
    assert rows[6]["closing_balance"] == -140


@pytest.mark.parametrize("horizon", [14, 30])
def test_supported_horizons_are_continuous_and_decimal_safe(plan, horizon):
    data = calculate(plan, days=horizon)
    rows = data["ingredients"][0]["conservative"]["days"]
    assert len(rows) == horizon and data["end_date"] == data["start_date"] + timedelta(days=horizon - 1)
    expected = Decimal("180")
    for index, row in enumerate(rows):
        assert row["date"] == data["start_date"] + timedelta(days=index)
        assert row["opening_balance"] == expected
        expected -= Decimal("40") if row["date"].weekday() < 5 else Decimal("60")
        assert row["closing_balance"] == expected


def test_ordered_supply_is_expected_only_and_dependency_is_reported(plan):
    add_order(plan, status="ordered", quantity="240")
    item = calculate(plan)["ingredients"][0]
    assert item["conservative"]["days"][4]["eligible_incoming"] == 0
    assert item["expected"]["days"][4]["eligible_incoming"] == 240
    assert item["expected"]["days"][-1]["closing_balance"] == 100
    assert item["depends_on_uncertain"] is True
    assert item["open_outstanding_quantity"] == 240
    assert any("not supplier-confirmed" in warning for warning in item["warnings"])


@pytest.mark.parametrize("status", ["draft", "cancelled"])
def test_draft_and_cancelled_never_count_as_committed_supply(plan, status):
    order_id = add_order(plan, status=status)
    item = calculate(plan)["ingredients"][0]
    delivery = next(row for row in item["incoming"] if row["order_id"] == order_id)
    assert not delivery["conservative_eligible"] and not delivery["expected_eligible"]
    assert item["open_outstanding_quantity"] == 0
    assert item["expected"]["days"][4]["eligible_incoming"] == 0


def test_partial_receiving_and_rejected_goods_do_not_double_count(plan):
    client, _, iid, _ = plan
    order_id = add_order(plan)
    headers = login(client)
    response = client.post(f"/api/v1/purchase-orders/{order_id}/receive", headers=headers, json={
        "received_by_staff_name": "Forecast Demo", "idempotency_key": "forecast-partial-receipt",
        "lines": [{"ingredient_id": iid, "quantity": "30", "quantity_rejected": "10"}],
    })
    assert response.status_code == 201, response.text
    item = calculate(plan)["ingredients"][0]
    delivery = next(row for row in item["incoming"] if row["order_id"] == order_id)
    assert item["actual_available"] == 210
    assert delivery["accepted_received_quantity"] == 30 and delivery["outstanding_quantity"] == 90
    assert item["conservative"]["days"][4]["eligible_incoming"] == 90  # rejected 10 still owed
    assert item["conservative"]["days"][-1]["closing_balance"] == -20


def test_fully_received_goods_are_only_in_available_stock(plan):
    client, _, iid, _ = plan
    order_id = add_order(plan)
    response = client.post(f"/api/v1/purchase-orders/{order_id}/receive", headers=login(client), json={
        "received_by_staff_name": "Forecast Demo", "idempotency_key": "forecast-full-receipt",
        "lines": [{"ingredient_id": iid, "quantity": "120"}],
    })
    assert response.status_code == 201
    item = calculate(plan)["ingredients"][0]
    assert item["actual_available"] == 300
    assert not any(row["order_id"] == order_id for row in item["incoming"])
    assert item["conservative"]["days"][4]["eligible_incoming"] == 0


@pytest.mark.parametrize("status,expected_date,reason", [
    ("delayed", FRIDAY, "Delayed"), ("confirmed", date(2026, 11, 14), "Overdue"),
    ("confirmed", date(2026, 12, 1), "outside"),
])
def test_excluded_open_orders_remain_visible_without_rescheduling(plan, status, expected_date, reason):
    order_id = add_order(plan, status=status, expected_date=expected_date)
    item = calculate(plan)["ingredients"][0]
    row = next(row for row in item["incoming"] if row["order_id"] == order_id)
    assert row["expected_date"] == expected_date and row["outstanding_quantity"] == 120
    assert not row["expected_eligible"] and any(reason in text for text in row["reasons"])
    assert item["open_outstanding_quantity"] == item["excluded_open_quantity"] == 120


def test_missing_expected_date_is_excluded_defensively_without_invention():
    # Current schema forbids NULL dates. The pure guard also covers malformed/legacy inputs.
    conservative, expected, reasons = forecast.delivery_eligibility(status="confirmed", expected_date=None,
        today=AS_OF.date(), start=date(2026, 11, 16), end=date(2026, 11, 22))
    assert not conservative and not expected and "missing" in reasons[0]


def test_supplier_holiday_and_schedule_changes_never_move_an_order(plan):
    client, _, _, supplier_id = plan
    order_id = add_order(plan)
    with client.app.state.session_factory() as db:
        db.add(SupplierCalendarException(supplier_id=supplier_id, date=FRIDAY, type="delivery_cancelled", is_active=True))
        db.commit()
    item = calculate(plan)["ingredients"][0]
    row = next(row for row in item["incoming"] if row["order_id"] == order_id)
    assert not row["expected_eligible"] and row["expected_date"] == FRIDAY
    assert any("calendar cancels" in warning for warning in item["warnings"])


def test_off_schedule_confirmed_delivery_is_uncertain_until_exception_allows_it(plan):
    client, _, _, supplier_id = plan
    thursday = date(2026, 11, 19)
    order_id = add_order(plan, expected_date=thursday)
    row = next(row for row in calculate(plan)["ingredients"][0]["incoming"] if row["order_id"] == order_id)
    assert row["expected_eligible"] and not row["conservative_eligible"] and row["uncertain"]
    with client.app.state.session_factory() as db:
        db.add(SupplierCalendarException(supplier_id=supplier_id, date=thursday, type="extra_delivery",
            override_cutoff_at=datetime(2026, 11, 18, 5, tzinfo=timezone.utc), is_active=True))
        db.commit()
    item = calculate(plan)["ingredients"][0]
    row = next(row for row in item["incoming"] if row["order_id"] == order_id)
    assert row["conservative_eligible"] and not row["uncertain"]


def test_cutoff_override_and_ordering_suspension_have_distinct_meanings(plan):
    client, _, _, supplier_id = plan
    add_order(plan)
    cutoff = datetime(2026, 11, 19, 6, tzinfo=timezone.utc)
    with client.app.state.session_factory() as db:
        db.add_all([
            SupplierCalendarException(supplier_id=supplier_id, date=FRIDAY, type="cutoff_override", override_cutoff_at=cutoff, is_active=True),
            SupplierCalendarException(supplier_id=supplier_id, date=FRIDAY, type="ordering_suspended", is_active=True),
        ]); db.commit()
    item = calculate(plan)["ingredients"][0]
    assert item["conservative"]["days"][4]["eligible_incoming"] == 120
    assert any("Ordering is suspended" in warning for warning in item["warnings"])
    with client.app.state.session_factory() as db:
        db.execute(delete(SupplierCalendarException).where(SupplierCalendarException.type == "ordering_suspended")); db.commit()
    cutoffs = calculate(plan)["ingredients"][0]["upcoming_cutoffs"]
    assert any(row["cutoff_at"] == cutoff and row["exception_type"] == "cutoff_override" for row in cutoffs)


def test_historical_order_supplier_calendar_survives_catalog_reassignment(plan):
    client, _, iid, supplier_id = plan
    add_order(plan)
    with client.app.state.session_factory() as db:
        db.get(Ingredient, iid).primary_supplier_id = None
        db.flush()
        db.execute(delete(SupplierIngredient).where(SupplierIngredient.ingredient_id == iid, SupplierIngredient.supplier_id == supplier_id))
        db.add(SupplierCalendarException(supplier_id=supplier_id, date=FRIDAY, type="delivery_cancelled", is_active=True)); db.commit()
    assert calculate(plan)["ingredients"][0]["conservative"]["days"][4]["eligible_incoming"] == 0


@pytest.mark.parametrize("balance,safety,status", [(60, 60, "enough"), (1, 60, "low"),
    (0, 0, "depleted"), (-1, 0, "shortage"), (1, 0, "enough"), (None, 60, "unknown"), (10, None, "unknown")])
def test_availability_boundaries(balance, safety, status):
    assert forecast.availability(Decimal(balance) if balance is not None else None,
        Decimal(safety) if safety is not None else None) == status


def test_missing_daily_value_poison_downstream_while_zero_is_valid(plan):
    client, _, iid, _ = plan
    add_order(plan)
    with client.app.state.session_factory() as db:
        config = db.get(IngredientDemandSetting, iid); config.tuesday = None; db.commit()
    rows = calculate(plan)["ingredients"][0]["conservative"]["days"]
    assert rows[0]["closing_balance"] == 140
    assert rows[1]["opening_balance"] == 140 and rows[1]["estimated_consumption"] is None
    assert all(row["closing_balance"] is None and row["status"] == "unknown" for row in rows[1:])
    assert rows[2]["opening_balance"] is None
    with client.app.state.session_factory() as db:
        db.get(IngredientDemandSetting, iid).tuesday = Decimal("0"); db.commit()
    assert calculate(plan)["ingredients"][0]["conservative"]["days"][1]["closing_balance"] == 140


@pytest.mark.parametrize("mode", ["missing", "disabled", "missing_safety"])
def test_missing_disabled_and_incomplete_configs_do_not_invent_demand(plan, mode):
    client, _, iid, _ = plan
    with client.app.state.session_factory() as db:
        config = db.get(IngredientDemandSetting, iid)
        if mode == "missing": db.delete(config)
        elif mode == "disabled": config.enabled = False
        else: config.safety_stock = None
        db.commit()
    item = calculate(plan)["ingredients"][0]
    assert all(row["closing_balance"] is None for row in item["conservative"]["days"])
    if mode == "disabled": assert all(row["estimated_consumption"] is None for row in item["expected"]["days"])


def test_independent_fractional_ingredient_forecast(plan):
    client, _, _, _ = plan
    with client.app.state.session_factory() as db:
        soup = db.scalar(select(Ingredient).where(Ingredient.name == "Soup Base"))
        db.add(IngredientDemandSetting(ingredient_id=soup.id, enabled=True, safety_stock=Decimal("1.5"),
            **{day: Decimal("0.125") for day in WEEKDAYS})); db.commit(); soup_id = soup.id
    item = calculate(plan, ingredient_id=soup_id)["ingredients"][0]
    assert item["unit"] == "pack" and item["conservative"]["days"][0]["closing_balance"] == Decimal("44.875")
    assert calculate(plan)["ingredients"][0]["conservative"]["days"][0]["closing_balance"] == 140


@pytest.mark.parametrize("age,expected", [(1, "high"), (24, "high"), (25, "medium"), (73, "low")])
def test_count_freshness_is_independent_and_never_persisted(plan, age, expected):
    client, _, iid, _ = plan
    with client.app.state.session_factory() as db:
        db.get(IngredientStockState, iid).last_counted_at = AS_OF - timedelta(hours=age); db.commit()
    before = snapshot(client)
    item = calculate(plan)["ingredients"][0]
    assert item["confidence"]["level"] == expected
    assert item["conservative"]["days"][0]["status"] == "enough"
    assert snapshot(client) == before


def test_discrepancy_or_missing_count_is_low_confidence(plan):
    client, _, iid, _ = plan
    with client.app.state.session_factory() as db:
        state = db.get(IngredientStockState, iid); state.last_counted_at = None; state.unresolved_discrepancy = True; db.commit()
    item = calculate(plan)["ingredients"][0]
    assert item["confidence"]["level"] == "low" and item["confidence"]["stale"]


def test_timezone_rollover_and_business_boundary(plan):
    sunday_late = datetime(2026, 11, 15, 14, 59, tzinfo=timezone.utc)
    monday_midnight = sunday_late + timedelta(minutes=1)
    assert calculate(plan, now=sunday_late)["start_date"] == date(2026, 11, 16)
    assert calculate(plan, now=monday_midnight)["start_date"] == date(2026, 11, 17)
    late_boundary = plan[1].model_copy(update={"business_day_start_time": "04:00"})
    assert calculate(plan, now=monday_midnight + timedelta(hours=2), settings=late_boundary)["start_date"] == date(2026, 11, 16)
    assert calculate(plan, now=monday_midnight + timedelta(hours=4), settings=late_boundary)["start_date"] == date(2026, 11, 17)


def test_as_of_stock_does_not_repeat_recorded_consumption_or_infer_usage_from_counts(plan):
    client, settings, iid, _ = plan
    with client.app.state.session_factory() as db:
        admin = db.get(User, 1)
        record_stock_entry(db, ingredient_id=iid, user=admin, mode="used", quantity=Decimal("50"),
            reason="daily_count", idempotency_key="forecast-used-baseline", confirmed=True, allow_negative=False, settings=settings)
    data = calculate(plan)
    assert data["business_date"] == date(2026, 11, 15)
    assert data["ingredients"][0]["conservative"]["days"][0]["opening_balance"] == 130
    assert data["ingredients"][0]["conservative"]["days"][0]["closing_balance"] == 90
    with client.app.state.session_factory() as db:
        record_stock_entry(db, ingredient_id=iid, user=db.get(User, 1), mode="remaining", quantity=Decimal("135"),
            reason="daily_count", idempotency_key="forecast-count-baseline", confirmed=True, allow_negative=False, settings=settings)
    assert calculate(plan)["ingredients"][0]["conservative"]["days"][0]["closing_balance"] == 95


def test_delivery_arrival_timing_warning_when_day_depends_on_the_shipment(plan):
    add_order(plan, expected_date=date(2026, 11, 16), quantity="120")
    client, _, iid, _ = plan
    with client.app.state.session_factory() as db:
        db.get(IngredientDemandSetting, iid).monday = Decimal("200"); db.commit()
    row = calculate(plan)["ingredients"][0]["conservative"]["days"][0]
    assert row["closing_balance"] == 100 and any("arrives late" in warning for warning in row["warnings"])


def test_ledger_drift_is_unknown_without_automatic_repair(plan):
    client, _, iid, _ = plan
    with client.app.state.session_factory() as db:
        db.get(IngredientStockState, iid).current_quantity = Decimal("999"); db.commit()
    before = snapshot(client)
    item = calculate(plan)["ingredients"][0]
    assert not item["ledger_consistent"] and item["actual_available"] is None
    assert item["ledger_balance"] == 180 and item["cached_balance"] == 999
    assert all(row["closing_balance"] is None for row in item["expected"]["days"])
    assert snapshot(client) == before


def test_snapshot_newer_than_clock_is_not_a_historical_forecast(plan):
    client, _, iid, _ = plan
    with client.app.state.session_factory() as db:
        db.get(IngredientStockState, iid).last_transaction_at = AS_OF + timedelta(minutes=1); db.commit()
    assert calculate(plan)["ingredients"][0]["conservative"]["days"][0]["closing_balance"] is None


def test_api_authorization_serialization_horizons_and_read_only(plan, monkeypatch):
    client, _, iid, _ = plan
    monkeypatch.setattr(forecast, "_now", lambda: AS_OF)
    assert client.get("/api/v1/forecast/calendar").status_code == 401
    for role in ("admin", "staff"):
        login(client, role)
        before = snapshot(client)
        response = client.get(f"/api/v1/forecast/calendar?days=7&ingredient_id={iid}")
        assert response.status_code == 200, response.text
        assert response.json()["ingredients"][0]["actual_available"] == "180.000"
        assert response.json()["as_of_local"].endswith("+09:00")
        assert snapshot(client) == before
    assert client.get("/api/v1/forecast/calendar?days=8").status_code == 422
    assert client.get("/api/v1/forecast/calendar?ingredient_id=999999").status_code == 404
    assert client.post("/api/v1/forecast/calendar", json={}).status_code == 405


def test_batch_summary_is_deterministic_and_all_forecast_reads_are_non_mutating(plan):
    client, settings, _, _ = plan
    add_order(plan, status="ordered", quantity="240")
    before = snapshot(client)
    with client.app.state.session_factory() as db:
        first = forecast.generate_forecast(db, settings=settings, now_utc=AS_OF, remaining_usage_overrides={plan[2]: Decimal("0")})
        second = forecast.generate_forecast(db, settings=settings, now_utc=AS_OF, remaining_usage_overrides={plan[2]: Decimal("0")})
    assert first == second
    assert first["summary"]["run_out_count"] == 1
    assert first["summary"]["missing_demand_count"] == 7
    assert first["summary"]["uncertain_dependency_count"] == 1
    assert first["summary"]["earliest_shortage_date"] == FRIDAY
    assert snapshot(client) == before


def set_available(plan, quantity):
    client, settings, iid, _ = plan
    with client.app.state.session_factory() as db:
        record_stock_entry(db, ingredient_id=iid, user=db.get(User, 1), mode="remaining", quantity=Decimal(quantity),
            reason="daily_count", idempotency_key=f"forecast-correction-stock-{quantity}", confirmed=True, allow_negative=False, settings=settings)


def receive_partial(plan, order_id):
    client, _, iid, _ = plan
    response = client.post(f"/api/v1/purchase-orders/{order_id}/receive", headers=login(client), json={
        "received_by_staff_name": "Targeted Forecast Demo", "idempotency_key": f"forecast-target-partial-{order_id}",
        "lines": [{"ingredient_id": iid, "quantity": "30", "quantity_rejected": "10"}],
    })
    assert response.status_code == 201, response.text


def test_possible_shortage_before_delivery_remains_red_risk_with_positive_close(plan):
    set_available(plan, "20")
    add_order(plan, expected_date=date(2026, 11, 16))
    row = calculate(plan)["ingredients"][0]["conservative"]["days"][0]
    assert row["opening_balance"] == 20 and row["estimated_consumption"] == 40
    assert row["pre_delivery_balance"] == -20 and row["pre_delivery_shortage"] == 20
    assert row["pre_delivery_shortage_risk"] is True
    assert row["closing_balance"] == 100 and row["status"] == "enough"
    assert "arrival time is unknown" in row["warnings"][0]


def test_default_today_upper_bound_and_explicit_zero_feed_tomorrow(plan):
    client, _, iid, _ = plan
    set_available(plan, "100")
    with client.app.state.session_factory() as db:
        db.get(IngredientDemandSetting, iid).sunday = Decimal("40"); db.commit()
    before = snapshot(client)
    default = calculate(plan, today_usage=None)["ingredients"][0]
    assert default["today_usage_source"] == "full_day_upper_bound"
    assert default["conservative"]["today"]["estimated_consumption"] == 40
    assert default["conservative"]["today"]["closing_balance"] == 60
    assert default["conservative"]["days"][0]["opening_balance"] == 60
    assert default["conservative"]["days"][0]["closing_balance"] == 20
    zero = calculate(plan, today_usage=Decimal("0"))["ingredients"][0]
    assert zero["today_usage_source"] == "override"
    assert zero["conservative"]["today"]["closing_balance"] == 100
    assert zero["conservative"]["days"][0]["closing_balance"] == 60
    assert snapshot(client) == before


def test_unknown_today_is_not_zero_but_an_explicit_zero_override_is_valid(plan):
    client, _, iid, _ = plan
    with client.app.state.session_factory() as db:
        db.get(IngredientDemandSetting, iid).sunday = None; db.commit()
    missing = calculate(plan, today_usage=None)["ingredients"][0]
    assert missing["today_remaining_usage"] is None and missing["today_usage_source"] == "missing"
    assert missing["conservative"]["today"]["closing_balance"] is None
    assert missing["conservative"]["days"][0]["closing_balance"] is None
    known = calculate(plan, today_usage=Decimal("0"))["ingredients"][0]
    assert known["conservative"]["days"][0]["closing_balance"] == 140
    with client.app.state.session_factory() as db:
        db.get(IngredientDemandSetting, iid).sunday = Decimal("0"); db.commit()
    configured_zero = calculate(plan, today_usage=None)["ingredients"][0]
    assert configured_zero["today_remaining_usage"] == 0
    assert configured_zero["today_usage_source"] == "full_day_upper_bound"


def test_eligible_today_deliveries_use_only_outstanding_accepted_quantities(plan):
    monday = date(2026, 11, 16)
    order_id = add_order(plan, expected_date=monday)
    receive_partial(plan, order_id)
    item = calculate(plan, now=AS_OF + timedelta(days=1), today_usage=None)["ingredients"][0]
    today = item["conservative"]["today"]
    assert item["actual_available"] == 210
    assert today["date"] == monday and today["eligible_incoming"] == 90
    assert today["estimated_consumption"] == 40 and today["closing_balance"] == 260
    assert item["conservative"]["days"][0]["opening_balance"] == 260


@pytest.mark.parametrize("initial_status,has_audit,confirmed", [
    ("ordered", True, False), ("confirmed", False, False), ("confirmed", True, True),
])
def test_partial_delivery_requires_matching_confirmation_evidence(plan, initial_status, has_audit, confirmed):
    order_id = add_order(plan, status=initial_status, with_confirmation_evidence=has_audit)
    receive_partial(plan, order_id)
    before = snapshot(plan[0])
    item = calculate(plan)["ingredients"][0]
    incoming = next(row for row in item["incoming"] if row["order_id"] == order_id)
    assert incoming["status"] == "partially_delivered" and incoming["outstanding_quantity"] == 90
    assert incoming["conservative_eligible"] is confirmed
    assert incoming["expected_eligible"] is True and incoming["uncertain"] is (not confirmed)
    assert item["conservative"]["days"][4]["eligible_incoming"] == (90 if confirmed else 0)
    assert item["expected"]["days"][4]["eligible_incoming"] == 90
    assert snapshot(plan[0]) == before


@pytest.mark.parametrize("reconfirmed", [False, True])
def test_delay_invalidates_old_confirmation_until_reconfirmed(plan, reconfirmed):
    client, _, _, _ = plan
    order_id = add_order(plan)
    with client.app.state.session_factory() as db:
        delay_order(db, user=db.get(User, 1), order_id=order_id)
        if reconfirmed:
            confirm_order(db, user=db.get(User, 1), order_id=order_id)
    receive_partial(plan, order_id)
    incoming = next(row for row in calculate(plan)["ingredients"][0]["incoming"] if row["order_id"] == order_id)
    assert incoming["conservative_eligible"] is reconfirmed


def test_changed_shipment_date_does_not_reuse_confirmation_for_the_old_date(plan):
    client, _, _, _ = plan
    order_id = add_order(plan)
    receive_partial(plan, order_id)
    with client.app.state.session_factory() as db:
        # Isolated legacy/manual change; forecasting itself never edits history.
        db.get(PurchaseOrder, order_id).expected_delivery_date = date(2026, 11, 18); db.commit()
    incoming = next(row for row in calculate(plan)["ingredients"][0]["incoming"] if row["order_id"] == order_id)
    assert not incoming["conservative_eligible"] and incoming["expected_eligible"]


def test_dashboard_summary_uses_the_same_full_day_default(plan):
    client, settings, iid, _ = plan
    with client.app.state.session_factory() as db:
        data = forecast.generate_forecast(db, settings=settings, now_utc=AS_OF)
    item = next(row for row in data["ingredients"] if row["ingredient_id"] == iid)
    assert item["today_remaining_usage"] == 60
    assert item["conservative"]["today"]["closing_balance"] == 120
    assert item["conservative"]["days"][0]["closing_balance"] == 80
    assert data["summary"]["earliest_shortage_date"] == date(2026, 11, 19)


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity", "abc", "1.0001", "100000000000"])
def test_remaining_usage_override_api_rejects_invalid_values(plan, value):
    client, _, iid, _ = plan
    login(client)
    response = client.get(f"/api/v1/forecast/calendar?ingredient_id={iid}&today_remaining_usage={value}")
    assert response.status_code == 422


def test_usage_override_api_is_scoped_read_only_and_expires_at_business_rollover(plan, monkeypatch):
    client, _, iid, _ = plan
    monkeypatch.setattr(forecast, "_now", lambda: AS_OF)
    login(client, "staff")
    before = snapshot(client)
    response = client.get(f"/api/v1/forecast/calendar?today_usage_ingredient_id={iid}&today_remaining_usage=0&today_usage_date=2026-11-15")
    assert response.status_code == 200, response.text
    item = next(row for row in response.json()["ingredients"] if row["ingredient_id"] == iid)
    assert item["today_remaining_usage"] == "0.000" and item["today_usage_source"] == "override"
    assert item["conservative"]["days"][0]["closing_balance"] == "140.000"
    assert client.get("/api/v1/forecast/calendar?today_remaining_usage=0").status_code == 422
    assert client.get(f"/api/v1/forecast/calendar?today_usage_ingredient_id={iid}").status_code == 422
    expired = client.get(f"/api/v1/forecast/calendar?ingredient_id={iid}&today_remaining_usage=0&today_usage_date=2026-11-14")
    assert expired.status_code == 409
    assert snapshot(client) == before
