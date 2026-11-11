"""Package counting converts into the one canonical ledger balance."""

from decimal import Decimal

import pytest
from app.seed import DEMO_ADMIN_PASSWORD, DEMO_STAFF_PASSWORD


def login(client, username="admin"):
    password = DEMO_ADMIN_PASSWORD if username == "admin" else DEMO_STAFF_PASSWORD
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrfToken"]}


def ingredient(client, name):
    return next(row for row in client.get("/api/v1/ingredients").json() if row["name"] == name)


def stock(client, ingredient_id):
    return next(row for row in client.get("/api/v1/inventory/stock").json()
                if row["ingredient_id"] == ingredient_id)


def entry(client, headers, ingredient_id, mode, boxes, loose, version, key):
    return client.post("/api/v1/inventory/entries", headers=headers, json={
        "ingredient_id": ingredient_id, "mode": mode, "reason": "daily_count",
        "package_count": {"full_packages": boxes, "loose_units": loose,
                          "package_version": version},
        "idempotency_key": key,
    })


def configure(client, headers, ingredient_id, name, size):
    response = client.patch(f"/api/v1/ingredients/{ingredient_id}", headers=headers,
                            json={"count_package_enabled": True,
                                  "count_package_name": name,
                                  "count_units_per_package": size})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("name,package_name,size,boxes,loose,expected", [
    ("Korean Noodles", "Box", "60", 2, "15", "135"),
    ("Korean Noodles", "Box", "60", 1, "0", "60"),
    ("Korean Noodles", "Box", "60", 0, "15", "15"),
    ("Korean Noodles", "Box", "60", 2, "0", "120"),
    ("Chinese Noodles", "Box", "50", 2, "15", "115"),
    ("Eggs", "Tray", "30", 2, "15", "75"),
    ("Green Onions", "Bag", "1.250", 2, "0.125", "2.625"),
])
def test_package_reconciliation(seeded_client, name, package_name, size, boxes, loose, expected):
    client = seeded_client
    headers = login(client)
    item = ingredient(client, name)
    before = Decimal(stock(client, item["id"])["current_quantity"])
    configured = configure(client, headers, item["id"], package_name, size)
    assert Decimal(stock(client, item["id"])["current_quantity"]) == before
    result = entry(client, headers, item["id"], "remaining", boxes, loose,
                   configured["count_package_version"], "count-packages-1")
    assert result.status_code == 200, result.text
    transaction = result.json()["transaction"]
    assert Decimal(transaction["resulting_quantity"]) == Decimal(expected)
    assert Decimal(transaction["quantity_delta"]) == Decimal(expected) - before
    assert transaction["counting_breakdown"]["full_packages"] == boxes
    assert transaction["counting_breakdown"]["units_per_package"] == str(Decimal(size).quantize(Decimal("0.001")))
    row = stock(client, item["id"])
    assert row["confidence"] == "high" and row["last_counted_at"]
    assert Decimal(row["current_quantity"]) == Decimal(expected)
    assert client.get("/api/v1/inventory/verify").json()["consistent"] is True


def test_used_package_entry_and_replay(seeded_client):
    client = seeded_client
    login(client, "staff")
    item = ingredient(client, "Eggs")
    admin_headers = login(client)
    configured = configure(client, admin_headers, item["id"], "Tray", "30")
    # Login again as staff on a separate client to preserve the staff session.
    from fastapi.testclient import TestClient
    staff = TestClient(client.app)
    staff_headers = login(staff, "staff")
    before = stock(staff, item["id"])
    result = entry(staff, staff_headers, item["id"], "used", 1, "5",
                   configured["count_package_version"], "used-packages-1")
    assert result.status_code == 200
    assert Decimal(stock(staff, item["id"])["current_quantity"]) == Decimal(before["current_quantity"]) - 35
    assert stock(staff, item["id"])["last_counted_at"] == before["last_counted_at"]
    replay = entry(staff, staff_headers, item["id"], "used", 1, "5",
                   configured["count_package_version"], "used-packages-1")
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["transaction"]["id"] == result.json()["transaction"]["id"]
    conflict = entry(staff, staff_headers, item["id"], "used", 0, "35",
                     configured["count_package_version"], "used-packages-1")
    assert conflict.status_code == 409
    assert client.get("/api/v1/inventory/verify").json()["consistent"] is True


