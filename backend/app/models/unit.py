"""Measurement units for ingredient quantities (portion, kg, L, ...)."""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Unit(Base):
    __tablename__ = "units"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    abbreviation: Mapped[str] = mapped_column(String(10), unique=True)
