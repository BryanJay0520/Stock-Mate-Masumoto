"""D42 acceptance and planning safeguards on migrated, isolated SQLite databases."""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select, text, update

from app.models import (Ingredient, IngredientDemandSetting, IngredientStockState, PurchaseOrder,
                        PurchaseOrderItem, Supplier, SupplierCalendarException, SupplierIngredient,
                        SupplierScheduleRule, User)
from app.models.demand import WEEKDAYS
from app.seed import DEMO_ADMIN_PASSWORD, DEMO_STAFF_PASSWORD
from app.services import recommendations
from app.services.inventory import record_stock_entry
from app.services.ordering import confirm_order

AS_OF = datetime(2026, 11, 19, 3, tzinfo=timezone.utc)  # Thursday noon JST, before Friday cutoff
FRIDAY = date(2026, 11, 20)
MONDAY = date(2026, 11, 23)


@pytest.fixture()
def plan(seeded_client, settings):
    with seeded_client.app.state.session_factory() as db:
        db.execute(update(PurchaseOrder).values(status="cancelled"))
        ingredient = db.scalar(select(Ingredient).where(Ingredient.name == "Korean Noodles"))
        iid, sid = ingredient.id, ingredient.primary_supplier_id
        db.add(IngredientDemandSetting(ingredient_id=iid, enabled=True, safety_stock=Decimal("60"),
            **{day: Decimal("40") if index < 5 else Decimal("60") for index, day in enumerate(WEEKDAYS)}))
        db.commit()
        record_stock_entry(db, ingredient_id=iid, user=db.get(User, 1), mode="remaining", quantity=Decimal("100"),
            reason="daily_count", idempotency_key="planner-opening-count", confirmed=True, allow_negative=False, settings=settings)
        state = db.get(IngredientStockState, iid)
        state.last_counted_at = AS_OF - timedelta(hours=1)
        db.commit()
    return seeded_client, settings, iid, sid


def run(plan, now=AS_OF, usage=Decimal("0"), filtered=True):
    client, settings, iid, _ = plan
    with client.app.state.session_factory() as db:
        return recommendations.generate_recommendations(db, settings=settings, now_utc=now,
            ingredient_id=iid if filtered else None,
            remaining_usage_overrides={iid: usage} if usage is not None else None)


def item(plan, **kwargs):
    return next(row for row in run(plan, **kwargs)["recommendations"] if row["ingredient_id"] == plan[2])


def stock(plan, quantity):
    client, settings, iid, _ = plan
    with client.app.state.session_factory() as db:
        record_stock_entry(db, ingredient_id=iid, user=db.get(User, 1), mode="remaining", quantity=Decimal(quantity),
            reason="daily_count", idempotency_key=f"planner-count-{quantity}", confirmed=True, allow_negative=False, settings=settings)


def order(plan, status="confirmed", quantity="120", day=FRIDAY, supplier_id=None, evidence=True):
    client, _, iid, sid = plan
    with client.app.state.session_factory() as db:
        row = PurchaseOrder(supplier_id=supplier_id or sid, status="ordered" if status == "confirmed" and evidence else status,
            expected_delivery_date=day, created_by=1, ordered_by=1)
        row.items = [PurchaseOrderItem(ingredient_id=iid, quantity_ordered=Decimal(quantity), quantity_received=Decimal("0"))]
        db.add(row); db.commit()
        if status == "confirmed" and evidence: confirm_order(db, user=db.get(User, 1), order_id=row.id)
        return row.id


def snapshot(client):
    with client.app.state.db_engine.connect() as connection:
        tables = connection.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")).scalars()
        return {table: connection.execute(text(f'SELECT * FROM "{table}" ORDER BY rowid')).all() for table in tables}


def login(client, role="admin"):
    response = client.post("/api/v1/auth/login", json={"username": role,
        "password": DEMO_ADMIN_PASSWORD if role == "admin" else DEMO_STAFF_PASSWORD})
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrfToken"]}


def test_friday_cycle_acceptance_a_includes_weekend_and_safety(plan):
    row = item(plan)
    calc = row["conservative"]
    assert row["coverage_start"] == FRIDAY and row["coverage_end"] == MONDAY - timedelta(days=1)
    assert row["following_opportunity"]["delivery_date"] == MONDAY
    assert calc["projected_before_delivery"] == 100
    assert calc["coverage_demand"] == 160 and calc["target_stock"] == 220
    assert calc["required_additional_quantity"] == 120
    assert calc["purchasing"]["recommended_package_count"] == 2
    assert calc["purchasing"]["recommended_quantity"] == 120 and calc["definitive"]
    assert row["status"] == "order_needed" and row["primary_supplier"]["phone"]
    assert row["opportunity"]["special_instructions"]


