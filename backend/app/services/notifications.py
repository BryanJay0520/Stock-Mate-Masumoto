"""Explicit, retryable business-condition evaluation for in-app notices (D43).

No GET or app startup invokes this service. It writes only notification tables.
Future schedulers may call ``evaluate`` with an injected UTC clock; the database
unique key and SQLite write-intent transaction serialize concurrent evaluations.
"""

import json
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import set_write_intent
from app.errors import ApiError
from app.models import (Ingredient, InventoryTransaction, NotificationCountRule,
                        NotificationEvent, NotificationSetting, NotificationUserState, NotificationWorkerState,
                        PurchaseOrder, PurchaseOrderItem, Supplier, SupplierIngredient)
from app.models.inventory import MODE_REMAINING, TYPE_RECONCILIATION
from app.models.ordering import OPEN_ORDER_STATUSES
from app.services.business_time import business_date_for, parse_day_start
from app.services.confidence import as_utc
from app.services.forecast import generate_forecast
from app.services.recommendations import generate_recommendations
from app.services.scheduling import compute_opportunities

DEFAULT_TIME = time(21, 30)
SETTING_FIELDS = ("daily_count_enabled", "order_cutoff_enabled", "supplier_contact_enabled",
                  "forecast_shortage_enabled", "verify_pending_order_enabled", "pending_delivery_enabled")
PRIORITY_ORDER = {"critical": 0, "high": 1, "normal": 2, "informational": 3}
TYPE_ORDER = {"forecast_shortage": 0, "order_cutoff": 1, "supplier_contact": 2,
              "verify_pending_order": 3, "pending_delivery": 4, "daily_count": 5}
ACTIVE = "active"


class WorkerLeaseLost(RuntimeError):
    """A worker may not evaluate after another process owns its lease."""


def _utc(now):
    return as_utc(now or datetime.now(timezone.utc))


def _quantity(value, unit):
    return f"{value:.3f} {unit}" if value is not None else "Unknown"


def _setting_view(db):
    row = db.get(NotificationSetting, 1)
    enabled = {name: getattr(row, name) if row else True for name in SETTING_FIELDS}
    ids = db.scalars(select(NotificationCountRule.ingredient_id).where(NotificationCountRule.enabled.is_(True)).order_by(NotificationCountRule.ingredient_id)).all()
    return {**enabled, "daily_count_time": (row.daily_count_time if row else DEFAULT_TIME),
            "automatic_evaluation_enabled": row.automatic_evaluation_enabled if row else False,
            "evaluation_interval_seconds": row.evaluation_interval_seconds if row else 60,
            "daily_count_ingredient_ids": list(ids)}


def get_settings(db):
    return _setting_view(db)


def update_settings(db, data):
    if "evaluation_interval_seconds" in data and not 60 <= data["evaluation_interval_seconds"] <= 900:
        raise ApiError(422, "invalid_evaluation_interval", "Choose an interval from 60 to 900 seconds.")
    now = _utc(None)
    set_write_intent(True)
    try:
        row = db.get(NotificationSetting, 1)
        if row is None:
            row = NotificationSetting(id=1, daily_count_time=DEFAULT_TIME, updated_at=now,
                                      **{name: True for name in SETTING_FIELDS})
            db.add(row)
        for name in (*SETTING_FIELDS, "daily_count_time", "automatic_evaluation_enabled", "evaluation_interval_seconds"):
            if name in data:
                setattr(row, name, data[name])
        if "daily_count_ingredient_ids" in data:
            ids = set(data["daily_count_ingredient_ids"])
            existing = set(db.scalars(select(Ingredient.id).where(Ingredient.id.in_(ids), Ingredient.status == "active")).all())
            if ids != existing:
                raise ApiError(422, "invalid_count_ingredient", "Select active ingredients for count reminders.")
            rules = {r.ingredient_id: r for r in db.scalars(select(NotificationCountRule)).all()}
            for iid in ids | set(rules):
                if iid in rules:
                    rules[iid].enabled = iid in ids
                else:
                    db.add(NotificationCountRule(ingredient_id=iid, enabled=True))
        row.updated_at = now
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        set_write_intent(False)
    return _setting_view(db)


