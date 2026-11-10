"""FastAPI dependencies: authentication, authorization, CSRF (decision D7).

MOBILE READINESS: every protected endpoint resolves identity exclusively
through ``get_session_and_user``. Today it reads the session cookie; when
native Android/iOS clients arrive, a Bearer-token path is added INSIDE this
one function (accept ``Authorization: Bearer <token>`` alongside the cookie)
— no route or business logic changes required.
"""

import hmac

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.auth.csrf import verify_csrf_token
from app.auth.sessions import (
    CSRF_COOKIE,
    CSRF_HEADER,
    SESSION_COOKIE,
    find_valid_session,
)
from app.database import get_db
from app.errors import ApiError
from app.models import User, UserSession
from app.models.user import ROLE_ADMIN


def get_session_and_user(
    request: Request, db: Session = Depends(get_db)
) -> tuple[UserSession, User]:
    token = request.cookies.get(SESSION_COOKIE)
    # FUTURE (native mobile): also accept
    #   Authorization: Bearer <token>
    # here and treat it identically to the cookie value.
    session = find_valid_session(db, token)
    if session is None:
        raise ApiError(401, "unauthenticated", "Authentication required.")
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise ApiError(401, "unauthenticated", "Authentication required.")
    return session, user


def get_current_user(
    session_and_user: tuple[UserSession, User] = Depends(get_session_and_user),
) -> User:
    return session_and_user[1]


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Server-side RBAC: staff can NEVER reach admin-only endpoints."""
    if user.role != ROLE_ADMIN:
        raise ApiError(403, "forbidden", "Administrator access required.")
    return user


def csrf_protect(
    request: Request,
    session_and_user: tuple[UserSession, User] = Depends(get_session_and_user),
) -> None:
    """Double-submit check for mutating endpoints (see app/auth/csrf.py)."""
    settings = request.app.state.settings
    cookie_value = request.cookies.get(CSRF_COOKIE)
    header_value = request.headers.get(CSRF_HEADER)
    if (
        not cookie_value
        or not header_value
        or not hmac.compare_digest(cookie_value, header_value)
        or not verify_csrf_token(
            settings.secret_key, header_value, session_and_user[0].token_hash
        )
    ):
        raise ApiError(403, "invalid_csrf", "Invalid or missing CSRF token.")
