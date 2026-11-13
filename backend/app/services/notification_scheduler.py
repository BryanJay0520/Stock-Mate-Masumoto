"""Database-coordinated polling for the dedicated notification worker.

The web app never imports this service at startup. SQLite's short write-intent
transactions serialize lease claims; the existing evaluator independently keeps
business events idempotent. A lease is coordination, not an exactly-once claim.
"""

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import Settings
from app.database import set_write_intent
from app.models import NotificationSetting, NotificationWorkerState
from app.services import notifications
from app.services.confidence import as_utc

LEASE_SECONDS = 180
HEARTBEAT_SECONDS = 30


def _utc(now=None):
    return as_utc(now or datetime.now(timezone.utc))


def _write(db: Session, action):
    set_write_intent(True)
    try:
        result = action(db)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        set_write_intent(False)


def claim(session_factory, *, owner_token: str, now_utc=None):
    """Heartbeat or take an expired lease; return whether evaluation is due."""
    now = _utc(now_utc)
    with session_factory() as db:
        def action(db):
            state = db.get(NotificationWorkerState, 1)
            if state is None:
                state = NotificationWorkerState(id=1, updated_at=now)
                db.add(state)
            elif (state.owner_token not in (None, owner_token) and
                  state.lease_expires_at is not None and as_utc(state.lease_expires_at) > now):
                return "busy"
            state.owner_token = owner_token
            state.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
            state.heartbeat_at = now
            state.updated_at = now
            policy = db.get(NotificationSetting, 1)
            if policy is None or not policy.automatic_evaluation_enabled:
                return "disabled"
            # The first worker check runs immediately after enable/recovery.
            # Thereafter a manual refresh may postpone the next automatic
            # check, since both use the same persisted evaluation snapshot.
            last = policy.last_evaluated_at if state.last_success_at else None
            if last is not None and now < as_utc(last) + timedelta(seconds=policy.evaluation_interval_seconds):
                return "idle"
            state.last_attempt_at = now
            return "due"
        return _write(db, action)


def _record(session_factory, *, owner_token: str, now, result=None, error=None):
    with session_factory() as db:
        def action(db):
            state = db.get(NotificationWorkerState, 1)
            if state is None or state.owner_token != owner_token:
                return False  # The lease was taken over; this worker is fenced out.
            state.heartbeat_at = now
            state.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
            state.updated_at = now
            if error is None:
                state.last_success_at = now
                state.last_result_json = json.dumps({
                    key: result.get(key, 0) for key in
                    ("created", "updated", "transitioned", "resolved", "expired", "superseded")
                })
            else:
                state.last_failure_at = now
                state.last_error_code = type(error).__name__[:80]
            return True
        return _write(db, action)


def release(session_factory, *, owner_token: str, now_utc=None):
    now = _utc(now_utc)
    with session_factory() as db:
        def action(db):
            state = db.get(NotificationWorkerState, 1)
            if state is None or state.owner_token != owner_token:
                return False
            state.owner_token = None
            state.lease_expires_at = None
            state.updated_at = now
            return True
        return _write(db, action)


def tick(session_factory, *, settings: Settings, owner_token: str, clock=None):
    """One non-overlapping worker check. Tests inject a controllable UTC clock."""
    clock = clock or (lambda: datetime.now(timezone.utc))
    state = claim(session_factory, owner_token=owner_token, now_utc=clock())
    if state != "due":
        return {"status": state}
    try:
        with session_factory() as db:
            result = notifications.evaluate(db, settings=settings, now_utc=clock(),
                worker_owner_token=owner_token)
        if result is None:
            return {"status": "skipped"}  # Manual refresh won the interval.
        if not _record(session_factory, owner_token=owner_token, now=_utc(clock()), result=result):
            return {"status": "lost"}
        return {"status": "evaluated", "result": result}
    except notifications.WorkerLeaseLost:
        return {"status": "lost"}
    except Exception as error:
        try:
            _record(session_factory, owner_token=owner_token, now=_utc(clock()), error=error)
        except Exception:
            pass  # Preserve the original error; the lease expires for takeover.
        raise


def health(db: Session, *, now_utc=None):
    """Read-only administrator status; never claims that enabled means running."""
    now = _utc(now_utc)
    policy = db.get(NotificationSetting, 1)
    state = db.get(NotificationWorkerState, 1)
    enabled = policy.automatic_evaluation_enabled if policy else False
    interval = policy.evaluation_interval_seconds if policy else 60
    heartbeat = as_utc(state.heartbeat_at) if state and state.heartbeat_at else None
    lease = as_utc(state.lease_expires_at) if state and state.lease_expires_at else None
    live = bool(state and state.owner_token and heartbeat and lease and
                lease > now and heartbeat + timedelta(seconds=HEARTBEAT_SECONDS * 3) > now)
    if not enabled:
        status = "disabled"
    elif state is None or heartbeat is None:
        status = "unknown"
    elif not live:
        status = "stopped"
    elif state.last_failure_at and (not state.last_success_at or
            as_utc(state.last_failure_at) > as_utc(state.last_success_at)):
        status = "error"
    else:
        status = "running"
    last_eval = as_utc(policy.last_evaluated_at) if policy and policy.last_evaluated_at else None
    next_due = max(now, last_eval + timedelta(seconds=interval)) if enabled and last_eval else now if enabled else None
    return {
        "automatic_evaluation_enabled": enabled, "evaluation_interval_seconds": interval,
        "status": status, "heartbeat_at": heartbeat, "lease_expires_at": lease,
        "last_attempt_at": as_utc(state.last_attempt_at) if state and state.last_attempt_at else None,
        "last_success_at": as_utc(state.last_success_at) if state and state.last_success_at else None,
        "last_failure_at": as_utc(state.last_failure_at) if state and state.last_failure_at else None,
        "last_error_code": state.last_error_code if state else None,
        "next_expected_evaluation_at": next_due,
        "last_result": json.loads(state.last_result_json) if state and state.last_result_json else None,
    }