def _candidate(key, kind, priority, title, message, *, business_date=None, action_at=None,
               ingredient_id=None, supplier_id=None, purchase_order_id=None,
               delivery_cycle_key=None, target="#/notifications", details=None):
    return {"condition_key": key, "type": kind, "priority": priority, "title": title,
            "message": message, "business_date": business_date, "action_at": action_at,
            "ingredient_id": ingredient_id, "supplier_id": supplier_id,
            "purchase_order_id": purchase_order_id, "delivery_cycle_key": delivery_cycle_key,
            "navigation_target": target, "details_json": json.dumps(details or {}, default=str, sort_keys=True)}


def _daily_count(db, now, settings, policy, today):
    if not policy["daily_count_enabled"]:
        return None
    tz = ZoneInfo(settings.business_timezone)
    local_day = today + (timedelta(days=1) if policy["daily_count_time"] < parse_day_start(settings.business_day_start_time) else timedelta())
    reminder = datetime.combine(local_day, policy["daily_count_time"], tzinfo=tz).astimezone(timezone.utc)
    if now < reminder:
        return None
    ids = policy["daily_count_ingredient_ids"]
    if not ids:
        return None
    names = dict(db.execute(select(Ingredient.id, Ingredient.name).where(Ingredient.id.in_(ids), Ingredient.status == "active")).all())
    counted = set(db.scalars(select(InventoryTransaction.ingredient_id).where(
        InventoryTransaction.ingredient_id.in_(names), InventoryTransaction.business_date == today,
        InventoryTransaction.type == TYPE_RECONCILIATION, InventoryTransaction.mode == MODE_REMAINING)).all())
    missing = [names[iid] for iid in sorted(names, key=lambda iid: names[iid]) if iid not in counted]
    if not missing:
        return None
    return _candidate(f"daily-count:{today}", "daily_count", "normal",
        "Complete today's inventory counts",
        "Please record today's remaining inventory for: " + ", ".join(missing) + ".",
        business_date=today, action_at=reminder, target="#/entry", details={"missing_ingredients": missing})


def _shortage_candidates(forecast, today):
    result, unknown = [], set()
    for item in forecast["ingredients"]:
        iid = item["ingredient_id"]
        if item["configuration_state"] != "enabled" or not item["ledger_consistent"]:
            unknown.add(iid)
        rows = [item["conservative"]["today"], *item["conservative"]["days"]]
        risks = [row for row in rows if row["closing_balance"] is not None and row["closing_balance"] < 0 or row["pre_delivery_shortage_risk"]]
        if not risks:
            continue
        first = risks[0]
        closing = first["closing_balance"] is not None and first["closing_balance"] < 0
        amount = -first["closing_balance"] if closing else first["pre_delivery_shortage"]
        kind = "projected closing shortage" if closing else "pre-delivery shortage risk"
        delivery = next((row for row in item["incoming"] if row["expected_date"] == first["date"] and row["expected_eligible"]), None)
        next_supply = next((row for row in item["incoming"] if row["expected_eligible"] and row["expected_date"] >= first["date"]), None)
        next_cycle = next((row for row in item["upcoming_cutoffs"] if row["delivery_date"] >= first["date"]), None)
        context = (f" An eligible outstanding delivery is dated {delivery['expected_date']}; its arrival time is unknown." if delivery
                   else f" An outstanding delivery is expected {next_supply['expected_date']}; it is not available until received." if next_supply
                   else f" The next scheduled ordering opportunity is for delivery {next_cycle['delivery_date']}; this is not physical stock." if next_cycle else "")
        confidence = item["confidence"]["level"]
        count_note = f" Count confidence: {confidence}{' (stale)' if item['confidence']['stale'] else ''}."
        warning = " Verify a fresh physical count." if item["confidence"]["stale"] or confidence == "low" else " Verify physical stock."
        uncertain = " The expected scenario depends on unconfirmed incoming; verify the supplier." if item["depends_on_uncertain"] else ""
        earlier = " A regular future order cannot repair an earlier shortage." if next_cycle and first["date"] < next_cycle["delivery_date"] else ""
        message = (f"{item['ingredient_name']}: possible {kind} of {_quantity(amount, item['unit'])} on {first['date']}. "
                   f"Actual available stock now is {_quantity(item['actual_available'], item['unit'])}; projected balances are estimates."
                   + context + count_note + warning + uncertain + " Review the forecast and urgent supply options." + earlier)
        result.append(_candidate(f"shortage:{iid}:{first['date']}", "forecast_shortage",
            "critical" if first["date"] <= today + timedelta(days=2) else "high",
            f"{item['ingredient_name']} — {kind}", message, ingredient_id=iid,
            business_date=first["date"], target=f"#/forecast?ingredient_id={iid}",
            details={"risk_kind": kind, "shortage_quantity": str(amount), "unit": item["unit"],
                     "actual_available": str(item["actual_available"]), "count_confidence": confidence,
                     "delivery_order_id": delivery["order_id"] if delivery else None}))
    return result, unknown


