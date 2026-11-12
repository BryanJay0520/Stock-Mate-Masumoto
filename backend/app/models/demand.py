"""Ingredient demand inputs only; no stock balances or forecast results."""

from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.mixins import TimestampMixin

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
QUANTITY_FIELDS = (*WEEKDAYS, "safety_stock")


class IngredientDemandSetting(TimestampMixin, Base):
    __tablename__ = "ingredient_demand_settings"
    __table_args__ = (
        *(CheckConstraint(f"{day} IS NULL OR {day} >= 0", name=f"ck_demand_{day}_nonneg")
          for day in QUANTITY_FIELDS),
        CheckConstraint("version > 0", name="ck_demand_version_positive"),
    )

    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    monday: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
    tuesday: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
    wednesday: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
    thursday: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
    friday: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
    saturday: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
    sunday: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
    safety_stock: Mapped[Decimal | None] = mapped_column(Numeric(14, 3), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    version: Mapped[int] = mapped_column(default=1, nullable=False)
