"""Structured advisory planning results; exact decimal quantities, no mutation payload."""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.forecast import CountConfidence, ForecastSummary, IncomingDelivery, Quantity
from app.schemas.scheduling import OpportunityOut

PlannerStatus = Literal["order_needed", "no_additional_order_needed", "verify_pending_order",
                        "urgent_shortage_risk", "check_physical_stock", "configuration_required"]


class PurchasingAdvice(BaseModel):
    package_size: Quantity | None
    package_name: str | None
    whole_packages_only: bool
    minimum_order_quantity: Quantity
    valid: bool
    recommended_package_count: Quantity | None
    recommended_quantity: Quantity | None
    warnings: list[str]


class PlannerSupplier(BaseModel):
    id: int
    name: str
    status: str
    contact_name: str | None
    phone: str | None
    email: str | None
    contact_method: str | None
    ordering_url: str | None
    ordering_procedures: str | None
    notes: str | None
    purchasing: PurchasingAdvice


class CalculationDay(BaseModel):
    date: date
    estimated_consumption: Quantity
    eligible_incoming: Quantity
    closing_without_new_order: Quantity
    closing_with_recommendation: Quantity | None


class PreDeliveryRisk(BaseModel):
    date: date
    quantity: Quantity
    phase: str


class RecommendationCalculation(BaseModel):
    scenario: Literal["conservative", "expected"]
    projected_before_delivery: Quantity | None
    coverage_demand: Quantity | None
    safety_stock: Quantity | None
    target_stock: Quantity | None
    confirmed_incoming_before_delivery: Quantity
    uncertain_incoming_before_delivery: Quantity
    confirmed_incoming_in_cycle: Quantity
    uncertain_incoming_in_cycle: Quantity
    required_additional_quantity: Quantity | None
    purchasing: PurchasingAdvice
    daily_calculation: list[CalculationDay]
    shortage_before_delivery: Quantity
    first_shortage_date: date | None
    delivery_day_shortage_risk: Quantity | None
    earlier_delivery_risk: bool
    pre_delivery_risks: list[PreDeliveryRisk]
    provisional: bool
    physical_review_required: bool
    definitive: bool
    configuration_required: bool
    message: str
    warnings: list[str]


class ExistingOrderContribution(IncomingDelivery):
    projection_phase: Literal["draft", "excluded", "starting_projection", "delivery_cycle", "outside_cycle"]


class AlternativeOpportunity(BaseModel):
    supplier: PlannerSupplier
    opportunity: OpportunityOut
    before_shortage_date: bool | None
    warning: str


class IngredientRecommendation(BaseModel):
    ingredient_id: int
    ingredient_name: str
    unit: str
    actual_available: Quantity | None
    confidence: CountConfidence
    configuration_state: str
    today_configured_usage: Quantity | None
    today_remaining_usage: Quantity | None
    today_usage_source: str
    primary_supplier: PlannerSupplier | None
    opportunity: OpportunityOut | None
    following_opportunity: OpportunityOut | None
    coverage_start: date | None
    coverage_end: date | None
    status: PlannerStatus
    status_flags: list[PlannerStatus]
    verify_pending_order: bool
    conservative: RecommendationCalculation
    expected: RecommendationCalculation
    existing_orders: list[ExistingOrderContribution]
    alternative_opportunities: list[AlternativeOpportunity]
    warnings: list[str]
    manual_actions: list[str]


class PlannerCutoff(BaseModel):
    ingredient_id: int
    ingredient_name: str
    cutoff_at: datetime
    delivery_date: date
    ordering_status: str


class PlannerSummary(BaseModel):
    order_needed_count: int
    urgent_shortage_count: int
    verify_pending_count: int
    physical_count_required_count: int
    configuration_required_count: int
    upcoming_cutoffs: list[PlannerCutoff]


class OrderRecommendations(BaseModel):
    as_of: datetime
    as_of_local: datetime
    business_date: date
    business_timezone: str
    search_horizon_days: int
    assumptions: list[str]
    recommendations: list[IngredientRecommendation]
    forecast_summary: ForecastSummary
    summary: PlannerSummary