def _order_candidates(advice, now):
    groups, verify = {}, []
    for row in advice["recommendations"]:
        opp, supplier = row["opportunity"], row["primary_supplier"]
        if row["verify_pending_order"]:
            relevant = [o for o in row["existing_orders"] if o["projection_phase"] in ("starting_projection", "delivery_cycle")]
            confirmed = sum((o["outstanding_quantity"] for o in relevant if o["conservative_eligible"]), Decimal(0))
            uncertain = sum((o["outstanding_quantity"] for o in relevant if o["uncertain"]), Decimal(0))
            for order in row["existing_orders"]:
                if not order["uncertain"] or order["projection_phase"] not in ("starting_projection", "delivery_cycle"):
                    continue
                oid = order["order_id"]
                verify.append(_candidate(f"verify-order:{row['ingredient_id']}:{oid}:{order['expected_date']}",
                    "verify_pending_order", "normal", f"Verify order #{oid} with {order['supplier_name']}",
                    f"An existing unconfirmed order may cover {row['ingredient_name']}. Confirmed incoming: {_quantity(confirmed, row['unit'])}; uncertain incoming: {_quantity(uncertain, row['unit'])}. Verify supplier confirmation before another purchase. Draft orders are not supply.",
                    ingredient_id=row["ingredient_id"], supplier_id=order["supplier_id"], purchase_order_id=oid,
                    business_date=order["expected_date"], target=f"#/orders?order_id={oid}",
                    details={"confirmed_quantity": str(confirmed), "uncertain_quantity": str(uncertain), "unit": row["unit"]}))
        if not opp or not supplier or opp["status"] not in ("open", "upcoming") or opp["reminder_at"] is None:
            continue
        if now < as_utc(opp["reminder_at"]) or now > as_utc(opp["cutoff_at"]):
            continue
        calc = row["conservative"]
        required = calc["required_additional_quantity"]
        urgent_manual = calc["shortage_before_delivery"] > 0
        if not urgent_manual and (required is None or required <= 0):
            continue
        key = (supplier["id"], opp["delivery_date"])
        group = groups.setdefault(key, {"supplier": supplier, "opportunity": opp, "items": [], "ingredient_ids": []})
        if as_utc(opp["cutoff_at"]) < as_utc(group["opportunity"]["cutoff_at"]):
            group["opportunity"] = opp
        if urgent_manual:
            detail = f"{row['ingredient_name']}: earlier shortage risk; verify physical stock and arrange urgent supply. No reliable regular-order quantity."
        elif row["verify_pending_order"]:
            potential = calc["purchasing"]["recommended_quantity"]
            detail = (f"{row['ingredient_name']}: potential additional {_quantity(potential, row['unit'])}; verify pending order before buying."
                if potential is not None else
                f"{row['ingredient_name']}: potential raw need {_quantity(required, row['unit'])}; purchasing configuration needs review. Verify pending order before buying.")
        else:
            purchase = calc["purchasing"]
            if purchase["recommended_quantity"] is None:
                detail = f"{row['ingredient_name']}: raw additional need {_quantity(required, row['unit'])}; review purchasing package configuration before ordering."
            else:
                packs = f"{int(purchase['recommended_package_count'])} {purchase['package_name'] or 'packages'} / " if purchase["recommended_package_count"] is not None else ""
                detail = f"{row['ingredient_name']}: additional {packs}{_quantity(purchase['recommended_quantity'], row['unit'])}."
        group["items"].append(detail)
        group["ingredient_ids"].append(row["ingredient_id"])
    result = []
    for (sid, day), group in groups.items():
        opp, supplier = group["opportunity"], group["supplier"]
        cutoff = as_utc(opp["cutoff_at"])
        local_cutoff = cutoff.astimezone(ZoneInfo(opp["timezone"]))
        cycle = f"{sid}:{day}"
        result.append(_candidate(f"order-cutoff:{cycle}", "order_cutoff",
            "high" if cutoff - now <= timedelta(hours=24) else "normal",
            f"Order deadline — {supplier['name']} for {day}",
            f"Delivery {day}; cutoff {local_cutoff:%Y-%m-%d %H:%M} ({opp['timezone']}). " + " ".join(group["items"]),
            supplier_id=sid, business_date=day, action_at=cutoff, delivery_cycle_key=cycle,
            target="#/planner", details={"ingredients": group["items"], "ingredient_ids": group["ingredient_ids"],
                                        "cutoff_at": cutoff.isoformat()}))
    return result, verify


