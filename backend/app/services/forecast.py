"""Deterministic, read-only forecasts from one database snapshot (D40).

No writes, commits, cache repairs, synthetic orders, or purchasing calculations.
The injected clock describes the current snapshot; this is not a historical API.
"""
from collections import defaultdict
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session
from pydantic import TypeAdapter, ValidationError

from app.config import Settings
from app.errors import ApiError
from app.models import (AuditLog, DeliveryReceipt, Ingredient, IngredientDemandSetting, IngredientStockState, InventoryTransaction,
                        PurchaseOrder, PurchaseOrderItem, Supplier, SupplierCalendarException,
                        SupplierIngredient, SupplierScheduleRule)
from app.models.demand import WEEKDAYS
from app.schemas.demand import DemandQuantity
from app.services.business_time import business_date_for, parse_day_start
from app.services.confidence import as_utc, count_confidence
from app.services.inventory import verify_ledger
from app.services.scheduling import compute_opportunities

ZERO = Decimal("0.000")
PLACED_STATUSES = ("ordered", "confirmed", "partially_delivered", "delayed")


def _now():
    return datetime.now(timezone.utc)


def availability(balance: Decimal | None, safety: Decimal | None) -> str:
    if balance is None or safety is None:
        return "unknown"
    if balance < 0:
        return "shortage"
    if balance == 0:
        return "depleted"
    return "low" if balance < safety else "enough"


def delivery_eligibility(*, status, expected_date, today, start, end,
                         cancelled=False, off_schedule=False, remainder_confirmed=False) -> tuple[bool, bool, list[str]]:
    """Keep commitment quantity separate from eligibility in this forecast window."""
    reasons = []
    if status not in ("ordered", "confirmed", "partially_delivered", "delayed"):
        return False, False, [f"{status.replace('_', ' ').capitalize()} orders are excluded."]
    if expected_date is None:
        return False, False, ["Expected delivery date is missing; no arrival date is invented."]
    if status == "delayed":
        reasons.append("Delayed order excluded until its date and delivery status are reconfirmed.")
    if expected_date < today:
        reasons.append("Overdue delivery excluded; it is not moved to today.")
    elif expected_date > end:
        reasons.append("Delivery is outside this forecast horizon.")
    if cancelled:
        reasons.append("Supplier calendar cancels delivery on this date; the order is not rescheduled.")
    if reasons:
        return False, False, reasons
    if off_schedule:
        reasons.append("Delivery date conflicts with the active weekly schedule; expected scenario only. Confirm with the supplier.")
    if status == "ordered":
        reasons.append("Ordered but not supplier-confirmed; expected scenario only.")
    if status == "partially_delivered":
        reasons.append("Outstanding remainder retains matching supplier-confirmation audit evidence."
            if remainder_confirmed else "Confirmation of the outstanding remainder is not established; expected scenario only.")
    confirmed = status == "confirmed" or (status == "partially_delivered" and remainder_confirmed)
    return confirmed and not off_schedule, True, reasons


def _partial_confirmation_evidence(db, order_rows, now):
    """Read existing audits; a receipt status never establishes supplier confirmation."""
    partials = {order.id for order, _, _ in order_rows if order.status == "partially_delivered"}
    if not partials:
        return {}
    receipt_orders = dict(db.execute(select(DeliveryReceipt.id, DeliveryReceipt.purchase_order_id)
        .where(DeliveryReceipt.purchase_order_id.in_(partials))).all())
    audits = db.scalars(select(AuditLog).where(or_(
        and_(AuditLog.entity_type == "purchase_order", AuditLog.entity_id.in_(partials)),
        and_(AuditLog.entity_type == "delivery_receipt", AuditLog.action == "receipt.reverse", AuditLog.entity_id.in_(receipt_orders)),
    )).order_by(AuditLog.id)).all()
    evidence = {}
    for audit in audits:
        if as_utc(audit.created_at) > now:
            continue
        try:
            after = json.loads(audit.after_json) if audit.after_json else {}
            if not isinstance(after, dict):
                after = {}
        except (ValueError, TypeError):
            after = {}
        if audit.action == "receipt.reverse":
            evidence[receipt_orders.get(audit.entity_id)] = None
        elif audit.action == "order.confirm":
            evidence[audit.entity_id] = after if after.get("status") == "confirmed" else None
        elif audit.action in ("order.delay", "order.cancel", "order.mark_ordered", "order.update", "order.create"):
            evidence[audit.entity_id] = None
        elif audit.action == "order.receive" and after.get("status") not in ("partially_delivered", "delivered"):
            evidence[audit.entity_id] = None
    return evidence