def test_invalid_counting_inputs_and_disabled_ingredient(seeded_client):
    client = seeded_client
    headers = login(client)
    item = ingredient(client, "Nori")
    base = {"ingredient_id": item["id"], "mode": "remaining", "reason": "daily_count",
            "idempotency_key": "invalid-package-entry"}
    assert entry(client, headers, item["id"], "remaining", 1, "0", 1,
                 "disabled-packaging").status_code == 400
    for bad in ({"full_packages": -1, "loose_units": "0", "package_version": 1},
                {"full_packages": "1.5", "loose_units": "0", "package_version": 1},
                {"full_packages": 1, "loose_units": "-1", "package_version": 1},
                {"full_packages": 1, "loose_units": "0.0001", "package_version": 1}):
        assert client.post("/api/v1/inventory/entries", headers=headers,
                           json={**base, "package_count": bad}).status_code == 422
    assert client.post("/api/v1/inventory/entries", headers=headers,
                       json={**base, "quantity": "5",
                             "package_count": {"full_packages": 1, "loose_units": "0",
                                               "package_version": 1}}).status_code == 422
    assert client.patch(f"/api/v1/ingredients/{item['id']}", headers=headers,
                        json={"count_package_enabled": True,
                              "count_package_name": "Tray", "count_units_per_package": "0"}).status_code == 422
    assert client.patch(f"/api/v1/ingredients/{item['id']}", headers=headers,
                        json={"count_package_enabled": True,
                              "count_package_name": "  ", "count_units_per_package": "30"}).status_code == 400
    configured = configure(client, headers, item["id"], "Tray", "30")
    assert entry(client, headers, item["id"], "remaining", 1, "0", 0,
                 "stale-packaging").status_code == 422
    assert entry(client, headers, item["id"], "remaining", 1, "0", 42,
                 "stale-packaging").status_code == 409
    assert configured["count_package_version"] == 1


def test_package_changes_do_not_rewrite_stock_or_history(seeded_client):
    client = seeded_client
    headers = login(client)
    item = ingredient(client, "Eggs")
    first = configure(client, headers, item["id"], "Tray", "30")
    result = entry(client, headers, item["id"], "remaining", 2, "0",
                   first["count_package_version"], "historical-count-1")
    assert result.status_code == 200
    before = stock(client, item["id"])
    changed = configure(client, headers, item["id"], "Case", "12")
    assert changed["count_package_version"] == first["count_package_version"] + 1
    assert stock(client, item["id"])["current_quantity"] == before["current_quantity"]
    assert stock(client, item["id"])["last_counted_at"] == before["last_counted_at"]
    replay = entry(client, headers, item["id"], "remaining", 2, "0",
                   first["count_package_version"], "historical-count-1")
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["transaction"]["counting_breakdown"]["units_per_package"] == "30.000"
    assert entry(client, headers, item["id"], "remaining", 2, "0",
                 first["count_package_version"], "historical-count-2").status_code == 409
    disabled = client.patch(f"/api/v1/ingredients/{item['id']}", headers=headers,
                            json={"count_package_enabled": False})
    assert disabled.status_code == 200
    assert stock(client, item["id"])["current_quantity"] == before["current_quantity"]
    normal = client.post("/api/v1/inventory/entries", headers=headers,
                         json={"ingredient_id": item["id"], "mode": "used",
                               "quantity": "5", "reason": "daily_count",
                               "idempotency_key": "normal-entry-after-disable"})
    assert normal.status_code == 200
    assert normal.json()["transaction"]["counting_breakdown"] is None