def _contact_candidates(db, now, settings):
    links = db.execute(select(SupplierIngredient.supplier_id, SupplierIngredient.ingredient_id)
                       .join(Ingredient, Ingredient.id == SupplierIngredient.ingredient_id)
                       .where(Ingredient.status == "active")).all()
    by_supplier = {}
    for sid, iid in links:
        by_supplier.setdefault(sid, set()).add(iid)
    suppliers = {row.id: row for row in db.scalars(select(Supplier).where(Supplier.id.in_(by_supplier), Supplier.status == "active"))}
    result = []
    for sid, supplier in suppliers.items():
        cycles = compute_opportunities(db, supplier_id=sid, now_utc=now, horizon_days=30,
                                       business_tz=settings.business_timezone)
        seen = set()
        for opp in cycles:
            contact = opp["contact"]
            if not contact or not contact["required"] or not contact["deadline_at"]:
                continue
            if opp["ingredient_id"] is not None and opp["ingredient_id"] not in by_supplier[sid]:
                continue
            deadline = as_utc(contact["deadline_at"])
            supplier_today = now.astimezone(ZoneInfo(opp["timezone"])).date()
            if opp["delivery_date"] < supplier_today or opp["status"] in ("missed", "unavailable_holiday") or not deadline - timedelta(hours=24) <= now <= deadline:
                continue
            key = (sid, opp["delivery_date"], contact["action"], deadline)
            if key in seen:
                continue
            seen.add(key)
            local = deadline.astimezone(ZoneInfo(opp["timezone"]))
            method = contact["action"].replace("_", " ")
            info = "; ".join(x for x in (supplier.contact_name, supplier.phone, supplier.email) if x)
            instructions = opp["special_instructions"] or supplier.ordering_procedures or "No special instructions recorded."
            result.append(_candidate(f"supplier-contact:{sid}:{opp['delivery_date']}:{contact['action']}:{deadline.isoformat()}",
                "supplier_contact", "high" if deadline - now <= timedelta(hours=12) else "normal",
                f"{method.capitalize()} {supplier.name} for {opp['delivery_date']}",
                f"Required {method} by {local:%Y-%m-%d %H:%M} ({opp['timezone']}) for delivery {opp['delivery_date']}. {instructions} Contact: {info or 'Review supplier details'}. Contact is not recorded as completed by reading this reminder.",
                supplier_id=sid, business_date=opp["delivery_date"], action_at=deadline,
                delivery_cycle_key=f"{sid}:{opp['delivery_date']}", target=f"#/suppliers?supplier_id={sid}",
                details={"contact_method": method, "instructions": instructions, "contact_information": info}))
    return result