def test_existing_confirmed_order_acceptance_b_is_counted_once(plan):
    oid = order(plan)
    row = item(plan); calc = row["conservative"]
    assert calc["projected_before_delivery"] == 100
    assert calc["confirmed_incoming_in_cycle"] == 120
    assert calc["required_additional_quantity"] == 0 and calc["purchasing"]["recommended_package_count"] == 0
    assert calc["message"] == "Existing confirmed orders already cover this delivery cycle."
    contribution = next(r for r in row["existing_orders"] if r["order_id"] == oid)
    assert contribution["projection_phase"] == "delivery_cycle" and contribution["order_item_id"]
    assert row["status"] == "no_additional_order_needed"


def test_uncertain_order_acceptance_c_requires_verification_in_both_scenarios(plan):
    oid = order(plan, status="ordered")
    row = item(plan)
    assert row["conservative"]["purchasing"]["recommended_package_count"] == 2
    assert row["expected"]["purchasing"]["recommended_package_count"] == 0
    assert row["status"] == "verify_pending_order" and row["verify_pending_order"]
    for scenario in ("conservative", "expected"):
        assert not row[scenario]["definitive"]
        assert any("Confirm the pending order before ordering additional stock" in warning for warning in row[scenario]["warnings"])
    assert any(r["order_id"] == oid and r["uncertain"] for r in row["existing_orders"])


def test_rounding_acceptance_d_135_requires_three_boxes(plan):
    stock(plan, "85")
    calc = item(plan)["conservative"]
    assert calc["required_additional_quantity"] == 135
    assert calc["purchasing"]["recommended_package_count"] == 3
    assert calc["purchasing"]["recommended_quantity"] == 180


def test_shortage_acceptance_e_suppresses_backlog_based_order_quantity(plan):
    stock(plan, "30")
    row = item(plan, now=AS_OF - timedelta(days=1), usage=Decimal("40"))
    calc = row["conservative"]
    assert row["status"] == "urgent_shortage_risk"
    assert calc["shortage_before_delivery"] == 50 and calc["projected_before_delivery"] == -50
    assert calc["required_additional_quantity"] is None and calc["purchasing"]["recommended_quantity"] is None
    assert calc["provisional"] and calc["physical_review_required"] and not calc["definitive"]
    assert any("not physical negative stock" in warning for warning in calc["warnings"])
    assert len(row["manual_actions"]) == 4


def test_holiday_acceptance_f_extends_friday_through_tuesday(plan):
    client, _, _, sid = plan
    with client.app.state.session_factory() as db:
        db.add(SupplierCalendarException(supplier_id=sid, date=MONDAY, type="delivery_cancelled", is_active=True)); db.commit()
    row = item(plan)
    assert row["following_opportunity"]["delivery_date"] == date(2026, 11, 25)
    assert row["coverage_end"] == date(2026, 11, 24)
    assert row["conservative"]["coverage_demand"] == 240
    assert row["conservative"]["required_additional_quantity"] == 200


def test_different_suppliers_acceptance_g_keep_packages_and_deadlines_independent(plan):
    client, _, iid, _ = plan
    with client.app.state.session_factory() as db:
        chinese = db.scalar(select(Ingredient).where(Ingredient.name == "Chinese Noodles"))
        db.add(IngredientDemandSetting(ingredient_id=chinese.id, enabled=True, safety_stock=Decimal("60"),
            **{day: Decimal("40") for day in WEEKDAYS})); db.commit(); cid = chinese.id
    rows = run(plan, filtered=False)["recommendations"]
    korean = next(r for r in rows if r["ingredient_id"] == iid)
    chinese = next(r for r in rows if r["ingredient_id"] == cid)
    assert korean["primary_supplier"]["purchasing"]["package_size"] == 60
    assert chinese["primary_supplier"]["purchasing"]["package_size"] == 50
    assert korean["opportunity"]["delivery_date"] != chinese["opportunity"]["delivery_date"]
    assert korean["opportunity"]["cutoff_at"].time() != chinese["opportunity"]["cutoff_at"].time()