def test_supplier_purchase_packaging_is_separate(seeded_client):
    client = seeded_client
    headers = login(client)
    item = ingredient(client, "Korean Noodles")
    supplier_id = item["primary_supplier_id"]
    configure(client, headers, item["id"], "Count box", "50")
    response = client.patch(
        f"/api/v1/ingredients/{item['id']}/suppliers/{supplier_id}/purchase-packaging",
        headers=headers, json={"purchase_package_name": "Supplier carton",
                               "package_size": "60", "whole_packages_only": True})
    assert response.status_code == 200, response.text
    assert response.json()["package_size"] == "60.000"
    detail = client.get(f"/api/v1/suppliers/{supplier_id}").json()
    link = next(link for link in detail["ingredients"] if link["ingredient_id"] == item["id"])
    assert link["package_size"] == "60.000"
    assert link["whole_packages_only"] is True
    assert ingredient(client, "Korean Noodles")["count_units_per_package"] == "50.000"
    # Phase 3 can round a 125-unit requirement to three supplier cartons.
    required = Decimal("125")
    packages = (required / Decimal(link["package_size"])).to_integral_value(rounding="ROUND_CEILING")
    assert packages == 3 and packages * Decimal(link["package_size"]) == 180


def test_new_korean_noodles_can_use_whole_piece_stock_units(seeded_client):
    client = seeded_client
    headers = login(client)
    unit = next(row for row in client.get("/api/v1/units").json() if row["abbreviation"] == "pc")
    created = client.post("/api/v1/ingredients", headers=headers, json={
        "name": "Korean Noodles by Piece", "category": "Noodles", "unit_id": unit["id"],
        "usage_per_serving": "1", "count_package_enabled": True,
        "count_package_name": "Box", "count_units_per_package": "60",
    })
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["unit"] == "pc"
    response = entry(client, headers, item["id"], "remaining", 2, "15",
                     item["count_package_version"], "piece-noodles-1")
    assert response.status_code == 200
    assert stock(client, item["id"])["current_quantity"] == "135.000"


def test_canonical_unit_cannot_change_with_stock_history(seeded_client):
    client = seeded_client
    headers = login(client)
    item = ingredient(client, "Eggs")
    units = client.get("/api/v1/units").json()
    other = next(unit for unit in units if unit["id"] != item["unit_id"])
    response = client.patch(f"/api/v1/ingredients/{item['id']}", headers=headers,
                            json={"unit_id": other["id"]})
    assert response.status_code == 409
    assert ingredient(client, "Eggs")["unit_id"] == item["unit_id"]


def test_supplier_purchase_packaging_requires_admin_and_valid_size(seeded_client):
    from fastapi.testclient import TestClient

    admin = seeded_client
    admin_headers = login(admin)
    item = ingredient(admin, "Eggs")
    url = (f"/api/v1/ingredients/{item['id']}/suppliers/"
           f"{item['primary_supplier_id']}/purchase-packaging")
    staff = TestClient(admin.app)
    staff_headers = login(staff, "staff")
    payload = {"package_size": "30", "purchase_package_name": "Tray",
               "whole_packages_only": True}
    assert staff.patch(url, headers=staff_headers, json=payload).status_code == 403
    assert admin.patch(url, headers=admin_headers,
                       json={**payload, "package_size": "0"}).status_code == 422
    assert admin.patch(url, headers=admin_headers,
                       json={**payload, "purchase_package_name": " "}).status_code == 400
    assert admin.patch(url, headers=admin_headers,
                       json={"whole_packages_only": True}).status_code == 400
    detail = admin.get(f"/api/v1/suppliers/{item['primary_supplier_id']}").json()
    link = next(row for row in detail["ingredients"] if row["ingredient_id"] == item["id"])
    assert link["package_size"] == "30.000" and link["whole_packages_only"] is True