def _delivery_candidates(db, today):
    rows = db.execute(select(PurchaseOrder, PurchaseOrderItem, Ingredient, Supplier)
        .join(PurchaseOrderItem, PurchaseOrderItem.order_id == PurchaseOrder.id)
        .join(Ingredient, Ingredient.id == PurchaseOrderItem.ingredient_id)
        .join(Supplier, Supplier.id == PurchaseOrder.supplier_id)
        .where(PurchaseOrder.status.in_(OPEN_ORDER_STATUSES))).all()
    grouped = {}
    for order, line, ingredient, supplier in rows:
        if order.status == "draft":
            continue
        outstanding = max(line.quantity_ordered - line.quantity_received, Decimal(0))
        if outstanding <= 0 or (order.expected_delivery_date > today and order.status != "delayed"):
            continue
        group = grouped.setdefault(order.id, {"order": order, "supplier": supplier, "lines": []})
        group["lines"].append(f"{_quantity(outstanding, ingredient.unit.abbreviation)} {ingredient.name}")
    result = []
    for oid, group in grouped.items():
        order, supplier = group["order"], group["supplier"]
        overdue = (today - order.expected_delivery_date).days
        state = "delayed" if order.status == "delayed" else "overdue" if overdue > 0 else "due"
        result.append(_candidate(f"pending-delivery:{oid}:{order.expected_delivery_date}", "pending_delivery",
            "high" if overdue >= 2 or order.status == "delayed" else "normal",
            f"{state.capitalize()} delivery — order #{oid}",
            f"{supplier.name}, order #{oid}, expected {order.expected_delivery_date}: {', '.join(group['lines'])} remain outstanding after accepted receipts. Check the expected date with the supplier. No new arrival date is assumed.",
            supplier_id=supplier.id, purchase_order_id=oid, business_date=order.expected_delivery_date,
            target=f"#/orders?order_id={oid}", details={"outstanding": group["lines"], "supplier_name": supplier.name}))
    return result