@pytest.mark.parametrize("instant,delivery", [
    (datetime(2026, 11, 19, 5, 30, tzinfo=timezone.utc), FRIDAY),
    (datetime(2026, 11, 19, 5, 30, 0, 1, tzinfo=timezone.utc), MONDAY),
])
def test_cutoff_is_inclusive_and_expired_cycles_are_skipped(plan, instant, delivery):
    row = item(plan, now=instant)
    assert row["coverage_start"] == delivery and row["opportunity"]["cutoff_at"] >= instant


def test_future_order_window_is_labeled_upcoming_and_supplier_timezone_is_used(plan):
    client, _, _, sid = plan
    with client.app.state.session_factory() as db:
        rule = db.scalar(select(SupplierScheduleRule).where(SupplierScheduleRule.supplier_id == sid, SupplierScheduleRule.delivery_weekday == 4))
        rule.order_open_days_before = 1; rule.order_open_time = time(13)
        rule.timezone = "America/New_York"; db.commit()
    row = item(plan)
    assert row["opportunity"]["status"] == "upcoming"
    assert row["opportunity"]["cutoff_at"] == datetime(2026, 11, 19, 19, 30, tzinfo=timezone.utc)


def test_ingredient_schedule_is_additive_and_not_shared_with_soup(plan):
    client, _, iid, sid = plan
    with client.app.state.session_factory() as db:
        soup = db.scalar(select(Ingredient).where(Ingredient.name == "Soup Base")); soup_id = soup.id
    rows = run(plan, now=datetime(2026, 11, 23, 3, tzinfo=timezone.utc), filtered=False)["recommendations"]
    assert next(r for r in rows if r["ingredient_id"] == iid)["coverage_start"] == date(2026, 11, 25)
    assert next(r for r in rows if r["ingredient_id"] == soup_id)["coverage_start"] == date(2026, 11, 27)


def test_one_time_delivery_and_suspension_use_scheduling_engine(plan):
    client, _, _, sid = plan
    with client.app.state.session_factory() as db:
        db.add_all([
            SupplierCalendarException(supplier_id=sid, date=MONDAY, type="ordering_suspended", is_active=True),
            SupplierCalendarException(supplier_id=sid, date=date(2026, 11, 21), type="extra_delivery",
                override_cutoff_at=AS_OF + timedelta(hours=1), note="One-time Saturday", is_active=True),
        ]); db.commit()
    row = item(plan)
    assert row["following_opportunity"]["delivery_date"] == date(2026, 11, 21)
    assert row["coverage_end"] == FRIDAY and row["conservative"]["coverage_demand"] == 40


@pytest.mark.parametrize("exception_type", ["delivery_cancelled", "ordering_suspended", "cutoff_override"])
def test_one_time_deliveries_also_respect_blocking_and_cutoff_exceptions(plan, exception_type):
    client, _, _, sid = plan
    with client.app.state.session_factory() as db:
        db.add_all([
            SupplierCalendarException(supplier_id=sid, date=FRIDAY, type="extra_delivery", override_cutoff_at=AS_OF + timedelta(hours=2), is_active=True),
            SupplierCalendarException(supplier_id=sid, date=FRIDAY, type=exception_type,
                override_cutoff_at=AS_OF - timedelta(hours=1) if exception_type == "cutoff_override" else None, is_active=True),
        ]); db.commit()
    row = item(plan)
    assert row["coverage_start"] == MONDAY  # Neither the regular nor extra Friday cycle bypasses the exception.


@pytest.mark.parametrize("status", ["draft", "cancelled", "delivered", "delayed"])
def test_uncommitted_or_excluded_orders_never_reduce_the_requirement(plan, status):
    oid = order(plan, status=status)
    row = item(plan)
    assert row["conservative"]["required_additional_quantity"] == 120
    assert row["expected"]["required_additional_quantity"] == 120
    if status == "draft":
        assert any("unfinished draft" in warning for warning in row["warnings"])
        assert next(r for r in row["existing_orders"] if r["order_id"] == oid)["projection_phase"] == "draft"


def test_overdue_order_keeps_original_date_and_is_not_moved_to_today(plan):
    day = AS_OF.date() - timedelta(days=1)
    oid = order(plan, day=day)
    row = item(plan)
    prior = next(r for r in row["existing_orders"] if r["order_id"] == oid)
    assert prior["expected_date"] == day and prior["projection_phase"] == "excluded"
    assert row["conservative"]["required_additional_quantity"] == 120


