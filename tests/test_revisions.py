"""
Integration tests for the revision, linked-service-order, activity-log,
and intake features.

Like the other suites here these run against a real Postgres database
rather than mocks, since the logic under test is fundamentally about
database state -- which revision is current, whether a superseded
revision is preserved, whether carried-forward line items keep their
original quote dates.

Run with: pytest tests/test_revisions.py
"""

import sys
from pathlib import Path
import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent / "src"))
from quote_service import (
    start_quote_from_service_order, add_line_item, add_custom_line_item,
    save_quote, save_revision, load_draft_from_quote, get_revisions,
    get_current_revision, get_linked_service_orders, UnknownQuoteError,
)
import activity
import intake
from db import get_connection


def _any_service_order(order_type=None):
    conn = get_connection()
    cur = conn.cursor()
    if order_type:
        cur.execute(
            "SELECT service_order_no FROM service_orders WHERE order_type = %s LIMIT 1",
            (order_type,),
        )
    else:
        cur.execute("SELECT service_order_no FROM service_orders LIMIT 1")
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row is None:
        pytest.skip("No service orders synced -- run src/erp_sync.py first.")
    return row[0]


def _any_part():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT part_number FROM parts LIMIT 2")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [r[0] for r in rows]


def _make_quote():
    so = _any_service_order()
    draft = start_quote_from_service_order(so)
    parts = _any_part()
    add_line_item(draft, parts[0], 2)
    add_custom_line_item(draft, "Trip Charge", 1, 75.00)
    return save_quote(draft, created_by="pytest-original")


def test_revision_preserves_original():
    """The whole point of revisions: the previous version must survive
    untouched, since it may already have been sent, approved, or paid."""
    qn = _make_quote()
    original = get_current_revision(qn)
    original_id = original["quote_id"]

    loaded, carried = load_draft_from_quote(qn)
    parts = _any_part()
    add_line_item(loaded, parts[1], 1)
    new_rev = save_revision(qn, loaded, created_by="pytest-reviser",
                            reason="tech returned", carried_meta=carried)

    assert new_rev == 2
    revisions = get_revisions(qn)
    assert len(revisions) == 2

    # Original row still exists, unchanged, and is no longer current
    rev1 = [r for r in revisions if r["revision_number"] == 1][0]
    assert rev1["quote_id"] == original_id
    assert rev1["is_current"] is False
    assert rev1["created_by"] == "pytest-original"


def test_exactly_one_current_revision():
    qn = _make_quote()
    loaded, carried = load_draft_from_quote(qn)
    save_revision(qn, loaded, created_by="pytest", carried_meta=carried)
    loaded2, carried2 = load_draft_from_quote(qn)
    save_revision(qn, loaded2, created_by="pytest", carried_meta=carried2)

    revisions = get_revisions(qn)
    assert len(revisions) == 3
    assert sum(1 for r in revisions if r["is_current"]) == 1
    assert get_current_revision(qn)["revision_number"] == 3


def test_carried_items_keep_original_quote_date():
    """A revision must show previously-quoted items with the date they
    were FIRST quoted, not make them look like they were added today."""
    qn = _make_quote()
    loaded, carried = load_draft_from_quote(qn)
    parts = _any_part()
    add_line_item(loaded, parts[1], 3)
    save_revision(qn, loaded, created_by="pytest", carried_meta=carried)

    current = get_current_revision(qn)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT description, first_quoted_revision
        FROM quote_line_items WHERE quote_id = %s ORDER BY id
        """,
        (current["quote_id"],),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    first_revs = {desc: rev for desc, rev in rows}
    # Items carried from the original are still attributed to revision 1
    assert 1 in first_revs.values(), "carried items should retain first_quoted_revision = 1"
    # The newly added item is attributed to revision 2
    assert 2 in first_revs.values(), "new items should be attributed to the new revision"


def test_revision_of_unknown_quote_raises():
    with pytest.raises(UnknownQuoteError):
        load_draft_from_quote("Q-DOES-NOT-EXIST")


def test_activity_is_logged_with_user_and_revision():
    qn = _make_quote()
    loaded, carried = load_draft_from_quote(qn)
    save_revision(qn, loaded, created_by="pytest-reviser", carried_meta=carried)

    trail = activity.get_activity_for_quote_number(qn)
    actions = [t["action"] for t in trail]
    assert activity.CREATED in actions
    assert activity.REVISED in actions

    users = {t["performed_by"] for t in trail}
    assert "pytest-original" in users
    assert "pytest-reviser" in users
    assert all(t["performed_at"] is not None for t in trail)


def test_linked_service_orders_resolve_both_directions():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT service_order_no, parent_service_order_no
        FROM service_orders WHERE parent_service_order_no IS NOT NULL LIMIT 1
        """
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row is None:
        pytest.skip("No linked service orders in this dataset.")
    child, parent = row

    from_child = get_linked_service_orders(child)
    assert from_child["parent"]["service_order_no"] == parent

    from_parent = get_linked_service_orders(parent)
    assert child in [c["service_order_no"] for c in from_parent["children"]]


def test_intake_rejects_unknown_service_order():
    with pytest.raises(intake.UnknownServiceOrderError):
        intake.create_request("999999", "issue", "work", "parts", "pytest")


def test_intake_request_lifecycle():
    so = _any_service_order()
    before = intake.pending_count()
    rid = intake.create_request(so, "door won't latch", "diagnosed closer",
                                "HW-2201 x1", "pytest-ccr")
    assert intake.pending_count() == before + 1

    pending = intake.list_requests("pending")
    assert any(r["id"] == rid for r in pending)

    intake.mark_quoted(rid)
    assert intake.pending_count() == before
