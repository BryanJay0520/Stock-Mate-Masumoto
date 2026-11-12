"""Read-only, time-phased order advice from forecast and schedule snapshots (D42)."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Ingredient, Supplier, SupplierIngredient
from app.services.confidence import as_utc
from app.services.forecast import generate_forecast, summarize_forecast
from app.services.scheduling import compute_opportunities

ZERO = Decimal("0.000")
PRECISION = Decimal("0.001")
MAX_QUANTITY = Decimal("99999999999.999")
SEARCH_DAYS = 30


def _now():
    return datetime.now(timezone.utc)


def purchase_rounding(required, link):
    """Counting packages are deliberately never consulted here."""
    warnings = []
    size = link.package_size if link else None
    minimum = link.min_order_quantity if link and link.min_order_quantity is not None else ZERO
    valid = size is not None and size.is_finite() and size > 0
    if not valid:
        warnings.append("Configure a positive purchasing package size for this supplier; no package size is assumed.")
    if not minimum.is_finite() or minimum < 0:
        warnings.append("Configure a non-negative supplier minimum order quantity.")
        valid = False
    quantity, packages = None, None
    if required is not None and valid:
        quantity = max(required, minimum) if required > 0 else ZERO
        if link.whole_packages_only:
            packages = (quantity / size).to_integral_value(rounding=ROUND_CEILING)
            quantity = packages * size
        else:
            quantity = quantity.quantize(PRECISION, rounding=ROUND_CEILING)
        if quantity > MAX_QUANTITY:
            warnings.append("Rounded purchase exceeds supported quantity precision; review the package configuration.")
            quantity, packages = None, None
            valid = False
    return {"package_size": size, "package_name": link.purchase_package_name if link else None,
            "whole_packages_only": bool(link and link.whole_packages_only),
            "minimum_order_quantity": minimum, "valid": valid,
            "recommended_package_count": packages, "recommended_quantity": quantity, "warnings": warnings}


def _supplier_info(supplier, link):
    if supplier is None:
        return None
    return {"id": supplier.id, "name": supplier.name, "status": supplier.status,
            "contact_name": supplier.contact_name, "phone": supplier.phone, "email": supplier.email,
            "contact_method": supplier.contact_method, "ordering_url": supplier.ordering_url,
            "ordering_procedures": supplier.ordering_procedures, "notes": supplier.notes,
            "purchasing": purchase_rounding(None, link)}


def _cycles(opportunities, iid, today, end):
    """D32 additive ingredient rules; dates and cutoff statuses come from scheduling."""
    candidates = [row for row in opportunities if row["ingredient_id"] in (None, iid)
                  and row["status"] in ("open", "upcoming") and today <= row["delivery_date"] <= end]
    unique = {}
    for row in sorted(candidates, key=lambda row: (row["delivery_date"], row["cutoff_at"], row["rule_id"] or 0)):
        unique.setdefault(row["delivery_date"], row)
    return list(unique.values())


def _calculate(item, scenario, opportunity, following, link):
    projection = item[scenario]
    rows = [projection["today"], *projection["days"]]
    start = opportunity["delivery_date"] if opportunity else None
    end = following["delivery_date"] - timedelta(days=1) if following else None
    before = [row for row in rows if start and row["date"] < start]
    cycle = [row for row in rows if start and end and start <= row["date"] <= end]
    starting_row = next((row for row in rows if row["date"] == start), None)
    starting = starting_row["opening_balance"] if starting_row else None
    safety = item["safety_stock"]
    missing = []
    if item["configuration_state"] == "disabled": missing.append("Forecasting is disabled for this ingredient.")
    if item["configuration_state"] == "missing": missing.append("Configure ingredient demand settings.")
    if safety is None: missing.append("Configure planning safety stock.")
    relevant = [row for row in rows if end and row["date"] <= end]
    for row in relevant:
        if row["estimated_consumption"] is None:
            missing.append(f"Configure remaining usage or {row['date']:%A} demand ({row['date']}).")
    if not opportunity: missing.append("No valid primary-supplier ordering opportunity exists within 30 days.")
    elif not following: missing.append("The following actionable delivery cannot be determined within 30 days; coverage is unknown.")
    if starting is None and opportunity:
        missing.append("The starting stock projection is unknown; verify ledger consistency and required demand inputs.")
    if not item["ledger_consistent"]: missing.append("Ledger and stock state do not reconcile; manual review is required.")
    prior_shortages = [row for row in before if row["closing_balance"] is not None and row["closing_balance"] < 0]
    prior_risks = [row for row in before if row["pre_delivery_shortage_risk"]]
    shortage = max((-row["closing_balance"] for row in prior_shortages), default=ZERO)
    first_shortage = prior_shortages[0]["date"] if prior_shortages else None
    same_day_risk = (max(-starting_row["pre_delivery_balance"], ZERO)
        if starting_row and starting_row["pre_delivery_balance"] is not None else None)
    cycle_risks = [row for row in cycle if row["pre_delivery_shortage_risk"]]
    risk_rows = {row["date"]: {"date": row["date"], "quantity": row["pre_delivery_shortage"],
                 "phase": "before_proposed_delivery" if start and row["date"] < start else "delivery_cycle"}
                 for row in [*prior_risks, *cycle_risks]}
    if same_day_risk is not None and same_day_risk > 0:
        risk_rows[start] = {"date": start, "quantity": same_day_risk, "phase": "proposed_delivery_day"}
    demand = sum((row["estimated_consumption"] for row in cycle), ZERO) if cycle and all(row["estimated_consumption"] is not None for row in cycle) else None
    target = demand + safety if demand is not None and safety is not None else None
    provisional = bool(prior_shortages or risk_rows)
    warnings = list(dict.fromkeys(missing))
    if prior_shortages:
        warnings.append(f"Projected shortage of {shortage:.3f} {item['unit']} before the next delivery. A regular order cannot resolve the earlier shortage.")
        warnings.append("Signed negative balances are unmet demand, not physical negative stock or outstanding customer obligations. Verify a fresh physical count and review operations before calculating a definitive order quantity.")
    if risk_rows:
        warnings.append("Arrival times are unknown. Demand before delivery may cause a shortage even when closing stock is positive; review timing and actual stock.")
    required = None
    path = []
    if not missing and not prior_shortages:
        balance, required = starting, ZERO
        for row in cycle:
            balance += row["eligible_incoming"] - row["estimated_consumption"]
            required = max(required, safety - balance, ZERO)
            path.append({"date": row["date"], "estimated_consumption": row["estimated_consumption"],
                         "eligible_incoming": row["eligible_incoming"], "closing_without_new_order": balance,
                         "closing_with_recommendation": None})
    rounded = purchase_rounding(required, link)
    warnings.extend(rounded["warnings"])
    if rounded["recommended_quantity"] is not None:
        for row in path:
            row["closing_with_recommendation"] = row["closing_without_new_order"] + rounded["recommended_quantity"]
    confirmed_cycle = sum((row["confirmed_incoming"] for row in cycle), ZERO)
    uncertain_cycle = sum((row["uncertain_incoming"] for row in cycle), ZERO)
    message = ("Existing confirmed orders already cover this delivery cycle." if required == 0 and confirmed_cycle > 0
               else "No additional order is needed under these assumptions." if required == 0
               else "Review the additional requirement and purchasing package rounding." if required is not None
               else "A reliable order quantity requires configuration or manual review.")
    if not item["confidence"]["stale"] and item["confidence"]["level"] != "low":
        count_review = False
    else:
        count_review = True
        warnings.append("Verify actual stock before following this recommendation.")
    return {"scenario": scenario, "projected_before_delivery": starting,
            "coverage_demand": demand, "safety_stock": safety, "target_stock": target,
            "confirmed_incoming_before_delivery": sum((row["confirmed_incoming"] for row in before), ZERO),
            "uncertain_incoming_before_delivery": sum((row["uncertain_incoming"] for row in before), ZERO),
            "confirmed_incoming_in_cycle": confirmed_cycle, "uncertain_incoming_in_cycle": uncertain_cycle,
            "required_additional_quantity": required, "purchasing": rounded, "daily_calculation": path,
            "shortage_before_delivery": shortage, "first_shortage_date": first_shortage,
            "delivery_day_shortage_risk": same_day_risk, "earlier_delivery_risk": bool(prior_risks),
            "pre_delivery_risks": [risk_rows[day] for day in sorted(risk_rows)],
            "provisional": provisional, "physical_review_required": count_review or provisional,
            "definitive": not provisional and not count_review and not missing and rounded["recommended_quantity"] is not None,
            "configuration_required": bool(missing or not rounded["valid"]),
            "message": message, "warnings": list(dict.fromkeys(warnings))}


def generate_recommendations(db: Session, *, settings: Settings, ingredient_id=None, now_utc=None,
                             remaining_usage_overrides=None, override_business_date=None):
    now = as_utc(now_utc or _now())
    ingredients = db.scalars(select(Ingredient).where(Ingredient.status == "active").order_by(Ingredient.name)).all()
    ids = [row.id for row in ingredients]
    links = db.scalars(select(SupplierIngredient).where(SupplierIngredient.ingredient_id.in_(ids))).all()
    supplier_ids = {link.supplier_id for link in links}
    suppliers = {row.id: row for row in db.scalars(select(Supplier).where(Supplier.id.in_(supplier_ids)))}
    opportunities = {sid: compute_opportunities(db, supplier_id=sid, now_utc=now, horizon_days=SEARCH_DAYS,
                     business_tz=settings.business_timezone) for sid, supplier in suppliers.items() if supplier.status == "active"}
    forecast = generate_forecast(db, settings=settings, days=SEARCH_DAYS, ingredient_id=ingredient_id,
        now_utc=now, remaining_usage_overrides=remaining_usage_overrides,
        override_business_date=override_business_date, opportunities_by_supplier=opportunities)
    catalog = {row.id: row for row in ingredients}
    junctions = {(row.ingredient_id, row.supplier_id): row for row in links}
    result = []
    for item in forecast["ingredients"]:
        iid = item["ingredient_id"]
        ingredient = catalog[iid]
        supplier = suppliers.get(ingredient.primary_supplier_id)
        link = junctions.get((iid, ingredient.primary_supplier_id))
        primary = _supplier_info(supplier, link)
        cycles = _cycles(opportunities.get(supplier.id, []), iid, forecast["business_date"], forecast["end_date"]) if supplier and supplier.status == "active" and link else []
        opportunity = cycles[0] if cycles else None
        following = cycles[1] if len(cycles) > 1 else None
        conservative = _calculate(item, "conservative", opportunity, following, link)
        expected = _calculate(item, "expected", opportunity, following, link)
        cycle_end = following["delivery_date"] - timedelta(days=1) if following else None
        contributions = []
        for delivery in item["incoming"]:
            day = delivery["expected_date"]
            phase = ("draft" if delivery["status"] == "draft" else "excluded" if not delivery["expected_eligible"]
                     else "starting_projection" if opportunity and day < opportunity["delivery_date"]
                     else "delivery_cycle" if opportunity and cycle_end and opportunity["delivery_date"] <= day <= cycle_end
                     else "outside_cycle")
            contributions.append({**delivery, "projection_phase": phase})
        pending = [row for row in contributions if row["uncertain"] and row["projection_phase"] in ("starting_projection", "delivery_cycle")]
        verify_pending = bool(pending and (conservative["required_additional_quantity"] != expected["required_additional_quantity"] or
            conservative["shortage_before_delivery"] != expected["shortage_before_delivery"]))
        if verify_pending:
            for calculation in (conservative, expected):
                calculation["definitive"] = False
                calculation["warnings"].append("An existing unconfirmed order may already cover this requirement. Confirm the pending order before ordering additional stock.")
        urgent = conservative["provisional"] or expected["provisional"]
        configuration = conservative["configuration_required"] or primary is None or supplier.status != "active"
        count_review = conservative["physical_review_required"]
        flags = []
        if urgent: flags.append("urgent_shortage_risk")
        if configuration: flags.append("configuration_required")
        if verify_pending: flags.append("verify_pending_order")
        if count_review: flags.append("check_physical_stock")
        if conservative["required_additional_quantity"] is not None and conservative["required_additional_quantity"] > 0:
            flags.append("order_needed")
        elif conservative["required_additional_quantity"] == 0:
            flags.append("no_additional_order_needed")
        if not flags: flags.append("configuration_required")
        warnings = []
        if primary is None: warnings.append("Assign an active primary supplier and its purchasing configuration.")
        elif supplier.status != "active": warnings.append("The primary supplier is archived; review the assignment.")
        drafts = [row for row in contributions if row["projection_phase"] == "draft"]
        if drafts: warnings.append("An unfinished draft already exists. Review it before preparing another order; drafts are not incoming stock.")
        alternatives = []
        if urgent or not opportunity:
            risk_dates = [risk["date"] for risk in conservative["pre_delivery_risks"]]
            if conservative["first_shortage_date"]: risk_dates.append(conservative["first_shortage_date"])
            first_risk = min(risk_dates) if risk_dates else (opportunity["delivery_date"] if opportunity else None)
            for alternative in sorted((row for row in links if row.ingredient_id == iid and row.supplier_id != ingredient.primary_supplier_id), key=lambda row: row.supplier_id):
                provider = suppliers[alternative.supplier_id]
                if provider.status != "active": continue
                available = _cycles(opportunities.get(provider.id, []), iid, forecast["business_date"], forecast["end_date"])
                if not available: continue
                earliest = available[0]
                alternatives.append({"supplier": _supplier_info(provider, alternative), "opportunity": earliest,
                    "before_shortage_date": earliest["delivery_date"] < first_risk if first_risk else None,
                    "warning": "Availability is not confirmed. Contact this approved alternative; no supplier switch or quantity allocation has been made."})
        result.append({"ingredient_id": iid, "ingredient_name": item["ingredient_name"], "unit": item["unit"],
            "actual_available": item["actual_available"], "confidence": item["confidence"],
            "configuration_state": item["configuration_state"], "today_configured_usage": item["today_configured_usage"],
            "today_remaining_usage": item["today_remaining_usage"], "today_usage_source": item["today_usage_source"],
            "primary_supplier": primary, "opportunity": opportunity, "following_opportunity": following,
            "coverage_start": opportunity["delivery_date"] if opportunity else None, "coverage_end": cycle_end,
            "status": flags[0], "status_flags": flags, "verify_pending_order": verify_pending,
            "conservative": conservative, "expected": expected, "existing_orders": contributions,
            "alternative_opportunities": alternatives, "warnings": warnings,
            "manual_actions": ["Verify the actual physical inventory.", "Contact the supplier for urgent delivery.",
                "Review an existing pending shipment.", "Check an approved alternative supplier."] if urgent else []})
    priority = {"urgent_shortage_risk": 0, "verify_pending_order": 1, "order_needed": 2, "check_physical_stock": 3,
                "configuration_required": 4, "no_additional_order_needed": 5}
    result.sort(key=lambda row: (priority[row["status"]], row["opportunity"]["cutoff_at"] if row["opportunity"] else datetime.max.replace(tzinfo=timezone.utc), row["ingredient_name"]))
    cutoffs = [{"ingredient_id": row["ingredient_id"], "ingredient_name": row["ingredient_name"],
                "cutoff_at": row["opportunity"]["cutoff_at"], "delivery_date": row["coverage_start"],
                "ordering_status": row["opportunity"]["status"]} for row in result if row["opportunity"]]
    cutoffs.sort(key=lambda row: (row["cutoff_at"], row["ingredient_name"]))
    return {"as_of": now, "as_of_local": forecast["as_of_local"], "business_date": forecast["business_date"],
        "business_timezone": settings.business_timezone, "search_horizon_days": SEARCH_DAYS,
        "assumptions": [*forecast["assumptions"],
            "Cycle sizing includes arrivals before consumption on their dated day, but arrival times are unknown and pre-delivery shortage risks remain separate.",
            "Coverage includes the proposed delivery date through the day before the following actionable delivery. Additional quantity covers the maximum time-phased deficit against planning safety stock.",
            "Prior unmet demand is not an outstanding customer obligation. Earlier negative closing balances suppress definitive quantities until a fresh count or manual review.",
            "Recommendations are advisory snapshots. Existing pending orders and drafts must be reviewed; no orders, contacts, stock changes, or supplier switches are executed."],
        "recommendations": result, "forecast_summary": summarize_forecast(forecast["ingredients"], through=forecast["business_date"] + timedelta(days=7)),
        "summary": {"order_needed_count": sum("order_needed" in row["status_flags"] for row in result),
            "urgent_shortage_count": sum("urgent_shortage_risk" in row["status_flags"] for row in result),
            "verify_pending_count": sum(row["verify_pending_order"] for row in result),
            "physical_count_required_count": sum("check_physical_stock" in row["status_flags"] for row in result),
            "configuration_required_count": sum("configuration_required" in row["status_flags"] for row in result),
            "upcoming_cutoffs": cutoffs}}
