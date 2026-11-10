"""Aggregates all v1 route modules into one router."""

from fastapi import APIRouter

from app.api.v1.routes import admin_users, auth, health, inventory

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, tags=["auth"])
api_router.include_router(admin_users.router, tags=["admin"])
api_router.include_router(inventory.router, tags=["inventory"])
