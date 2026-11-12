"""Read-only forecast API contract. Every quantity is an exact decimal string."""
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, PlainSerializer

Quantity = Annotated[Decimal, PlainSerializer(lambda value: format(value, ".3f"), return_type=str)]
Availability = Literal["enough", "low", "depleted", "shortage", "unknown"]
Confidence = Literal["high", "medium", "low"]


class CountConfidence(BaseModel):
    level: Confidence
    last_verified_at: datetime | None
    age_hours: int | None
    stale: bool
    high_freshness_hours: int
    medium_freshness_hours: int
    warnings: list[str]


class IncomingDelivery(BaseModel):
    order_id: int
    supplier_id: int
    supplier_name: str
    status: str
    expected_date: date | None
    ordered_quantity: Quantity
    accepted_received_quantity: Quantity
    outstanding_quantity: Quantity
    conservative_eligible: bool
    expected_eligible: bool
    uncertain: bool
    reasons: list[str]


class ForecastDay(BaseModel):
    date: date
    opening_balance: Quantity | None
    confirmed_incoming: Quantity
    uncertain_incoming: Quantity
    eligible_incoming: Quantity
    estimated_consumption: Quantity | None
    closing_balance: Quantity | None
    pre_delivery_balance: Quantity | None
    pre_delivery_shortage: Quantity | None
    pre_delivery_shortage_risk: bool
    safety_stock: Quantity | None
    status: Availability
    unmet_demand: Quantity | None
    delivery_uncertainty: bool
    confidence: Confidence
    warnings: list[str]


class ForecastScenario(BaseModel):
    scenario: Literal["conservative", "expected"]
    days: list[ForecastDay]
    today: ForecastDay
    first_shortage_date: date | None
    first_run_out_date: date | None
    first_low_date: date | None
    first_pre_delivery_risk_date: date | None


class SupplierCutoff(BaseModel):
    supplier_id: int
    supplier_name: str
    is_primary: bool
    delivery_date: date
    cutoff_at: datetime
    timezone: str
    status: str
    exception_type: str | None


class IngredientForecast(BaseModel):
    ingredient_id: int
    ingredient_name: str
    unit: str
    actual_available: Quantity | None
    cached_balance: Quantity | None
    ledger_balance: Quantity
    ledger_consistent: bool
    configuration_state: Literal["missing", "disabled", "incomplete", "enabled"]
    safety_stock: Quantity | None
    confidence: CountConfidence
    today_configured_usage: Quantity | None
    today_remaining_usage: Quantity | None
    today_usage_source: Literal["full_day_upper_bound", "override", "missing", "disabled"]
    warnings: list[str]
    open_outstanding_quantity: Quantity
    excluded_open_quantity: Quantity
    incoming: list[IncomingDelivery]
    upcoming_cutoffs: list[SupplierCutoff]
    conservative: ForecastScenario
    expected: ForecastScenario
    depends_on_uncertain: bool


class ForecastSummaryItem(BaseModel):
    ingredient_id: int
    ingredient_name: str
    first_shortage_date: date | None
    first_run_out_date: date | None
    first_low_date: date | None
    first_pre_delivery_risk_date: date | None
    stale_count: bool
    depends_on_uncertain: bool
    missing_demand: bool
    disabled: bool
    issues: list[str]


class ForecastSummary(BaseModel):
    configured_ingredients: int
    run_out_count: int
    low_count: int
    pre_delivery_risk_count: int
    stale_count: int
    uncertain_dependency_count: int
    missing_demand_count: int
    disabled_count: int
    earliest_shortage_date: date | None
    ingredients: list[ForecastSummaryItem]


class ForecastCalendar(BaseModel):
    as_of: datetime
    as_of_local: datetime
    business_timezone: str
    business_date: date
    start_date: date
    end_date: date
    horizon_days: Literal[7, 14, 30]
    baseline_policy: str
    delivery_timing: str
    assumptions: list[str]
    ingredients: list[IngredientForecast]
    summary: ForecastSummary