@pytest.mark.parametrize("evidence", [True, False])
def test_partial_and_rejected_receipts_use_only_outstanding_with_confirmation_evidence(plan, evidence):
    client, _, iid, _ = plan
    oid = order(plan, evidence=evidence)
    response = client.post(f"/api/v1/purchase-orders/{oid}/receive", headers=login(client), json={
        "received_by_staff_name": "Planner Test", "idempotency_key": "planner-receive",
        "lines": [{"ingredient_id": iid, "quantity": "30", "quantity_rejected": "10"}],
    })
    assert response.status_code == 201, response.text
    row = item(plan)
    assert row["actual_available"] == 130
    incoming = next(r for r in row["existing_orders"] if r["order_id"] == oid)
    assert incoming["outstanding_quantity"] == 90 and incoming["accepted_received_quantity"] == 30
    assert incoming["conservative_eligible"] is evidence
    assert row["conservative"]["required_additional_quantity"] == (0 if evidence else 90)
    assert row["expected"]["required_additional_quantity"] == 0


def test_incoming_before_delivery_is_in_starting_projection_and_not_subtracted_twice(plan):
    client, _, _, sid = plan
    with client.app.state.session_factory() as db:
        db.add(SupplierCalendarException(supplier_id=sid, date=AS_OF.date(), type="extra_delivery",
            override_cutoff_at=AS_OF - timedelta(hours=1), is_active=True)); db.commit()
    oid = order(plan, quantity="60", day=AS_OF.date())
    row = item(plan)
    assert row["conservative"]["projected_before_delivery"] == 160
    assert row["conservative"]["confirmed_incoming_before_delivery"] == 60
    assert row["conservative"]["required_additional_quantity"] == 60
    assert next(r for r in row["existing_orders"] if r["order_id"] == oid)["projection_phase"] == "starting_projection"


def test_late_cycle_incoming_cannot_cover_an_earlier_requirement(plan):
    client, _, _, sid = plan
    stock(plan, "20")
    with client.app.state.session_factory() as db:
        db.add(SupplierCalendarException(supplier_id=sid, date=date(2026, 11, 22), type="extra_delivery",
            override_cutoff_at=AS_OF - timedelta(hours=1), is_active=True)); db.commit()
    order(plan, quantity="300", day=date(2026, 11, 22))
    calc = item(plan)["conservative"]
    assert calc["required_additional_quantity"] == 140  # Friday/Saturday deficit + safety, despite Sunday's 300
    assert all(r["closing_with_recommendation"] >= 60 for r in calc["daily_calculation"])
    assert calc["daily_calculation"][0]["eligible_incoming"] == 0
    assert calc["daily_calculation"][-1]["eligible_incoming"] == 300


def test_same_day_risk_survives_zero_additional_order_and_positive_close(plan):
    stock(plan, "20"); order(plan, quantity="240")
    row = item(plan)
    assert row["conservative"]["required_additional_quantity"] == 0
    assert row["conservative"]["delivery_day_shortage_risk"] == 20
    assert row["status"] == "urgent_shortage_risk"
    assert row["conservative"]["provisional"] and not row["conservative"]["definitive"]
    assert run(plan)["forecast_summary"]["pre_delivery_risk_count"] == 1


def test_risk_on_an_intermediate_delivery_is_not_hidden_by_zero_order_need(plan):
    client, _, iid, sid = plan
    stock(plan, "140")
    with client.app.state.session_factory() as db:
        db.get(IngredientDemandSetting, iid).safety_stock = Decimal("20")
        db.add(SupplierCalendarException(supplier_id=sid, date=date(2026, 11, 22), type="extra_delivery",
            override_cutoff_at=AS_OF - timedelta(hours=1), is_active=True)); db.commit()
    order(plan, quantity="100", day=date(2026, 11, 22))
    row = item(plan)
    assert row["conservative"]["required_additional_quantity"] == 0
    assert row["conservative"]["pre_delivery_risks"] == [{"date": date(2026, 11, 22), "quantity": Decimal("20"), "phase": "delivery_cycle"}]
    assert row["status"] == "urgent_shortage_risk" and not row["conservative"]["definitive"]


@pytest.mark.parametrize("mode", ["missing", "disabled", "today", "cycle", "safety"])
def test_missing_settings_never_fabricate_a_quantity(plan, mode):
    client, _, iid, _ = plan
    with client.app.state.session_factory() as db:
        config = db.get(IngredientDemandSetting, iid)
        if mode == "missing": db.delete(config)
        elif mode == "disabled": config.enabled = False
        elif mode == "today": config.thursday = None
        elif mode == "cycle": config.saturday = None
        else: config.safety_stock = None
        db.commit()
    row = item(plan, usage=None)
    assert row["conservative"]["required_additional_quantity"] is None
    assert "configuration_required" in row["status_flags"]