def _persist(db, candidates, now, today, policy, unknown_shortage, unknown_order, covered_order):
    existing = db.scalars(select(NotificationEvent).order_by(NotificationEvent.id)).all()
    by_condition = {}
    for event in existing:
        by_condition.setdefault(event.condition_key, []).append(event)
    desired = {row["condition_key"]: row for row in candidates}
    created = updated = transitioned = resolved = expired = superseded = 0
    for key, candidate in desired.items():
        episodes = by_condition.get(key, [])
        active = next((row for row in reversed(episodes) if row.status == ACTIVE), None)
        if active is None:
            suffix = f":{len(episodes) + 1}" if episodes else ""
            event = NotificationEvent(**candidate, dedupe_key=key + suffix, status=ACTIVE,
                                      created_at=now, updated_at=now)
            db.add(event)
            created += 1
        else:
            changed = any(getattr(active, name) != value for name, value in candidate.items())
            if changed:
                for name, value in candidate.items():
                    setattr(active, name, value)
                active.updated_at = now
                for state in db.scalars(select(NotificationUserState).where(NotificationUserState.notification_id == active.id)):
                    state.read_at = None
                    state.acknowledged_at = None
                updated += 1
    for event in existing:
        if event.status != ACTIVE or event.condition_key in desired:
            continue
        if event.type == "forecast_shortage" and event.ingredient_id in unknown_shortage and not any(
                c["type"] == "forecast_shortage" and c["ingredient_id"] == event.ingredient_id for c in candidates):
            continue  # missing inputs are unknown, not proof of resolution
        if (event.type == "order_cutoff" and event.condition_key in unknown_order
                and set(json.loads(event.details_json).get("ingredient_ids", [])) & unknown_order[event.condition_key]
                and event.action_at and now <= as_utc(event.action_at)):
            continue
        if event.type == "daily_count" and event.business_date == today and now < as_utc(event.action_at):
            continue
        if event.action_at and event.type in ("order_cutoff", "supplier_contact") and now > as_utc(event.action_at):
            event.status = "expired"
        elif event.type == "daily_count" and event.business_date != today:
            event.status = "expired"
        elif event.type == "daily_count" and not policy["daily_count_enabled"]:
            event.status = "superseded"
        elif not policy.get({"forecast_shortage": "forecast_shortage_enabled",
                             "verify_pending_order": "verify_pending_order_enabled",
                             "pending_delivery": "pending_delivery_enabled"}.get(event.type, ""), True):
            event.status = "superseded"
        elif event.type in ("order_cutoff", "supplier_contact") and not policy[{"order_cutoff": "order_cutoff_enabled", "supplier_contact": "supplier_contact_enabled"}[event.type]]:
            event.status = "superseded"
        elif event.type == "supplier_contact":
            # No completion tracking exists. A removed/blocked rule supersedes
            # the reminder; neither reading nor acknowledgement caused this.
            event.status = "superseded"
        elif event.type == "order_cutoff":
            prior_ids = set(json.loads(event.details_json).get("ingredient_ids", []))
            event.status = "resolved" if prior_ids and prior_ids <= covered_order.get(event.condition_key, set()) else "superseded"
        elif event.type == "forecast_shortage" and any(c["type"] == "forecast_shortage" and c["ingredient_id"] == event.ingredient_id for c in candidates):
            event.status = "superseded"
        elif event.type == "verify_pending_order" and any(c["type"] == "verify_pending_order" and c["purchase_order_id"] == event.purchase_order_id for c in candidates):
            event.status = "superseded"
        elif event.type == "pending_delivery" and any(c["type"] == "pending_delivery" and c["purchase_order_id"] == event.purchase_order_id for c in candidates):
            event.status = "superseded"
        else:
            event.status = "resolved"
        event.closed_at = now
        event.resolved_at = now if event.status == "resolved" else None
        event.updated_at = now
        transitioned += 1
        if event.status == "resolved":
            resolved += 1
        elif event.status == "expired":
            expired += 1
        elif event.status == "superseded":
            superseded += 1
    return {"created": created, "updated": updated, "transitioned": transitioned,
            "resolved": resolved, "expired": expired, "superseded": superseded}