def _matching_confirmation(snapshot, order, line):
    if not snapshot or snapshot.get("supplier_id") != order.supplier_id or snapshot.get("expected_delivery_date") != str(order.expected_delivery_date):
        return False
    try:
        return any(row.get("ingredient_id") == line.ingredient_id and
                   Decimal(str(row.get("quantity_ordered"))) == line.quantity_ordered
                   for row in snapshot.get("items", []) if isinstance(row, dict))
    except (ValueError, TypeError, ArithmeticError):
        return False


def _walk(*, today, start, days, starting, config, trusted, uncertain, scenario, confidence,
          baseline_valid, today_usage):
    balance = starting if baseline_valid else None
    safety = config.safety_stock if config else None
    usable = config is not None and config.enabled and safety is not None and safety >= 0
    if not usable:
        balance = None
    rows = []
    for day in [today, *(start + timedelta(days=offset) for offset in range(days))]:
        usage = today_usage if day == today else getattr(config, WEEKDAYS[day.weekday()]) if config and config.enabled else None
        if usage is not None and (not usage.is_finite() or usage < 0):
            usage = None
        incoming = trusted[day] + (uncertain[day] if scenario == "expected" else ZERO)
        opening = balance
        warnings = []
        if usage is None:
            warnings.append("Daily consumption is not configured; this and downstream balances are unknown.")
        pre_delivery = opening - usage if opening is not None and usage is not None else None
        pre_delivery_shortage = max(-pre_delivery, ZERO) if pre_delivery is not None and incoming > 0 else ZERO if pre_delivery is not None else None
        if pre_delivery_shortage is not None and pre_delivery_shortage > 0:
            warnings.append(f"Possible {pre_delivery_shortage:.3f} shortage before delivery if it arrives late; arrival time is unknown.")
        if uncertain[day] > 0:
            warnings.append("Uncertain incoming is included only in the expected scenario.")
        balance = opening + incoming - usage if opening is not None and usage is not None else None
        rows.append({"date": day, "opening_balance": opening, "confirmed_incoming": trusted[day],
                     "uncertain_incoming": uncertain[day], "eligible_incoming": incoming,
                     "estimated_consumption": usage, "closing_balance": balance, "safety_stock": safety,
                     "pre_delivery_balance": pre_delivery, "pre_delivery_shortage": pre_delivery_shortage,
                     "pre_delivery_shortage_risk": pre_delivery_shortage is not None and pre_delivery_shortage > 0,
                     "status": availability(balance, safety),
                     "unmet_demand": max(-balance, ZERO) if balance is not None else None,
                     "delivery_uncertainty": uncertain[day] > 0, "confidence": confidence,
                     "warnings": warnings})
    def first(statuses):
        return next((row["date"] for row in rows if row["status"] in statuses), None)
    return {"scenario": scenario, "today": rows[0], "days": rows[1:], "first_shortage_date": first(("shortage",)),
            "first_run_out_date": first(("depleted", "shortage")), "first_low_date": first(("low",)),
            "first_pre_delivery_risk_date": next((row["date"] for row in rows if row["pre_delivery_shortage_risk"]), None)}


def uncertain_dependency(conservative, expected, safety, through=None):
    return any(c["closing_balance"] is not None and e["closing_balance"] is not None and safety is not None
               and (through is None or c["date"] <= through)
               and ((c["closing_balance"] <= 0 < e["closing_balance"]) or
                    (c["closing_balance"] < safety <= e["closing_balance"] and e["closing_balance"] > 0))
               for c, e in zip([conservative["today"], *conservative["days"]], [expected["today"], *expected["days"]]))