def test_default_upper_bound_and_explicit_zero_change_only_the_forecast(plan):
    before = snapshot(plan[0])
    assert item(plan, usage=None)["conservative"]["required_additional_quantity"] == 160
    assert item(plan, usage=Decimal("0"))["conservative"]["required_additional_quantity"] == 120
    assert snapshot(plan[0]) == before


def test_zero_demand_and_zero_safety_are_valid(plan):
    client, _, iid, _ = plan
    with client.app.state.session_factory() as db:
        config = db.get(IngredientDemandSetting, iid)
        for day in WEEKDAYS: setattr(config, day, Decimal("0"))
        config.safety_stock = Decimal("0"); db.commit()
    calc = item(plan, usage=None)["conservative"]
    assert calc["coverage_demand"] == 0 and calc["required_additional_quantity"] == 0


def test_fractional_measurement_demand_and_packages_remain_exact(plan):
    client, settings, _, _ = plan
    with client.app.state.session_factory() as db:
        soup = db.scalar(select(Ingredient).where(Ingredient.name == "Soup Base"))
        db.add(IngredientDemandSetting(ingredient_id=soup.id, enabled=True, safety_stock=Decimal("0.250"),
            **{day: Decimal("0.125") for day in WEEKDAYS})); db.commit()
        record_stock_entry(db, ingredient_id=soup.id, user=db.get(User, 1), mode="remaining", quantity=Decimal("0.375"),
            reason="daily_count", idempotency_key="planner-fractional-soup", confirmed=True, allow_negative=False, settings=settings)
        link = db.get(SupplierIngredient, (soup.primary_supplier_id, soup.id))
        link.package_size = Decimal("0.125"); link.whole_packages_only = True; db.commit()
        data = recommendations.generate_recommendations(db, settings=settings, now_utc=AS_OF,
            ingredient_id=soup.id, remaining_usage_overrides={soup.id: Decimal("0")})
    row = data["recommendations"][0]
    assert row["unit"] == "pack"
    assert row["conservative"]["coverage_demand"] == Decimal("0.375")
    assert row["conservative"]["required_additional_quantity"] == Decimal("0.250")
    assert row["conservative"]["purchasing"]["recommended_package_count"] == 2


@pytest.mark.parametrize("size", [None, Decimal("0"), Decimal("-1")])
def test_missing_or_invalid_buying_size_shows_base_requirement_without_inventing_boxes(plan, size):
    client, _, iid, sid = plan
    with client.app.state.session_factory() as db:
        link = db.get(SupplierIngredient, (sid, iid)); link.package_size = size; db.commit()
    calc = item(plan)["conservative"]
    assert calc["required_additional_quantity"] == 120
    assert calc["purchasing"]["recommended_package_count"] is None and calc["purchasing"]["recommended_quantity"] is None
    assert calc["configuration_required"]


def test_stale_count_is_separate_from_required_quantity(plan):
    client, _, iid, _ = plan
    with client.app.state.session_factory() as db:
        db.get(IngredientStockState, iid).last_counted_at = AS_OF - timedelta(hours=80); db.commit()
    row = item(plan)
    assert row["confidence"]["level"] == "low" and row["conservative"]["required_additional_quantity"] == 120
    assert "check_physical_stock" in row["status_flags"] and not row["conservative"]["definitive"]
    assert "Verify actual stock before following this recommendation." in row["conservative"]["warnings"]


def test_alternatives_are_informational_and_never_split_the_requirement(plan):
    stock(plan, "30")
    row = item(plan, now=AS_OF - timedelta(days=2), usage=Decimal("40"))
    assert len(row["alternative_opportunities"]) == 1
    alternative = row["alternative_opportunities"][0]
    assert alternative["supplier"]["id"] != row["primary_supplier"]["id"]
    assert alternative["supplier"]["purchasing"]["package_size"] == 50
    assert "Availability is not confirmed" in alternative["warning"]
    assert "required_additional_quantity" not in alternative


