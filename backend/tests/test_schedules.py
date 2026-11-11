"""Supplier delivery scheduling tests (Phase 2, Step 1).

Covers independent per-supplier rules, timezone-aware cutoff math with a
pinned clock, the inclusive cutoff boundary (D16), calendar exceptions,
ingredient overrides (D32), duplicate prevention, RBAC, and validation.

Demo schedules (seeded): Tokyo Noodle Co. delivers Mon+Fri supplier-wide
and Wed for Korean Noodles only (all cutoffs 1 day before, 14:30 JST).
Dragon Noodle Works: Tue/Thu (cutoff 1 day, 12:00 JST).
Masumoto Provisions: Wed/Sat (cutoff 2 days, 17:00 JST).
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import AuditLog, Ingredient, Supplier
from app.seed import DEMO_ADMIN_PASSWORD, DEMO_STAFF_PASSWORD
from app.services import scheduling

JST = ZoneInfo("Asia/Tokyo")
# A Wednesday — see module docstring for the seeded schedules.
NOW = datetime(2026, 11, 11, 10, 0, tzinfo=JST)  # 2026-11-11 01:00 UTC


def _login(client, username, password):
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200
    return response.json()["csrfToken"]


@pytest.fixture()
def make_client(seeded_client):
    def _make():
        return TestClient(seeded_client.app)
    return _make


@pytest.fixture()
def admin(make_client):
    client = make_client()
    return client, _login(client, "admin", DEMO_ADMIN_PASSWORD)


@pytest.fixture()
def staff(make_client):
    client = make_client()
    return client, _login(client, "staff", DEMO_STAFF_PASSWORD)


@pytest.fixture()
def ids(seeded_client):
    db = seeded_client.app.state.session_factory()
    try:
        suppliers = {
            s.name: s.id for s in db.scalars(select(Supplier)).all()
        }
        ingredients = {
            i.name: i.id for i in db.scalars(select(Ingredient)).all()
        }
        return suppliers, ingredients
    finally:
        db.close()


def _create_rule(client, csrf, supplier_id, **overrides):
    payload = {
        "delivery_weekday": 6,  # Sunday — unused by every seeded supplier
        "order_cutoff_days_before": 1,
        "order_cutoff_time": "09:00",
        **overrides,
    }
    return client.post(
        f"/api/v1/suppliers/{supplier_id}/schedules",
        json=payload, headers={"X-CSRF-Token": csrf},
    )


def _opportunities(seeded_client, supplier_id, now=NOW):
    db = seeded_client.app.state.session_factory()
    try:
        return scheduling.compute_opportunities(
            db, supplier_id=supplier_id, now_utc=now
        )
    finally:
        db.close()


# --- Rule listing and independence ---------------------------------------------

def test_seeded_supplier_rule_counts(staff, ids):
    client, _ = staff
    suppliers, _ = ids
    tokyo = client.get(f"/api/v1/suppliers/{suppliers['Tokyo Noodle Co.']}/schedules").json()
    dragon = client.get(f"/api/v1/suppliers/{suppliers['Dragon Noodle Works']}/schedules").json()
    masumoto = client.get(f"/api/v1/suppliers/{suppliers['Masumoto Provisions']}/schedules").json()
    # Tokyo: Mon + Fri supplier-wide, Wed for Korean Noodles (override).
    assert len(tokyo) == 3
    assert len(dragon) == 2
    assert len(masumoto) == 2


def test_different_cutoffs_per_weekday(staff, ids):
    client, _ = staff
    suppliers, _ = ids
    masumoto = client.get(f"/api/v1/suppliers/{suppliers['Masumoto Provisions']}/schedules").json()
    by_day = {r["delivery_weekday"]: r for r in masumoto}
    assert by_day[2]["order_cutoff_days_before"] == 2  # Wed <- Monday 17:00
    assert by_day[5]["order_cutoff_days_before"] == 2  # Sat <- Thursday 17:00
    assert by_day[2]["order_cutoff_time"].startswith("17:00")


def test_ingredient_override_rule_is_marked(staff, ids):
    client, _ = staff
    suppliers, ingredients = ids
    rules = client.get(f"/api/v1/suppliers/{suppliers['Tokyo Noodle Co.']}/schedules").json()
    wednesday = next(r for r in rules if r["delivery_weekday"] == 2)
    assert wednesday["ingredient_id"] == ingredients["Korean Noodles"]
    assert wednesday["ingredient_name"] == "Korean Noodles"
    assert wednesday["special_instructions"] is not None


def test_schedule_specific_instructions_are_independent(staff, ids):
    client, _ = staff
    suppliers, _ = ids
    rules = client.get(f"/api/v1/suppliers/{suppliers['Tokyo Noodle Co.']}/schedules").json()
    notes = {r["delivery_weekday"]: r["special_instructions"] for r in rules}
    assert "Sunday morning" in notes[0]
    assert "LINE" in notes[4]
    assert "Tuesday before 11:00" in notes[2]
    assert notes[0] != notes[4] != notes[2]


def test_supplier_schedule_isolation(admin, ids):
    client, csrf = admin
    suppliers, _ = ids
    tokyo_before = client.get(f"/api/v1/suppliers/{suppliers['Tokyo Noodle Co.']}/schedules").json()
    response = _create_rule(client, csrf, suppliers["Dragon Noodle Works"],
                            delivery_weekday=6, order_cutoff_days_before=3)
    assert response.status_code == 201
    tokyo_after = client.get(f"/api/v1/suppliers/{suppliers['Tokyo Noodle Co.']}/schedules").json()
    assert tokyo_before == tokyo_after


# --- Engine math (pinned clock) ---------------------------------------------------

def test_next_monday_delivery_cutoff_math(seeded_client, ids):
    """Wed 2026-11-11 10:00 JST -> next Mon delivery 2026-11-16,
    cutoff Sun 2026-11-15 14:30 JST = 05:30 UTC (previous-week ordering)."""
    suppliers, _ = ids
    opportunities = _opportunities(seeded_client, suppliers["Tokyo Noodle Co."])
    monday = next(
        o for o in opportunities
        if o["delivery_date"].isoformat() == "2026-11-16" and o["ingredient_id"] is None
    )
    assert monday["cutoff_at"] == datetime(2026, 11, 15, 5, 30, tzinfo=timezone.utc)
    # Reminder: Saturday 2026-11-14 21:30 JST = 12:30 UTC.
    assert monday["reminder_at"] == datetime(2026, 11, 14, 12, 30, tzinfo=timezone.utc)
    assert monday["status"] == "open"
    assert monday["contact"] == {
        "action": "call",
        "deadline_at": datetime(2026, 11, 15, 1, 0, tzinfo=timezone.utc),
        "required": True,
    }


def test_wednesday_override_only_applies_to_korean_noodles(seeded_client, ids):
    suppliers, ingredients = ids
    opportunities = _opportunities(seeded_client, suppliers["Tokyo Noodle Co."])
    wednesday = [
        o for o in opportunities if o["delivery_weekday"] == 2 and o["status"] != "missed"
    ]
    assert len(wednesday) > 0
    assert all(o["ingredient_id"] == ingredients["Korean Noodles"] for o in wednesday)


def test_inclusive_cutoff_boundary(seeded_client, ids):
    """Exactly at the cutoff the slot is still open; one second later it is not (D16)."""
    suppliers, _ = ids
    cutoff = datetime(2026, 11, 15, 5, 30, tzinfo=timezone.utc)  # Sun 14:30 JST

    at_cutoff = _opportunities(
        seeded_client, suppliers["Tokyo Noodle Co."], now=cutoff
    )
    monday = next(
        o for o in at_cutoff if o["delivery_date"].isoformat() == "2026-11-16"
    )
    assert monday["status"] == "open"

    after_cutoff = _opportunities(
        seeded_client, suppliers["Tokyo Noodle Co."],
        now=datetime(2026, 11, 15, 5, 30, 1, tzinfo=timezone.utc),
    )
    monday = next(
        o for o in after_cutoff if o["delivery_date"].isoformat() == "2026-11-16"
    )
    assert monday["status"] == "cutoff_reached"


def test_missed_status_for_past_delivery(seeded_client, ids):
    suppliers, _ = ids
    opportunities = _opportunities(seeded_client, suppliers["Tokyo Noodle Co."])
    past_monday = next(
        o for o in opportunities if o["delivery_date"].isoformat() == "2026-11-09"
    )
    assert past_monday["status"] == "missed"


def test_upcoming_status_with_order_window(admin, ids, seeded_client):
    """A rule with an order-opening window reports 'upcoming' before it opens."""
    client, csrf = admin
    suppliers, _ = ids
    # Friday delivery, cutoff Thu 14:30, ordering opens Wed 09:00.
    response = _create_rule(
        client, csrf, suppliers["Dragon Noodle Works"],
        delivery_weekday=4, order_cutoff_days_before=1, order_cutoff_time="14:30",
        order_open_days_before=2, order_open_time="09:00",
    )
    assert response.status_code == 201
    # Now = Mon 2026-11-09 12:00 JST: before the Wednesday opening.
    opportunities = _opportunities(
        seeded_client, suppliers["Dragon Noodle Works"],
        now=datetime(2026, 11, 9, 12, 0, tzinfo=JST),
    )
    friday = next(
        o for o in opportunities
        if o["delivery_date"].isoformat() == "2026-11-13" and o["rule_id"] == response.json()["id"]
    )
    assert friday["status"] == "upcoming"
    assert friday["order_open_at"] == datetime(2026, 11, 11, 0, 0, tzinfo=timezone.utc)


# --- Calendar exceptions ----------------------------------------------------------

def _create_exception(client, csrf, supplier_id, **payload):
    return client.post(
        f"/api/v1/suppliers/{supplier_id}/exceptions",
        json=payload, headers={"X-CSRF-Token": csrf},
    )


def test_delivery_cancelled_marks_unavailable(admin, ids, seeded_client):
    client, csrf = admin
    suppliers, _ = ids
    response = _create_exception(
        client, csrf, suppliers["Tokyo Noodle Co."],
        date="2026-11-16", type="delivery_cancelled", note="Supplier holiday",
    )
    assert response.status_code == 201
    opportunities = _opportunities(seeded_client, suppliers["Tokyo Noodle Co."])
    monday = next(
        o for o in opportunities
        if o["delivery_date"].isoformat() == "2026-11-16" and o["ingredient_id"] is None
    )
    assert monday["status"] == "unavailable_holiday"
    assert monday["exception"] == {"type": "delivery_cancelled", "note": "Supplier holiday"}


def test_cutoff_override_moves_only_that_cutoff(admin, ids, seeded_client):
    client, csrf = admin
    suppliers, _ = ids
    new_cutoff = "2026-11-13T12:00:00+00:00"  # Fri 21:00 JST — earlier than usual
    response = _create_exception(
        client, csrf, suppliers["Tokyo Noodle Co."],
        date="2026-11-16", type="cutoff_override",
        override_cutoff_at=new_cutoff, note="Order early this week",
    )
    assert response.status_code == 201
    opportunities = _opportunities(seeded_client, suppliers["Tokyo Noodle Co."])
    monday = next(
        o for o in opportunities
        if o["delivery_date"].isoformat() == "2026-11-16" and o["ingredient_id"] is None
    )
    assert monday["cutoff_at"] == datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
    assert monday["exception"]["type"] == "cutoff_override"
    # The following week's Monday keeps the normal cutoff.
    next_monday = next(
        o for o in opportunities
        if o["delivery_date"].isoformat() == "2026-11-23" and o["ingredient_id"] is None
    )
    assert next_monday["cutoff_at"] == datetime(2026, 11, 22, 5, 30, tzinfo=timezone.utc)


def test_extra_delivery_adds_one_time_opportunity(admin, ids, seeded_client):
    client, csrf = admin
    suppliers, _ = ids
    response = _create_exception(
        client, csrf, suppliers["Masumoto Provisions"],
        date="2026-11-12", type="extra_delivery",
        override_cutoff_at="2026-11-11T08:00:00+00:00", note="One-off Thursday delivery",
    )
    assert response.status_code == 201
    opportunities = _opportunities(seeded_client, suppliers["Masumoto Provisions"])
    extra = next(
        o for o in opportunities if o["delivery_date"].isoformat() == "2026-11-12"
    )
    assert extra["rule_id"] is None
    assert extra["status"] == "open"
    assert extra["exception"]["type"] == "extra_delivery"


def test_ordering_suspension_blocks_cycle(admin, ids, seeded_client):
    client, csrf = admin
    suppliers, _ = ids
    response = _create_exception(
        client, csrf, suppliers["Dragon Noodle Works"],
        date="2026-11-12", type="ordering_suspended", note="System maintenance",
    )
    assert response.status_code == 201
    opportunities = _opportunities(seeded_client, suppliers["Dragon Noodle Works"])
    thursday = next(
        o for o in opportunities if o["delivery_date"].isoformat() == "2026-11-12"
    )
    assert thursday["status"] == "unavailable_holiday"


def test_duplicate_exception_rejected(admin, ids):
    client, csrf = admin
    suppliers, _ = ids
    payload = {"date": "2026-11-16", "type": "delivery_cancelled"}
    assert _create_exception(client, csrf, suppliers["Tokyo Noodle Co."], **payload).status_code == 201
    second = _create_exception(client, csrf, suppliers["Tokyo Noodle Co."], **payload)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "duplicate_exception"


def test_extra_delivery_requires_cutoff(admin, ids):
    client, csrf = admin
    suppliers, _ = ids
    response = _create_exception(
        client, csrf, suppliers["Tokyo Noodle Co."],
        date="2026-11-17", type="extra_delivery",
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "missing_override_cutoff"


def test_naive_override_cutoff_rejected(admin, ids):
    client, csrf = admin
    suppliers, _ = ids
    response = _create_exception(
        client, csrf, suppliers["Tokyo Noodle Co."],
        date="2026-11-17", type="extra_delivery",
        override_cutoff_at="2026-11-16T08:00:00",
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "naive_datetime"


# --- Rule CRUD and validation ------------------------------------------------------

def test_create_update_and_clear_rule_fields(admin, ids):
    client, csrf = admin
    suppliers, _ = ids
    response = _create_rule(
        client, csrf, suppliers["Dragon Noodle Works"],
        reminder_days_before=1, reminder_time="08:00",
        special_instructions="Ask about seasonal noodles.",
    )
    assert response.status_code == 201
    rule = response.json()
    assert rule["reminder_time"].startswith("08:00")

    updated = client.patch(
        f"/api/v1/schedules/{rule['id']}",
        json={"order_cutoff_time": "10:30", "reminder_days_before": None,
              "reminder_time": None},
        headers={"X-CSRF-Token": csrf},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["order_cutoff_time"].startswith("10:30")
    assert body["reminder_days_before"] is None
    assert body["reminder_time"] is None


def test_deactivate_and_reactivate_rule(admin, ids):
    client, csrf = admin
    suppliers, _ = ids
    rule = _create_rule(client, csrf, suppliers["Dragon Noodle Works"]).json()
    deactivated = client.post(
        f"/api/v1/schedules/{rule['id']}/deactivate", headers={"X-CSRF-Token": csrf}
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["is_active"] is False
    reactivated = client.post(
        f"/api/v1/schedules/{rule['id']}/reactivate", headers={"X-CSRF-Token": csrf}
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["is_active"] is True


def test_duplicate_active_rule_rejected(admin, ids):
    """Tokyo already has a supplier-wide Monday rule; a second one conflicts."""
    client, csrf = admin
    suppliers, _ = ids
    response = _create_rule(
        client, csrf, suppliers["Tokyo Noodle Co."], delivery_weekday=0
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflicting_rule"


def test_reactivation_collision_rejected(admin, ids):
    """Deactivating then re-creating then reactivating must fail cleanly."""
    client, csrf = admin
    suppliers, _ = ids
    dragon = suppliers["Dragon Noodle Works"]
    tuesday = next(
        r for r in client.get(f"/api/v1/suppliers/{dragon}/schedules").json()
        if r["delivery_weekday"] == 1
    )
    client.post(f"/api/v1/schedules/{tuesday['id']}/deactivate",
                headers={"X-CSRF-Token": csrf})
    replacement = _create_rule(client, csrf, dragon, delivery_weekday=1)
    assert replacement.status_code == 201
    reactivated = client.post(
        f"/api/v1/schedules/{tuesday['id']}/reactivate", headers={"X-CSRF-Token": csrf}
    )
    assert reactivated.status_code == 409
    assert reactivated.json()["error"]["code"] == "conflicting_rule"


def test_invalid_timezone_rejected(admin, ids):
    client, csrf = admin
    suppliers, _ = ids
    response = _create_rule(
        client, csrf, suppliers["Dragon Noodle Works"], timezone="Mars/Olympus"
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_timezone"


def test_reminder_must_precede_cutoff(admin, ids):
    client, csrf = admin
    suppliers, _ = ids
    response = _create_rule(
        client, csrf, suppliers["Dragon Noodle Works"],
        order_cutoff_time="09:00",
        reminder_days_before=1, reminder_time="10:00",  # same day, AFTER cutoff
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_reminder"


def test_incomplete_opening_pair_rejected(admin, ids):
    client, csrf = admin
    suppliers, _ = ids
    response = _create_rule(
        client, csrf, suppliers["Dragon Noodle Works"], order_open_days_before=2
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "incomplete_pair"


def test_contact_deadline_on_delivery_day_rejected(admin, ids):
    client, csrf = admin
    suppliers, _ = ids
    response = _create_rule(
        client, csrf, suppliers["Dragon Noodle Works"],
        contact_action="call",
        contact_deadline_days_before=0, contact_deadline_time="08:00",
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_contact_deadline"


def test_required_contact_needs_action(admin, ids):
    client, csrf = admin
    suppliers, _ = ids
    response = _create_rule(
        client, csrf, suppliers["Dragon Noodle Works"], contact_required=True
    )
    assert response.status_code == 400


def test_ingredient_override_requires_supplier_link(admin, ids):
    """Chashu is supplied by Masumoto only — Tokyo cannot get a Chashu rule."""
    client, csrf = admin
    suppliers, ingredients = ids
    response = _create_rule(
        client, csrf, suppliers["Tokyo Noodle Co."],
        ingredient_id=ingredients["Chashu"],
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "ingredient_not_supplied"


def test_invalid_weekday_and_offset_rejected(admin, ids):
    client, csrf = admin
    suppliers, _ = ids
    assert _create_rule(
        client, csrf, suppliers["Dragon Noodle Works"], delivery_weekday=7
    ).status_code == 422
    assert _create_rule(
        client, csrf, suppliers["Dragon Noodle Works"], order_cutoff_days_before=0
    ).status_code == 422


def test_schedule_on_archived_supplier_rejected(admin, ids):
    client, csrf = admin
    suppliers, _ = ids
    created = client.post(
        "/api/v1/suppliers", json={"name": "Archived Supplier"},
        headers={"X-CSRF-Token": csrf},
    )
    supplier_id = created.json()["id"]
    client.post(f"/api/v1/suppliers/{supplier_id}/archive",
                headers={"X-CSRF-Token": csrf})
    response = _create_rule(client, csrf, supplier_id)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "supplier_inactive"


def test_missing_supplier_is_404(admin):
    client, csrf = admin
    assert client.get("/api/v1/suppliers/9999/schedules").status_code == 404
    assert _create_rule(client, csrf, 9999).status_code == 404
    assert client.get("/api/v1/suppliers/9999/opportunities").status_code == 404


# --- RBAC --------------------------------------------------------------------------

def test_staff_can_read_schedules_and_opportunities(staff, ids):
    client, _ = staff
    suppliers, _ = ids
    tokyo = suppliers["Tokyo Noodle Co."]
    assert client.get(f"/api/v1/suppliers/{tokyo}/schedules").status_code == 200
    assert client.get(f"/api/v1/suppliers/{tokyo}/exceptions").status_code == 200
    assert client.get(f"/api/v1/suppliers/{tokyo}/opportunities").status_code == 200
    assert client.get("/api/v1/ordering/opportunities").status_code == 200


def test_staff_cannot_mutate_schedules(staff, ids):
    client, csrf = staff
    suppliers, _ = ids
    response = _create_rule(client, csrf, suppliers["Tokyo Noodle Co."])
    assert response.status_code == 403
    rules = client.get(f"/api/v1/suppliers/{suppliers['Tokyo Noodle Co.']}/schedules").json()
    assert client.patch(
        f"/api/v1/schedules/{rules[0]['id']}", json={"special_instructions": "x"},
        headers={"X-CSRF-Token": csrf},
    ).status_code == 403
    assert client.post(
        f"/api/v1/schedules/{rules[0]['id']}/deactivate",
        headers={"X-CSRF-Token": csrf},
    ).status_code == 403


def test_unauthenticated_access_rejected(seeded_client, ids):
    client = TestClient(seeded_client.app)
    suppliers, _ = ids
    assert client.get(f"/api/v1/suppliers/{suppliers['Tokyo Noodle Co.']}/schedules").status_code == 401
    assert client.get("/api/v1/ordering/opportunities").status_code == 401


def test_mutations_require_csrf(admin, ids):
    client, _ = admin  # logged in, but no CSRF header
    suppliers, _ = ids
    response = client.post(
        f"/api/v1/suppliers/{suppliers['Tokyo Noodle Co.']}/schedules",
        json={"delivery_weekday": 6, "order_cutoff_days_before": 1,
              "order_cutoff_time": "09:00"},
    )
    assert response.status_code == 403


# --- Audit -------------------------------------------------------------------------

def test_rule_changes_are_audited(admin, ids, seeded_client):
    client, csrf = admin
    suppliers, _ = ids
    rule = _create_rule(client, csrf, suppliers["Dragon Noodle Works"]).json()
    client.patch(f"/api/v1/schedules/{rule['id']}",
                 json={"special_instructions": "Ring the bell."},
                 headers={"X-CSRF-Token": csrf})
    client.post(f"/api/v1/schedules/{rule['id']}/deactivate",
                headers={"X-CSRF-Token": csrf})

    db = seeded_client.app.state.session_factory()
    try:
        actions = db.scalars(
            select(AuditLog.action).where(
                AuditLog.entity_type == "schedule_rule",
                AuditLog.entity_id == rule["id"],
            ).order_by(AuditLog.id)
        ).all()
    finally:
        db.close()
    assert actions == ["schedule.create", "schedule.update", "schedule.deactivate"]


# --- Aggregate endpoint ------------------------------------------------------------

def test_aggregate_opportunities_cover_all_suppliers(staff, ids):
    client, _ = staff
    suppliers, _ = ids
    opportunities = client.get("/api/v1/ordering/opportunities?days=14").json()
    covered = {o["supplier_id"] for o in opportunities}
    assert {
        suppliers["Tokyo Noodle Co."],
        suppliers["Dragon Noodle Works"],
        suppliers["Masumoto Provisions"],
    } <= covered
    cutoffs = [o["cutoff_at"] for o in opportunities]
    assert cutoffs == sorted(cutoffs)  # sorted by cutoff