def summarize_forecast(items, through=None):
    rows = []
    for item in items:
        conservative = dict(item["conservative"])
        for field in ("first_shortage_date", "first_run_out_date", "first_low_date", "first_pre_delivery_risk_date"):
            if through is not None and conservative[field] is not None and conservative[field] > through:
                conservative[field] = None
        depends = uncertain_dependency(item["conservative"], item["expected"], item["safety_stock"], through)
        missing = item["configuration_state"] in ("missing", "incomplete")
        disabled = item["configuration_state"] == "disabled"
        issues = []
        if conservative["first_run_out_date"]: issues.append("Projected to run out")
        if conservative["first_low_date"]: issues.append("Below planning safety stock")
        if conservative["first_pre_delivery_risk_date"]: issues.append("Pre-delivery shortage risk")
        if item["confidence"]["stale"]: issues.append("Stale physical count")
        if depends: issues.append("Depends on uncertain incoming")
        if missing: issues.append("Missing or incomplete demand settings")
        if disabled: issues.append("Forecast disabled")
        if not item["ledger_consistent"]: issues.append("Ledger verification failed")
        rows.append({"ingredient_id": item["ingredient_id"], "ingredient_name": item["ingredient_name"],
                     **{field: conservative[field] for field in ("first_shortage_date", "first_run_out_date", "first_low_date", "first_pre_delivery_risk_date")},
                     "stale_count": item["confidence"]["stale"], "depends_on_uncertain": depends,
                     "missing_demand": missing, "disabled": disabled, "issues": issues})
    dates = [row["first_shortage_date"] for row in rows if row["first_shortage_date"]]
    return {"configured_ingredients": sum(item["configuration_state"] == "enabled" for item in items),
            "run_out_count": sum(bool(row["first_run_out_date"]) for row in rows),
            "low_count": sum(bool(row["first_low_date"]) for row in rows),
            "pre_delivery_risk_count": sum(bool(row["first_pre_delivery_risk_date"]) for row in rows),
            "stale_count": sum(row["stale_count"] for row in rows),
            "uncertain_dependency_count": sum(row["depends_on_uncertain"] for row in rows),
            "missing_demand_count": sum(row["missing_demand"] for row in rows),
            "disabled_count": sum(row["disabled"] for row in rows),
            "earliest_shortage_date": min(dates) if dates else None, "ingredients": rows}