def _evaluate_once(db: Session, *, settings: Settings, now_utc=None,
                   worker_owner_token=None, minimum_interval_seconds=None):
    now = _utc(now_utc)
    set_write_intent(True)
    try:
        policy = _setting_view(db)
        if worker_owner_token is not None:
            worker = db.get(NotificationWorkerState, 1)
            if (worker is None or worker.owner_token != worker_owner_token or
                    worker.lease_expires_at is None or as_utc(worker.lease_expires_at) <= now):
                raise WorkerLeaseLost("Worker lease is no longer owned by this process.")
            setting_row = db.get(NotificationSetting, 1)
            if setting_row is None or not setting_row.automatic_evaluation_enabled:
                db.rollback()
                return None
            interval = minimum_interval_seconds or setting_row.evaluation_interval_seconds
            if (worker.last_success_at is not None and setting_row.last_evaluated_at is not None and
                    now < as_utc(setting_row.last_evaluated_at) + timedelta(seconds=interval)):
                db.rollback()
                return None
        today = business_date_for(now, settings.business_timezone, parse_day_start(settings.business_day_start_time))
        candidates = []
        count = _daily_count(db, now, settings, policy, today)
        if count:
            candidates.append(count)
        forecast = None
        if policy["forecast_shortage_enabled"] or policy["order_cutoff_enabled"] or policy["verify_pending_order_enabled"]:
            forecast = generate_forecast(db, settings=settings, days=30, now_utc=now)
        unknown_shortage = set()
        unknown_order = {}
        covered_order = {}
        if policy["forecast_shortage_enabled"]:
            alerts, unknown_shortage = _shortage_candidates(forecast, today)
            candidates.extend(alerts)
        if policy["order_cutoff_enabled"] or policy["verify_pending_order_enabled"]:
            advice = generate_recommendations(db, settings=settings, now_utc=now, forecast_snapshot=forecast)
            for row in advice["recommendations"]:
                opp, supplier = row["opportunity"], row["primary_supplier"]
                if opp and supplier and row["conservative"]["required_additional_quantity"] is None and not row["conservative"]["shortage_before_delivery"]:
                    unknown_order.setdefault(f"order-cutoff:{supplier['id']}:{opp['delivery_date']}", set()).add(row["ingredient_id"])
                if opp and supplier and row["conservative"]["required_additional_quantity"] == 0:
                    covered_order.setdefault(f"order-cutoff:{supplier['id']}:{opp['delivery_date']}", set()).add(row["ingredient_id"])
            orders, verification = _order_candidates(advice, now)
            if policy["order_cutoff_enabled"]:
                candidates.extend(orders)
            if policy["verify_pending_order_enabled"]:
                candidates.extend(verification)
        if policy["supplier_contact_enabled"]:
            candidates.extend(_contact_candidates(db, now, settings))
        if policy["pending_delivery_enabled"]:
            candidates.extend(_delivery_candidates(db, today))
        result = _persist(db, candidates, now, today, policy, unknown_shortage, unknown_order, covered_order)
        setting_row = db.get(NotificationSetting, 1)
        if setting_row is None:
            setting_row = NotificationSetting(id=1, daily_count_time=DEFAULT_TIME, updated_at=now,
                                              **{name: True for name in SETTING_FIELDS})
            db.add(setting_row)
        setting_row.last_evaluated_at = now
        db.commit()
        return {**result, "evaluated_at": now, "business_date": today, "active_conditions": len({row["condition_key"] for row in candidates})}
    except Exception:
        db.rollback()
        raise
    finally:
        set_write_intent(False)


def evaluate(db: Session, *, settings: Settings, now_utc=None,
             worker_owner_token=None, minimum_interval_seconds=None):
    """A concurrent writer may win the unique-key insert; retry the full snapshot."""
    now = _utc(now_utc)
    for attempt in range(3):
        try:
            return _evaluate_once(db, settings=settings, now_utc=now,
                                  worker_owner_token=worker_owner_token,
                                  minimum_interval_seconds=minimum_interval_seconds)
        except (IntegrityError, OperationalError):
            if attempt == 2:
                raise


def _serialize(event, state):
    return {"id": event.id, "type": event.type, "priority": event.priority, "status": event.status,
            "title": event.title, "message": event.message, "details": json.loads(event.details_json),
            "ingredient_id": event.ingredient_id, "supplier_id": event.supplier_id,
            "purchase_order_id": event.purchase_order_id, "delivery_cycle_key": event.delivery_cycle_key,
            "business_date": event.business_date, "action_at": as_utc(event.action_at) if event.action_at else None,
            "navigation_target": event.navigation_target, "created_at": as_utc(event.created_at),
            "updated_at": as_utc(event.updated_at), "closed_at": as_utc(event.closed_at) if event.closed_at else None,
            "resolved_at": as_utc(event.resolved_at) if event.resolved_at else None,
            "read_at": as_utc(state.read_at) if state and state.read_at else None,
            "acknowledged_at": as_utc(state.acknowledged_at) if state and state.acknowledged_at else None}


