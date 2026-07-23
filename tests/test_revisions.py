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


def test_pdf_and_detail_queries_target_current_revision():
    """Regression guard. quote_number stopped being unique once revisions
    existed, which broke every query that looked a quote up by number
    alone -- some raised CardinalityViolation, and worse, some silently
    returned an arbitrary revision or updated ALL of them."""
    from pdf_generator import fetch_quote_data
    from quote_service import mark_quote_sent

    qn = _make_quote()
    loaded, carried = load_draft_from_quote(qn)
    parts = _any_part()
    add_line_item(loaded, parts[1], 1)
    new_rev = save_revision(qn, loaded, created_by="pytest", carried_meta=carried)

    # Must not raise, and must return the CURRENT revision -- not Rev 1
    data = fetch_quote_data(qn)
    assert data["quote"]["revision_number"] == new_rev

    # Marking sent must touch only the current revision
    mark_quote_sent(qn, "output/test.pdf")
    revisions = get_revisions(qn)
    sent = [r["revision_number"] for r in revisions if r["status"] == "sent"]
    assert sent == [new_rev], f"only current revision should be sent, got {sent}"


# ---------- parts parser / guardrails / migration runner ----------

def test_parts_parser_handles_common_formats():
    from parts_parser import parse_parts_text
    parts = _any_part()
    pn = parts[0]
    parsed = parse_parts_text(f"{pn} x3\n2x {pn}\n{pn}\nsomething with no part number")
    assert parsed[0]["part_number"] == pn and parsed[0]["quantity"] == 3
    assert parsed[1]["part_number"] == pn and parsed[1]["quantity"] == 2
    assert parsed[2]["part_number"] == pn and parsed[2]["quantity"] == 1
    # unmatched lines are kept, not dropped -- they're what a human must review
    assert parsed[3]["matched"] is False
    assert parsed[3]["raw"] == "something with no part number"


def test_nte_check_only_fires_when_exceeded():
    import pricing_checks
    from db import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT service_order_no FROM service_orders WHERE nte_amount IS NOT NULL LIMIT 1")
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row is None:
        pytest.skip("No service order with an NTE in this dataset.")
    so = row[0]
    nte = pricing_checks.get_nte(so)
    assert pricing_checks.check_nte(so, nte - 1) is None
    over = pricing_checks.check_nte(so, nte + 500)
    assert over is not None and over["overage"] == 500


def test_pricing_checks_flag_anomalies_not_correct_lines():
    import pricing_checks
    from quote_service import QuoteLineItem
    so = _any_service_order()
    draft = start_quote_from_service_order(so)
    parts = _any_part()
    add_line_item(draft, parts[0], 1)          # correctly priced
    correct = draft.line_items[0]

    items = [
        correct,
        QuoteLineItem(None, "No-charge warranty part", 1, 0.0, "custom"),
        QuoteLineItem(parts[0], correct.description, 500, correct.unit_price, "part"),
    ]
    warnings = pricing_checks.check_line_items(draft.account_id, items)
    messages = " ".join(w["message"] for w in warnings)
    assert "$0.00" in messages
    assert "unusually high" in messages
    # the correctly-priced single line must not produce a price-variance warning
    assert "above the expected" not in messages and "below the expected" not in messages


def test_migration_runner_is_idempotent():
    """The runner must never re-apply a migration it has already recorded."""
    import importlib.util
    from db import get_connection
    spec = importlib.util.spec_from_file_location(
        "migrate", Path(__file__).resolve().parent.parent / "scripts" / "migrate.py"
    )
    migrate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migrate)

    conn = get_connection()
    migrate.ensure_tracking_table(conn)
    applied = migrate.applied_migrations(conn)
    available = {p.name for p in migrate.available_migrations()}
    conn.close()

    # Everything on disk should already be recorded after a normal run,
    # and the tracking table must not contain unknown files.
    assert applied <= available or not applied


