"""
Integration tests for the FastAPI app, using FastAPI's TestClient. These
run against the real database (same as tests/test_quote_service.py) since
the API is a thin layer over real business logic and DB queries -- schema
and seed data must already be loaded, and src/erp_sync.py must have been
run at least once so a known service order exists.

If the QUOTE_API_KEY environment variable is set in the test environment,
these tests automatically attach it as an X-API-Key header so they pass
whether or not the API is running in "protected" mode.

Run with: pytest api/tests/
"""

import os
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

sys.path.append(str(Path(__file__).resolve().parent.parent))
sys.path.append(str(Path(__file__).resolve().parent.parent.parent / "src"))

from main import app
from db import get_connection

client = TestClient(app)
_api_key = os.environ.get("QUOTE_API_KEY")
if _api_key:
    client.headers.update({"X-API-Key": _api_key})


def _get_any_synced_service_order() -> str:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT service_order_no FROM service_orders LIMIT 1")
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row is None:
        pytest.skip("No service orders synced -- run src/erp_sync.py first.")
    return row[0]


def _get_any_part_number() -> str:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT part_number FROM parts LIMIT 1")
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0]


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_service_order_lookup_success():
    service_order_no = _get_any_synced_service_order()
    response = client.get(f"/service-orders/{service_order_no}")
    assert response.status_code == 200
    body = response.json()
    assert body["service_order_no"] == service_order_no
    assert body["account_name"]


def test_service_order_lookup_not_found():
    response = client.get("/service-orders/999999")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_list_parts():
    response = client.get("/parts")
    assert response.status_code == 200
    parts = response.json()
    assert len(parts) > 0
    assert "part_number" in parts[0]


def test_create_quote_end_to_end():
    service_order_no = _get_any_synced_service_order()
    part_number = _get_any_part_number()

    response = client.post("/quotes", json={
        "service_order_no": service_order_no,
        "created_by": "pytest",
        "line_items": [{"part_number": part_number, "quantity": 2}],
    })
    assert response.status_code == 201
    body = response.json()
    # Quote numbers are account-and-date derived, e.g. MER-2026-07-23-01
    # (they used to be a running Q-YYYY-NNNNN counter).
    import re
    assert re.match(r"^[A-Z]{2,6}-\d{4}-\d{2}-\d{2}-\d{2}$", body["quote_number"]), \
        f"unexpected quote number format: {body['quote_number']}"
    assert body["total"] > 0
    assert len(body["line_items"]) == 1

    detail_response = client.get(f"/quotes/{body['quote_number']}")
    assert detail_response.status_code == 200
    assert detail_response.json()["status"] == "draft"


def test_create_quote_unknown_service_order_returns_404():
    response = client.post("/quotes", json={
        "service_order_no": "999999",
        "created_by": "pytest",
        "line_items": [{"part_number": "does-not-matter", "quantity": 1}],
    })
    assert response.status_code == 404


def test_create_quote_requires_at_least_one_line_item():
    service_order_no = _get_any_synced_service_order()
    response = client.post("/quotes", json={
        "service_order_no": service_order_no,
        "created_by": "pytest",
        "line_items": [],
    })
    assert response.status_code == 422


def test_get_quote_not_found():
    response = client.get("/quotes/Q-NONEXISTENT-00000")
    assert response.status_code == 404


def test_list_quotes():
    response = client.get("/quotes?limit=5")
    assert response.status_code == 200
    body = response.json()
    assert "count" in body
    assert len(body["quotes"]) <= 5


def test_pipeline_report():
    response = client.get("/reports/pipeline")
    assert response.status_code == 200
    body = response.json()
    assert 0 <= body["win_rate_pct"] <= 100
    assert isinstance(body["status_breakdown"], list)


def test_follow_up_report():
    response = client.get("/reports/follow-up?days=7")
    assert response.status_code == 200
    body = response.json()
    assert body["days_threshold"] == 7
    assert "quotes" in body


def test_openapi_docs_available():
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Quote Automation API"


@pytest.mark.skipif(not _api_key, reason="QUOTE_API_KEY not set -- API running open, nothing to test.")
def test_protected_endpoint_rejects_missing_or_wrong_key():
    unauthenticated_client = TestClient(app)  # no X-API-Key header attached
    response = unauthenticated_client.get("/parts")
    assert response.status_code == 401

    response = unauthenticated_client.get("/parts", headers={"X-API-Key": "definitely-wrong"})
    assert response.status_code == 401


def test_health_check_never_requires_api_key():
    unauthenticated_client = TestClient(app)  # no X-API-Key header attached
    response = unauthenticated_client.get("/health")
    assert response.status_code == 200
