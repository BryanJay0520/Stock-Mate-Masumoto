"""Tests for the health check endpoint (Phase 1, Step 1)."""


def test_health_returns_ok(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "StockMate"
    assert body["database"] == "ok"


def test_health_reports_business_timezone(client):
    body = client.get("/api/v1/health").json()
    assert body["businessTimezone"] == "Asia/Tokyo"
    assert body["businessDayStartTime"] == "00:00"


def test_frontend_is_served(client):
    """The static frontend shell must be reachable at the root URL."""
    response = client.get("/")
    assert response.status_code == 200
    assert "StockMate" in response.text


def test_api_wins_over_static_mount(client):
    """/api/v1 routes must not be swallowed by the static files mount."""
    response = client.get("/api/v1/health")
    assert response.headers["content-type"].startswith("application/json")
