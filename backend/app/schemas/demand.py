from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

DemandQuantity = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=3)]


class DemandSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=0)
    enabled: bool | None = None
    monday: DemandQuantity | None = None
    tuesday: DemandQuantity | None = None
    wednesday: DemandQuantity | None = None
    thursday: DemandQuantity | None = None
    friday: DemandQuantity | None = None
    saturday: DemandQuantity | None = None
    sunday: DemandQuantity | None = None
    safety_stock: DemandQuantity | None = None
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def enabled_is_not_null(self):
        if "enabled" in self.model_fields_set and self.enabled is None:
            raise ValueError("Enabled must be true or false.")
        return self


class DemandSettingsOut(BaseModel):
    ingredient_id: int
    unit: str
    configured: bool
    enabled: bool
    monday: Decimal | None
    tuesday: Decimal | None
    wednesday: Decimal | None
    thursday: Decimal | None
    friday: Decimal | None
    saturday: Decimal | None
    sunday: Decimal | None
    safety_stock: Decimal | None
    notes: str | None
    version: int
    updated_at: datetime | None
    configured_days: int
    weekly_estimated_demand: Decimal | None
    is_complete: bool
    forecast_ready: bool

    @field_serializer("monday", "tuesday", "wednesday", "thursday", "friday",
                      "saturday", "sunday", "safety_stock", "weekly_estimated_demand")
    def serialize_quantity(self, value):
        return format(value, ".3f") if value is not None else None