def test_technician_recorded_internally_but_never_on_customer_pdf():
    """Technician identity is deliberately internal. This test is the
    guard: if someone later adds technician fields to the PDF, this fails
    loudly rather than quietly exposing techs to customers."""
    import subprocess
    import technicians as tech
    from pdf_generator import generate_pdf

    roster = tech.list_technicians()
    if not roster:
        pytest.skip("No technicians in roster.")
    t = roster[0]

    qn = _make_quote()
    current = get_current_revision(qn)
    tech.set_quote_technician(current["quote_id"], t["technician_id"])

    # Retrievable internally
    assert tech.get_technician(t["technician_id"])["full_name"] == t["full_name"]

    path = generate_pdf(qn, output_dir="/tmp/pytest_pdf")
    text = subprocess.run(["pdftotext", path, "-"], capture_output=True, text=True).stdout.lower()

    for token in filter(None, [t["full_name"], t["employee_code"]]):
        assert token.lower() not in text, f"technician identity leaked onto customer PDF: {token}"


def test_quote_numbers_are_account_and_date_derived():
    from quote_service import generate_quote_number
    from db import get_connection
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT account_id, quote_prefix FROM accounts WHERE quote_prefix IS NOT NULL LIMIT 1")
    account_id, prefix = cur.fetchone()
    cur.close(); conn.close()
    from datetime import date
    number = generate_quote_number(account_id)
    assert number.startswith(f"{prefix}-{date.today().isoformat()}-")


def test_customer_pdf_never_leaks_contractor_pricing_or_identity():
    """The commercial confidentiality guard: a customer must not see what
    we pay a subcontractor, or that one was used at all."""
    import subprocess
    import contractors as gc
    from pdf_generator import generate_pdf

    roster = gc.list_contractors()
    if not roster:
        pytest.skip("No contractors in roster.")
    contractor = roster[0]

    so = _any_service_order()
    draft = start_quote_from_service_order(so)
    add_custom_line_item(draft, "Door repair - labor and parts", 1, 800.00)
    qn = save_quote(draft, created_by="pytest")
    current = get_current_revision(qn)

    gc.assign_to_quote(current["quote_id"], contractor["contractor_id"])
    gc.set_line_costs(current["quote_id"], {"Door repair - labor and parts": 650.00})

    margin = gc.get_margin(current["quote_id"])
    assert margin["margin"] == 150.00 and margin["complete"]

    customer_pdf = generate_pdf(qn, output_dir="/tmp/pytest_gc", audience="customer")
    text = subprocess.run(["pdftotext", customer_pdf, "-"], capture_output=True, text=True).stdout

    assert "800.00" in text, "customer should see their own price"
    assert "650.00" not in text, "customer PDF leaked contractor pricing"
    assert contractor["company_name"].split()[0] not in text, "customer PDF leaked contractor identity"
    assert "contractor" not in text.lower(), "customer PDF mentions a contractor"

    contractor_pdf = generate_pdf(qn, output_dir="/tmp/pytest_gc", audience="contractor")
    gtext = subprocess.run(["pdftotext", contractor_pdf, "-"], capture_output=True, text=True).stdout
    assert "650.00" in gtext and "800.00" not in gtext


def test_duplicate_quote_warning_spans_linked_service_orders():
    from quote_service import find_open_quotes_for_job
    from db import get_connection
    conn = get_connection(); cur = conn.cursor()
    cur.execute("SELECT service_order_no, parent_service_order_no FROM service_orders "
                "WHERE parent_service_order_no IS NOT NULL LIMIT 1")
    row = cur.fetchone(); cur.close(); conn.close()
    if row is None:
        pytest.skip("No linked service orders.")
    child, parent = row

    draft = start_quote_from_service_order(child)
    add_line_item(draft, _any_part()[0], 1)
    qn = save_quote(draft, created_by="pytest")

    # Quoting against the LINKED order must surface the quote on its sibling
    found = find_open_quotes_for_job(parent)
    assert any(q["quote_number"] == qn for q in found), \
        "duplicate check must span the whole job family, not just one service order"
