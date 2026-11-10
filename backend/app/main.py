"""StockMate FastAPI application factory.

One process serves both the versioned JSON API (``/api/v1``) and the static
frontend, so local development needs a single command. The API is fully
client-agnostic: any HTTP client (browser, PWA, future native app) uses the
same endpoints (decision D1).
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.auth.rate_limit import RateLimiter
from app.config import Settings, get_settings
from app.database import create_db_engine, create_session_factory
from app.errors import ApiError, api_error_handler


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. Tests pass custom settings for isolation."""
    settings = settings or get_settings()

    app = FastAPI(title=settings.app_name, version=settings.app_version)

    # Database is attached to app.state so dependencies can reach it.
    engine = create_db_engine(settings.database_url)
    app.state.db_engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.settings = settings

    # Consistent error shape {"error": {...}} for all ApiError raises.
    app.add_exception_handler(ApiError, api_error_handler)

    # Per-process login rate limiter (MVP; see app/auth/rate_limit.py).
    app.state.login_limiter = RateLimiter()

    # Versioned API first — it must win over the static mount below.
    app.include_router(api_router, prefix="/api/v1")

    # Static frontend served at "/" (mobile-ready thin client, decision D1).
    app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="frontend")

    return app


app = create_app()
