"""Admin-only user management.

The first admin is created via the CLI (``python -m app.cli create-admin``);
after that, administrators manage accounts here. Every endpoint is protected
by require_admin (server-side RBAC) and mutations require CSRF.
"""

import json

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import csrf_protect, require_admin
from app.auth.passwords import hash_password, validate_password_strength
from app.database import get_db
from app.errors import ApiError
from app.models import AuditLog, User
from app.schemas.auth import CreateUserRequest, UserOut

router = APIRouter()


@router.get("/admin/users")
def list_users(
    _admin: User = Depends(require_admin), db: Session = Depends(get_db)
):
    users = db.scalars(select(User).order_by(User.id)).all()
    return {"users": [UserOut.model_validate(u) for u in users]}


@router.post(
    "/admin/users", status_code=201, dependencies=[Depends(csrf_protect)]
)
def create_user(
    payload: CreateUserRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    errors = validate_password_strength(payload.password)
    if errors:
        raise ApiError(
            400, "weak_password", "Password must contain: " + ", ".join(errors) + "."
        )

    existing = db.scalar(select(User).where(User.username == payload.username))
    if existing is not None:
        raise ApiError(409, "username_taken", "That username is already in use.")

    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.add(AuditLog(
        user_id=admin.id,
        action="user.create",
        entity_type="user",
        entity_id=user.id,
        after_json=json.dumps({"username": user.username, "role": user.role}),
    ))
    db.commit()
    return {"user": UserOut.model_validate(user)}