def generate_forecast(db: Session, *, settings: Settings, days: int = 7,
                      ingredient_id: int | None = None, now_utc: datetime | None = None,
                      remaining_usage_overrides: dict[int, Decimal] | None = None,
                      override_business_date=None, opportunities_by_supplier=None) -> dict:
    if days not in (7, 14, 30):
        raise ApiError(422, "invalid_horizon", "Forecast horizon must be 7, 14, or 30 days.")
    now = as_utc(now_utc or _now())
    today = business_date_for(now, settings.business_timezone, parse_day_start(settings.business_day_start_time))
    overrides = remaining_usage_overrides or {}
    if overrides and override_business_date is not None and override_business_date != today:
        raise ApiError(409, "today_override_expired", "Today's usage override belongs to a different business day. Use the default or enter a new remaining usage estimate.")
    try:
        overrides = {iid: TypeAdapter(DemandQuantity).validate_python(value) for iid, value in overrides.items()}
    except ValidationError:
        raise ApiError(422, "invalid_remaining_usage", "Today's remaining usage must be non-negative with up to three decimals.")
    start, end = today + timedelta(days=1), today + timedelta(days=days)
    query = select(Ingredient).where(Ingredient.status == "active").order_by(Ingredient.name)
    if ingredient_id is not None:
        query = query.where(Ingredient.id == ingredient_id)
    ingredients = db.scalars(query).all()
    if ingredient_id is not None and not ingredients:
        raise ApiError(404, "ingredient_not_found", "Active ingredient not found.")
    ids = [ingredient.id for ingredient in ingredients]
    if any(iid not in ids for iid in overrides):
        raise ApiError(422, "invalid_override_ingredient", "A usage override must target an active ingredient included in this forecast.")
    states = {row.ingredient_id: row for row in db.scalars(select(IngredientStockState).where(IngredientStockState.ingredient_id.in_(ids)))}
    configs = {row.ingredient_id: row for row in db.scalars(select(IngredientDemandSetting).where(IngredientDemandSetting.ingredient_id.in_(ids)))}
    verified = {row["ingredient_id"]: row for row in verify_ledger(db)}
    missing_state_balances = dict(db.execute(select(InventoryTransaction.ingredient_id,
        func.sum(InventoryTransaction.quantity_delta)).where(InventoryTransaction.ingredient_id.in_(
        [iid for iid in ids if iid not in states])).group_by(InventoryTransaction.ingredient_id)).all())
    links = db.scalars(select(SupplierIngredient).where(SupplierIngredient.ingredient_id.in_(ids))).all()
    order_rows = db.execute(select(PurchaseOrder, PurchaseOrderItem, Supplier)
        .join(PurchaseOrderItem, PurchaseOrderItem.order_id == PurchaseOrder.id)
        .join(Supplier, Supplier.id == PurchaseOrder.supplier_id)
        .where(PurchaseOrderItem.ingredient_id.in_(ids)).order_by(PurchaseOrder.id)).all()
    # Historical orders keep their supplier even after a catalog reassignment.
    supplier_ids = {link.supplier_id for link in links} | {supplier.id for _, _, supplier in order_rows}
    rules = db.scalars(select(SupplierScheduleRule).where(SupplierScheduleRule.supplier_id.in_(supplier_ids), SupplierScheduleRule.is_active.is_(True))).all()
    exceptions = db.scalars(select(SupplierCalendarException).where(SupplierCalendarException.supplier_id.in_(supplier_ids), SupplierCalendarException.is_active.is_(True))).all()
    suppliers = {row.id: row for row in db.scalars(select(Supplier).where(Supplier.id.in_(supplier_ids)))}
    # Compute each supplier's cutoff calendar once, then share it across its ingredients.
    opportunities = opportunities_by_supplier if opportunities_by_supplier is not None else {sid: compute_opportunities(db, supplier_id=sid, now_utc=now,
                     horizon_days=max(days + 7, 21), business_tz=settings.business_timezone)
                     for sid, supplier in suppliers.items() if supplier.status == "active"}
    incoming = defaultdict(list)
    for order, line, supplier in order_rows:
        incoming[line.ingredient_id].append((order, line, supplier))
    partial_evidence = _partial_confirmation_evidence(db, order_rows, now)
    result = []
    for ingredient in ingredients:
        iid = ingredient.id
        state, config, check = states.get(iid), configs.get(iid), verified.get(iid)
        consistent = bool(check and check["consistent"])
        ledger_balance = check["ledger_balance"] if check else missing_state_balances.get(iid, ZERO)
        actual = ledger_balance if consistent else None
        confidence = count_confidence(state, now_utc=now,
            high_hours=settings.count_high_freshness_hours, medium_hours=settings.count_medium_freshness_hours)
        warnings = list(confidence["warnings"])
        baseline_valid = consistent
        if not consistent:
            warnings.append("Ledger and cached stock do not agree, or stock state is missing. Projections are unknown; no automatic repair was made.")
        if state and state.last_transaction_at and as_utc(state.last_transaction_at) > now:
            baseline_valid = False
            actual = None
            warnings.append("Stock snapshot is newer than the forecast timestamp; historical forecasting is unsupported.")
        if config is None:
            config_state = "missing"; warnings.append("Demand settings are not configured.")
        elif not config.enabled:
            config_state = "disabled"; warnings.append("Forecast disabled: saved demand values are excluded.")
        else:
            complete = all(getattr(config, day) is not None for day in WEEKDAYS) and config.safety_stock is not None
            config_state = "enabled" if complete else "incomplete"
            if not complete: warnings.append("Demand settings are incomplete; missing values are not zero.")
        configured_today = getattr(config, WEEKDAYS[today.weekday()]) if config and config.enabled else None
        if config is None or not config.enabled:
            today_usage, today_source = None, "disabled" if config else "missing"
        elif iid in overrides:
            today_usage, today_source = overrides[iid], "override"
        else:
            today_usage = configured_today
            today_source = "full_day_upper_bound" if today_usage is not None else "missing"
        if today_usage is None and config and config.enabled:
            warnings.append("Today's remaining usage is unknown; future opening balances are unknown until an explicit estimate is supplied.")
        trusted, uncertain = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO)
        deliveries, outstanding, excluded = [], ZERO, ZERO
        for order, line, supplier in incoming[iid]:
            pending = max(line.quantity_ordered - line.quantity_received, ZERO)
            if pending == 0:
                continue  # already accepted goods are in actual stock, never incoming twice
            date = order.expected_delivery_date
            applicable = [r for r in rules if r.supplier_id == supplier.id and r.ingredient_id in (None, iid)]
            date_exceptions = [e for e in exceptions if e.supplier_id == supplier.id and e.date == date]
            cancelled = any(e.type == "delivery_cancelled" for e in date_exceptions)
            extra = any(e.type == "extra_delivery" for e in date_exceptions)
            off_schedule = bool(date and applicable and not extra and all(r.delivery_weekday != date.weekday() for r in applicable))
            conservative, expected, reasons = delivery_eligibility(status=order.status, expected_date=date,
                today=today, start=start, end=end, cancelled=cancelled, off_schedule=off_schedule,
                remainder_confirmed=_matching_confirmation(partial_evidence.get(order.id), order, line))
            if any(e.type == "ordering_suspended" for e in date_exceptions):
                reasons.append("Ordering is suspended on this date; the existing placed delivery is retained unless separately cancelled.")
            if order.status in PLACED_STATUSES:
                outstanding += pending
                if not expected: excluded += pending
            if conservative: trusted[date] += pending
            elif expected: uncertain[date] += pending
            delivery = {"order_id": order.id, "order_item_id": line.id, "supplier_id": supplier.id, "supplier_name": supplier.name,
                        "status": order.status, "expected_date": date, "ordered_quantity": line.quantity_ordered,
                        "accepted_received_quantity": line.quantity_received, "outstanding_quantity": pending,
                        "conservative_eligible": conservative, "expected_eligible": expected,
                        "uncertain": expected and not conservative, "reasons": reasons}
            deliveries.append(delivery)
            if order.status in PLACED_STATUSES:
                warnings.extend(f"Order #{order.id}: {reason}" for reason in reasons)
        cutoffs, seen = [], set()
        for link in links:
            if link.ingredient_id != iid: continue
            selected = [o for o in opportunities.get(link.supplier_id, []) if
                        o["ingredient_id"] in (None, iid) and o["status"] in ("open", "upcoming")
                        and o["delivery_date"] >= start]
            for opportunity in sorted(selected, key=lambda row: row["cutoff_at"])[:3]:
                key = (link.supplier_id, opportunity["delivery_date"], opportunity["cutoff_at"])
                if key in seen: continue
                seen.add(key)
                cutoffs.append({"supplier_id": link.supplier_id, "supplier_name": opportunity["supplier_name"],
                                "is_primary": link.supplier_id == ingredient.primary_supplier_id,
                                "delivery_date": opportunity["delivery_date"], "cutoff_at": opportunity["cutoff_at"],
                                "timezone": opportunity["timezone"], "status": opportunity["status"],
                                "exception_type": opportunity["exception"]["type"] if opportunity["exception"] else None})
        conservative = _walk(today=today, start=start, days=days, starting=actual, config=config, trusted=trusted,
            uncertain=uncertain, scenario="conservative", confidence=confidence["level"], baseline_valid=baseline_valid, today_usage=today_usage)
        expected = _walk(today=today, start=start, days=days, starting=actual, config=config, trusted=trusted,
            uncertain=uncertain, scenario="expected", confidence=confidence["level"], baseline_valid=baseline_valid, today_usage=today_usage)
        safety = config.safety_stock if config else None
        depends = uncertain_dependency(conservative, expected, safety)
        result.append({"ingredient_id": iid, "ingredient_name": ingredient.name, "unit": ingredient.unit.abbreviation,
                       "actual_available": actual, "cached_balance": state.current_quantity if state else None,
                       "ledger_balance": ledger_balance, "ledger_consistent": consistent,
                       "configuration_state": config_state, "safety_stock": safety, "confidence": confidence,
                       "today_configured_usage": configured_today, "today_remaining_usage": today_usage, "today_usage_source": today_source,
                       "warnings": list(dict.fromkeys(warnings)), "open_outstanding_quantity": outstanding,
                       "excluded_open_quantity": excluded, "incoming": deliveries,
                       "upcoming_cutoffs": sorted(cutoffs, key=lambda row: row["cutoff_at"]),
                       "conservative": conservative, "expected": expected, "depends_on_uncertain": depends})
    return {"as_of": now, "as_of_local": now.astimezone(ZoneInfo(settings.business_timezone)),
            "business_timezone": settings.business_timezone, "business_date": today,
            "start_date": start, "end_date": end, "horizon_days": days,
            "baseline_policy": "today_remaining_usage_then_future_days", "delivery_timing": "unknown_arrival_time_with_pre_delivery_risk",
            "assumptions": [
                "Today's preview starts from actual stock now. Remaining usage defaults to the configured full-day estimate as a conservative upper bound; override it, including zero after operations, when appropriate. Today's projected close becomes tomorrow's opening.",
                "Arrival times are unknown. The closing projection includes eligible outstanding deliveries, while the pre-delivery balance deducts demand before incoming and exposes possible shortage risk. Scheduled stock is not physically available until received.",
                "Negative projected balances carry cumulative unmet demand forward; they are not negative physical inventory.",
                "Count confidence describes the as-of physical count, independently of availability. No future verification is assumed.",
            ], "ingredients": result, "summary": summarize_forecast(result)}
