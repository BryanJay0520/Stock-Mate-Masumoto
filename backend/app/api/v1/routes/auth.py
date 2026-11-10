"""Authentication routes: login, logout, current user.

Login is deliberately CSRF-exempt (there is no session yet; SameSite=Lax on
the session cookie additionally blocks cross-site sending). All mutating
routes created after login use the csrf_protect dependency instead.

Security notes:
- Identical generic error for unknown user / wrong password / inactive user,
  so responses never reveal which accounts exist.
- Failed attempts are rate-limited per username+client (see rate_limit.py).
- Passwords are never logged; only usernames appear in audit records.
"""

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.csrf import issue_csrf_token
from app.auth.dependencies import get_current_user, get_session_and_user
from app.auth.passwords import verify_password
from app.auth.sessions import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    create_session,
    revoke_session,
)
from app.database import get_db
from app.errors import ApiError
from app.models import User
from app.schemas.auth import LoginRequest, UserOut

router = APIRouter()

_INVALID = ApiError(401, "invalid_credentials", "Invalid username or password.")


@router.post("/auth/login")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    settings = request.app.state.settings
    client_host = request.client.host if request.client else "unknown"
    rate_key = f"{payload.username.lower()}|{client_host}"

    limiter = request.app.state.login_limiter
    if not limiter.check(
        rate_key,
        settings.login_rate_limit_attempts,
        settings.login_rate_limit_window_seconds,
    ):
        raise ApiError(
            429, "rate_limited", "Too many login attempts. Please try again later."
        )

    user = db.scalar(select(User).where(User.username == payload.username))
    if user is None or not user.is_active or not verify_password(
        user.password_hash, payload.password
    ):
        raise _INVALID

    limiter.reset(rate_key)
    raw_token, session_row = create_session(db, user, settings.session_ttl_hours)
    csrf_token = issue_csrf_token(settings.secret_key, session_row.token_hash)

    max_age = settings.session_ttl_hours * 3600
    response.set_cookie(
        SESSION_COOKIE, raw_token, max_age=max_age,
        httponly=True, samesite="lax", secure=settings.secure_cookies, path="/",
    )
    # Readable by our JavaScript so it can echo it in X-CSRF-Token (double-submit).
    response.set_cookie(
        CSRF_COOKIE, csrf_token, max_age=max_age,
        httponly=False, samesite="lax", secure=settings.secure_cookies, path="/",
    )
    return {"user": UserOut.model_validate(user), "csrfToken": csrf_token}


@router.post("/auth/logout")
def logout(
    response: Response,
    session_and_user=Depends(get_session_and_user),
    db: Session = Depends(get_db),
):
    """Revoke the server-side session and clear both cookies."""
    revoke_session(db, session_and_user[0])
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"status": "logged_out"}


@router.get("/auth/me")
def me(user: User = Depends(get_current_user)):
    return {"user": UserOut.model_validate(user)}