def list_notifications(db, *, user_id, view="active", limit=25, offset=0):
    joined = (NotificationUserState.notification_id == NotificationEvent.id) & (NotificationUserState.user_id == user_id)
    condition = NotificationEvent.status == ACTIVE if view != "all" else True
    if view == "unread":
        condition = condition & NotificationUserState.read_at.is_(None)
    elif view == "urgent":
        condition = condition & NotificationEvent.priority.in_(("critical", "high"))
    elif view == "history":
        condition = NotificationEvent.status != ACTIVE
    total = db.scalar(select(func.count(NotificationEvent.id)).outerjoin(NotificationUserState, joined).where(condition)) or 0
    priority = case(*((NotificationEvent.priority == name, order) for name, order in PRIORITY_ORDER.items()), else_=4)
    task = case(*((NotificationEvent.type == name, order) for name, order in TYPE_ORDER.items()), else_=6)
    rows = db.execute(select(NotificationEvent, NotificationUserState).outerjoin(NotificationUserState, joined)
        .where(condition).order_by(priority, task, NotificationEvent.created_at.desc(), NotificationEvent.id.desc())
        .limit(limit).offset(offset)).all()
    setting = db.get(NotificationSetting, 1)
    return {"items": [_serialize(e, s) for e, s in rows], "total": total,
            "limit": limit, "offset": offset,
            "evaluated_at": as_utc(setting.last_evaluated_at) if setting and setting.last_evaluated_at else None}


def unread_count(db, *, user_id):
    joined = (NotificationUserState.notification_id == NotificationEvent.id) & (NotificationUserState.user_id == user_id)
    count = db.scalar(select(func.count(NotificationEvent.id)).outerjoin(NotificationUserState, joined)
        .where(NotificationEvent.status == ACTIVE, NotificationUserState.read_at.is_(None))) or 0
    return {"unread_count": count}


def mark_read(db, *, user_id, notification_id):
    event = db.get(NotificationEvent, notification_id)
    if event is None:
        raise ApiError(404, "notification_not_found", "Notification not found.")
    state = db.scalar(select(NotificationUserState).where(NotificationUserState.notification_id == notification_id,
                                                         NotificationUserState.user_id == user_id))
    if state is None:
        state = NotificationUserState(notification_id=notification_id, user_id=user_id)
        db.add(state)
    if state.read_at is None:
        state.read_at = _utc(None)
    db.commit()
    return _serialize(event, state)


def mark_all_read(db, *, user_id):
    active = db.scalars(select(NotificationEvent).where(NotificationEvent.status == ACTIVE)).all()
    states = {row.notification_id: row for row in db.scalars(select(NotificationUserState).where(NotificationUserState.user_id == user_id))}
    now = _utc(None)
    changed = 0
    for event in active:
        state = states.get(event.id)
        if state is None:
            db.add(NotificationUserState(notification_id=event.id, user_id=user_id, read_at=now))
            changed += 1
        elif state.read_at is None:
            state.read_at = now
            changed += 1
    db.commit()
    return {"marked_read": changed}


def acknowledge(db, *, user_id, notification_id):
    event = db.get(NotificationEvent, notification_id)
    if event is None:
        raise ApiError(404, "notification_not_found", "Notification not found.")
    if event.status != ACTIVE:
        raise ApiError(409, "notification_inactive", "Only active notifications can be acknowledged.")
    state = db.scalar(select(NotificationUserState).where(NotificationUserState.notification_id == notification_id,
                                                         NotificationUserState.user_id == user_id))
    if state is None:
        state = NotificationUserState(notification_id=notification_id, user_id=user_id)
        db.add(state)
    if state.acknowledged_at is None:
        state.acknowledged_at = _utc(None)
    db.commit()
    return _serialize(event, state)
