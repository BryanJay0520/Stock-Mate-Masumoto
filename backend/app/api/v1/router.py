"""Aggregates all v1 route modules into one router."""

from fastapi import APIRouter

from app.api.v1.routes import (
    admin_users,
    auth,
    demand,
    forecast,
    recommendations,
    health,
    ingredients,
    inventory,
    orders,
    receiving,
    schedules,
    suppliers,
    units,
)

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, tags=["auth"])
api_router.include_router(admin_users.router, tags=["admin"])
api_router.include_router(inventory.router, tags=["inventory"])
api_router.include_router(suppliers.router, tags=["suppliers"])
api_router.include_router(ingredients.router, tags=["ingredients"])
api_router.include_router(demand.router, tags=["demand settings"])
api_router.include_router(forecast.router, tags=["forecast"])
api_router.include_router(recommendations.router, tags=["recommendations"])
api_router.include_router(schedules.router, tags=["schedules"])
api_router.include_router(orders.router, tags=["orders"])
api_router.include_router(receiving.router, tags=["receiving"])
api_router.include_router(units.router, tags=["units"])