@pytest.mark.parametrize("case", ["no_primary", "no_schedules", "only_one_extra"])
def test_missing_opportunities_and_following_cycle_are_explicit(plan, case):
    client, _, iid, sid = plan
    with client.app.state.session_factory() as db:
        if case == "no_primary": db.get(Ingredient, iid).primary_supplier_id = None
        else:
            db.execute(delete(SupplierScheduleRule).where(SupplierScheduleRule.supplier_id == sid))
            if case == "only_one_extra":
                db.add(SupplierCalendarException(supplier_id=sid, date=FRIDAY, type="extra_delivery", override_cutoff_at=AS_OF + timedelta(hours=1), is_active=True))
        db.commit()
    row = item(plan)
    assert row["conservative"]["required_additional_quantity"] is None
    assert "configuration_required" in row["status_flags"]
    if case == "only_one_extra": assert row["coverage_start"] == FRIDAY and row["coverage_end"] is None


@pytest.mark.parametrize("required,size,minimum,whole,quantity,packages", [
    ("135", "60", "0", True, "180", "3"), ("0", "60", "300", True, "0", "0"),
    ("1", "60", "130", True, "180", "3"), ("0.251", "0.125", "0", True, "0.375", "3"),
    ("1.125", "0.125", "2.001", False, "2.001", None),
])
def test_purchasing_rounding_is_decimal_safe_and_respects_minimums(required, size, minimum, whole, quantity, packages):
    link = SimpleNamespace(package_size=Decimal(size), min_order_quantity=Decimal(minimum), whole_packages_only=whole, purchase_package_name="Box")
    result = recommendations.purchase_rounding(Decimal(required), link)
    assert result["recommended_quantity"] == Decimal(quantity)
    assert result["recommended_package_count"] == (Decimal(packages) if packages is not None else None)


def test_api_roles_serialization_readonly_and_date_scoped_zero_override(plan, monkeypatch):
    client, _, iid, _ = plan
    monkeypatch.setattr(recommendations, "_now", lambda: AS_OF)
    assert client.get("/api/v1/recommendations").status_code == 401
    for role in ("admin", "staff"):
        login(client, role)
        before = snapshot(client)
        response = client.get(f"/api/v1/recommendations?ingredient_id={iid}&today_remaining_usage=0&today_usage_date=2026-11-19")
        assert response.status_code == 200, response.text
        row = response.json()["recommendations"][0]
        assert row["today_remaining_usage"] == "0.000"
        assert row["conservative"]["required_additional_quantity"] == "120.000"
        assert client.get(f"/api/v1/recommendations?ingredient_id={iid}&today_remaining_usage=0&today_usage_date=2026-11-18").status_code == 409
        assert snapshot(client) == before
    assert client.post("/api/v1/recommendations", json={}).status_code == 405


@pytest.mark.parametrize("query", ["today_remaining_usage=0", "today_usage_ingredient_id=1", "ingredient_id=999999",
    "ingredient_id=1&today_remaining_usage=-1", "ingredient_id=1&today_remaining_usage=NaN", "ingredient_id=1&today_remaining_usage=1.0001"])
def test_invalid_requests_do_not_write(plan, query, monkeypatch):
    client = plan[0]; login(client)
    monkeypatch.setattr(recommendations, "_now", lambda: AS_OF)
    before = snapshot(client)
    assert client.get(f"/api/v1/recommendations?{query}").status_code in (404, 422)
    assert snapshot(client) == before


def test_batch_computes_each_supplier_calendar_and_forecast_once_and_is_deterministic(plan, monkeypatch):
    calendars, forecasts = [], []
    schedule_fn, forecast_fn = recommendations.compute_opportunities, recommendations.generate_forecast
    def scheduling(db, **kwargs):
        calendars.append(kwargs["supplier_id"]); return schedule_fn(db, **kwargs)
    def forecasting(db, **kwargs):
        forecasts.append(1); return forecast_fn(db, **kwargs)
    monkeypatch.setattr(recommendations, "compute_opportunities", scheduling)
    monkeypatch.setattr(recommendations, "generate_forecast", forecasting)
    before = snapshot(plan[0])
    first = run(plan, filtered=False)
    assert len(calendars) == len(set(calendars)) == 3 and len(forecasts) == 1
    assert first == run(plan, filtered=False)
    assert snapshot(plan[0]) == before


def test_dashboard_forecast_summary_matches_separate_seven_day_default_forecast(plan):
    client, settings, _, _ = plan
    with client.app.state.session_factory() as db:
        planner = recommendations.generate_recommendations(db, settings=settings, now_utc=AS_OF)
        forecast = recommendations.generate_forecast(db, settings=settings, now_utc=AS_OF, days=7)
    assert planner["forecast_summary"] == forecast["summary"]
