"""
Integration tests for quote_service.py.

These run against a real Postgres database (not mocked) since the whole
point of this module is database logic -- pricing lookups, service order
resolution, and quote persistence. Requires the schema and seed data to
already be loaded (see README setup steps) before running.

Run with: pytest tests/
"""

import sys
from pathlib import Path
import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent / "src"))
from quote_service import (
    start_quote_from_service_order, add_line_item, lookup_price,
    UnknownServiceOrderError, UnknownPartError,
)
from db import get_connection


def _get_any_synced_service_order():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT service_order_no FROM service_orders LIMIT 1")
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row is None:
        pytest.skip("No service orders synced -- run baan_sync.py first.")
    return row[0]


def _get_any_part_number():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT part_number FROM parts LIMIT 1")
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0]


def test_unknown_service_order_raises():
    with pytest.raises(UnknownServiceOrderError):
        start_quote_from_service_order("999999")


def test_known_service_order_populates_account():
    service_order_no = _get_any_synced_service_order()
    draft = start_quote_from_service_order(service_order_no)
    assert draft.account_id is not None
    assert draft.account_name


def test_unknown_part_raises():
    service_order_no = _get_any_synced_service_order()
    draft = start_quote_from_service_order(service_order_no)
    with pytest.raises(UnknownPartError):
        add_line_item(draft, "NOT-A-REAL-PART", 1)


def test_add_line_item_computes_total():
    service_order_no = _get_any_synced_service_order()
    draft = start_quote_from_service_order(service_order_no)
    part_number = _get_any_part_number()

    add_line_item(draft, part_number, 3)
    assert len(draft.line_items) == 1
    li = draft.line_items[0]
    assert li.quantity == 3
    assert li.line_total == round(3 * li.unit_price, 2)
    assert draft.total == li.line_total


def test_lookup_price_falls_back_to_list_price_for_unpriced_account():
    """An account with no negotiated price for a part should fall back
    to the part's list price rather than erroring."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT list_price FROM parts LIMIT 1")
    list_price = float(cur.fetchone()[0])
    part_number = _get_any_part_number()

    cur.execute("SELECT account_id FROM accounts ORDER BY account_id DESC LIMIT 1")
    account_id = cur.fetchone()[0]
    cur.execute(
        "DELETE FROM account_pricing WHERE account_id = %s AND part_number = %s",
        (account_id, part_number),
    )
    conn.commit()
    cur.close()
    conn.close()

    price, _ = lookup_price(account_id, part_number)
    assert price == list_price
