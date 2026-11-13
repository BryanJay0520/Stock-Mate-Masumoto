"""Explicit notification evaluation; all ordinary reads remain read-only."""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.auth.dependencies import csrf_protect, get_current_user, require_admin
from app.database import get_db
from app.errors import ApiError
from app.models import User
from app.schemas.notifications import (EvaluationOut, NotificationOut, NotificationPage,
                                       NotificationSettingsOut, NotificationSettingsUpdate, NotificationView,
                                       SchedulerHealthOut)
from app.services import notification_scheduler, notifications

router = APIRouter(prefix="/notifications")


@router.get("", response_model=NotificationPage)
def list_items(view: NotificationView = "active", limit: int = Query(25, ge=1, le=100),
               offset: int = Query(0, ge=0), user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    return notifications.list_notifications(db, user_id=user.id, view=view, limit=limit, offset=offset)


@router.get("/unread-count")
def unread_count(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return notifications.unread_count(db, user_id=user.id)


@router.get("/settings", response_model=NotificationSettingsOut)
def settings(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return notifications.get_settings(db)


@router.get("/scheduler-health", response_model=SchedulerHealthOut)
def scheduler_health(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return notification_scheduler.health(db)


@router.patch("/settings", response_model=NotificationSettingsOut, dependencies=[Depends(csrf_protect)])
def update_settings(payload: NotificationSettingsUpdate, admin: User = Depends(require_admin),
                    db: Session = Depends(get_db)):
    changes = payload.model_dump(exclude_unset=True)
    if any(value is None for value in changes.values()):
        raise ApiError(422, "invalid_notification_setting", "Use explicit values for notification settings.")
    return notifications.update_settings(db, changes)


@router.post("/evaluate", response_model=EvaluationOut, dependencies=[Depends(csrf_protect)])
def evaluate(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return notifications.evaluate(db, settings=request.app.state.settings)


@router.post("/mark-all-read", dependencies=[Depends(csrf_protect)])
def mark_all_read(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return notifications.mark_all_read(db, user_id=user.id)


@router.post("/{notification_id}/read", response_model=NotificationOut, dependencies=[Depends(csrf_protect)])
def read_one(notification_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return notifications.mark_read(db, user_id=user.id, notification_id=notification_id)


@router.post("/{notification_id}/acknowledge", response_model=NotificationOut, dependencies=[Depends(csrf_protect)])
def acknowledge(notification_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return notifications.acknowledge(db, user_id=user.id, notification_id=notification_id)
