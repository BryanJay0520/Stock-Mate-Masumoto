"""Authentication, session, CSRF, and RBAC tests (Phase 1, Step 3).

Uses the seeded demo users: admin / demo-admin-password (role admin) and
staff / demo-staff-password (role staff). All 18 pre-existing tests must
stay green.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.passwords import hash_password, verify_password
from app.auth.tokens import hash_token
from app.config import Settings
from app.db_admin import run_migrations
from app.main import create_app
from app.models import User, UserSession
from app.seed import DEMO_ADMIN_PASSWORD, DEMO_STAFF_PASSWORD, seed_demo_data

ADMIN = {"username": "admin", "password": DEMO_ADMIN_PASSWORD}
STAFF = {"username": "staff", "password": DEMO_STAFF_PASSWORD}


def _login(client, credentials):
    return client.post("/api/v1/auth/login", json=credentials)


@pytest.fixture()
def admin_client(seeded_client):
    response = _login(seeded_client, ADMIN)
    assert response.status_code == 200
    return seeded_client


@pytest.fixture()
def staff_client(seeded_client):
    response = _login(seeded_client, STAFF)
    assert response.status_code == 200
    return seeded_client


# --- login behavior -------------------------------------------------------

def test_login_success_returns_user_and_csrf(seeded_client):
    response = _login(seeded_client, ADMIN)
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["username"] == "admin"
    assert body["user"]["role"] == "admin"
    assert "password" not in str(body).lower() or "password_hash" not in str(body)
    assert body["csrfToken"]


def test_login_sets_secure_cookie_flags(seeded_client):
    response = _login(seeded_client, ADMIN)
    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith("stockmate_session="))
    csrf_cookie = next(c for c in cookies if c.startswith("stockmate_csrf="))
    assert "HttpOnly" in session_cookie          # invisible to JavaScript
    assert "samesite=lax" in session_cookie.lower()
    assert "HttpOnly" not in csrf_cookie          # JS must read it (double-submit)


def test_wrong_password_and_unknown_user_get_identical_error(seeded_client):
    wrong_pw = _login(seeded_client, {"username": "admin", "password": "nope-nope-nope"})
    unknown = _login(seeded_client, {"username": "nobody", "password": "nope-nope-nope"})
    assert wrong_pw.status_code == 401 == unknown.status_code
    assert wrong_pw.json() == unknown.json()      # never reveal which accounts exist
    assert wrong_pw.json()["error"]["code"] == "invalid_credentials"


def test_inactive_user_cannot_login(seeded_client):
    db = seeded_client.app.state.session_factory()
    staff = db.scalar(select(User).where(User.username == "staff"))
    staff.is_active = False
    db.commit()
    db.close()
    response = _login(seeded_client, STAFF)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


# --- session lifecycle ----------------------------------------------------

def test_me_requires_authentication(seeded_client):
    response = seeded_client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_me_returns_current_user(admin_client):
    body = admin_client.get("/api/v1/auth/me").json()
    assert body["user"]["username"] == "admin"


def test_logout_revokes_session(admin_client):
    response = admin_client.post("/api/v1/auth/logout")
    assert response.status_code == 200
    assert admin_client.get("/api/v1/auth/me").status_code == 401


def test_expired_session_is_rejected(admin_client):
    db = admin_client.app.state.session_factory()
    row = db.scalar(select(UserSession))
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    db.close()
    assert admin_client.get("/api/v1/auth/me").status_code == 401


def test_revoked_session_is_rejected(admin_client):
    db = admin_client.app.state.session_factory()
    row = db.scalar(select(UserSession))
    row.revoked_at = datetime.now(timezone.utc)
    db.commit()
    db.close()
    assert admin_client.get("/api/v1/auth/me").status_code == 401


# --- RBAC -----------------------------------------------------------------

def test_staff_cannot_reach_admin_endpoints(staff_client):
    response = staff_client.get("/api/v1/admin/users")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_admin_can_list_users(admin_client):
    response = admin_client.get("/api/v1/admin/users")
    assert response.status_code == 200
    usernames = {u["username"] for u in response.json()["users"]}
    assert {"admin", "staff"} <= usernames


# --- CSRF protection ------------------------------------------------------

def test_mutation_without_csrf_header_is_rejected(admin_client):
    payload = {"username": "newcook", "password": "cook-password-1", "role": "staff"}
    response = admin_client.post("/api/v1/admin/users", json=payload)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_csrf"


def test_mutation_with_valid_csrf_token_succeeds(admin_client):
    token = _login(admin_client, ADMIN).json()["csrfToken"]
    payload = {"username": "newcook", "password": "cook-password-1", "role": "staff"}
    response = admin_client.post(
        "/api/v1/admin/users", json=payload, headers={"X-CSRF-Token": token}
    )
    assert response.status_code == 201
    assert response.json()["user"]["role"] == "staff"


def test_csrf_token_from_another_session_is_rejected(seeded_client):
    token_a = _login(seeded_client, ADMIN).json()["csrfToken"]
    # Second login = a different session; its cookie replaces the first.
    _login(seeded_client, ADMIN)
    payload = {"username": "newcook", "password": "cook-password-1", "role": "staff"}
    response = seeded_client.post(
        "/api/v1/admin/users", json=payload, headers={"X-CSRF-Token": token_a}
    )
    assert response.status_code == 403


# --- user creation validation ----------------------------------------------

def test_create_user_rejects_weak_password(admin_client):
    token = _login(admin_client, ADMIN).json()["csrfToken"]
    payload = {"username": "weakpw", "password": "short", "role": "staff"}
    response = admin_client.post(
        "/api/v1/admin/users", json=payload, headers={"X-CSRF-Token": token}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "weak_password"


def test_create_user_rejects_duplicate_username(admin_client):
    token = _login(admin_client, ADMIN).json()["csrfToken"]
    payload = {"username": "admin", "password": "admin-password-9", "role": "staff"}
    response = admin_client.post(
        "/api/v1/admin/users", json=payload, headers={"X-CSRF-Token": token}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "username_taken"


# --- rate limiting ---------------------------------------------------------

def test_login_rate_limiting(seeded_client):
    """Dedicated username so the limiter state can't affect other tests."""
    credentials = {"username": "ratelimit-probe", "password": "wrong-password-1"}
    for _ in range(5):
        assert _login(seeded_client, credentials).status_code == 401
    blocked = _login(seeded_client, credentials)
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"


# --- password hashing --------------------------------------------------------

def test_argon2_hashing_round_trip():
    hashed = hash_password("correct-horse-1")
    assert hashed != "correct-horse-1"
    assert hashed.startswith("$argon2")
    assert verify_password(hashed, "correct-horse-1") is True
    assert verify_password(hashed, "wrong-password-2") is False
    # Salting: same password never produces the same hash twice.
    assert hash_password("correct-horse-1") != hashed


# --- cookie hardening in production mode -------------------------------------

def test_secure_flag_set_when_enabled(tmp_path):
    settings = Settings(
        environment="test",
        database_url=f"sqlite:///{(tmp_path / 'secure.db').as_posix()}",
        secret_key="test-secret",
        secure_cookies=True,
    )
    run_migrations(settings.database_url)
    app = create_app(settings)
    with TestClient(app) as client:
        session = client.app.state.session_factory()
        seed_demo_data(session, settings)
        session.close()
        response = _login(client, ADMIN)
    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith("stockmate_session="))
    assert "Secure" in session_cookie
