"""
quote_service.py

Core business logic for building a quote -- this is the direct replacement
for the "scratch sheet" in the original workflow:

  1. Look up a service order by its number -> account auto-populates
     (exactly like typing the "500 number" used to auto-fill the account,
     except now it's sourced from the synced ERP data instead of a formula
     tied to a second spreadsheet).
  2. Add part numbers -> price auto-populates from account_pricing, using
     whatever price was in effect on the quote date (falls back to the
     part's list price if the account has no negotiated price for it).
  3. Save the quote -> replaces the "Quotation tab" + manual PDF export.
"""

from __future__ import annotations
import sys
from pathlib import Path
from datetime import date, datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional

sys.path.append(str(Path(__file__).resolve().parent))
from db import get_connection, get_dict_cursor
import activity


class UnknownServiceOrderError(Exception):
    pass


class UnknownPartError(Exception):
    pass


class UnknownQuoteError(Exception):
    pass


@dataclass
class QuoteLineItem:
    part_number: Optional[str]  # None for manually-added items not in the catalog/ERP sync
    description: str
    quantity: int
    unit_price: float
    item_type: str = "part"  # "part" | "custom" | "tax" -- in-session only, not a DB column;
                              # used to compute pre-tax subtotals and to find/replace an
                              # existing tax line when re-applying tax after adding more items

    @property
    def line_total(self) -> float:
        return round(self.quantity * self.unit_price, 2)


@dataclass
class QuoteDraft:
    service_order_no: str
    account_id: int
    account_name: str
    contact_name: Optional[str]
    contact_email: Optional[str]
    site_address: Optional[str]
    line_items: list[QuoteLineItem] = field(default_factory=list)

    @property
    def total(self) -> float:
        return round(sum(li.line_total for li in self.line_items), 2)


def start_quote_from_service_order(service_order_no: str) -> QuoteDraft:
    """The core "auto-populate" step: enter a service order number, get
    back everything the original scratch sheet used to fill in automatically."""
    conn = get_connection()
    cur = get_dict_cursor(conn)

    cur.execute(
        """
        SELECT so.service_order_no, so.site_address, a.account_id, a.account_name,
               a.contact_name, a.contact_email
        FROM service_orders so
        JOIN accounts a ON a.account_id = so.account_id
        WHERE so.service_order_no = %s
        """,
        (service_order_no,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()

    if row is None:
        raise UnknownServiceOrderError(
            f"Service order {service_order_no} not found. "
            f"Has it been synced yet? Run src/erp_sync.py."
        )

    return QuoteDraft(
        service_order_no=row["service_order_no"],
        account_id=row["account_id"],
        account_name=row["account_name"],
        contact_name=row["contact_name"],
        contact_email=row["contact_email"],
        site_address=row["site_address"],
    )


def lookup_price(account_id: int, part_number: str, as_of: date = None) -> tuple[float, str]:
    """Returns (price, description) for a part, using the account's
    negotiated price if one is in effect on `as_of`, otherwise falling
    back to the part's list price -- this is the VLOOKUP replacement."""
    as_of = as_of or date.today()
    conn = get_connection()
    cur = get_dict_cursor(conn)

    cur.execute("SELECT description, list_price FROM parts WHERE part_number = %s", (part_number,))
    part = cur.fetchone()
    if part is None:
        cur.close()
        conn.close()
        raise UnknownPartError(f"Part {part_number} not found in catalog.")

    cur.execute(
        """
        SELECT price FROM account_pricing
        WHERE account_id = %s AND part_number = %s
          AND effective_date <= %s
          AND (expired_date IS NULL OR expired_date > %s)
        ORDER BY effective_date DESC
        LIMIT 1
        """,
        (account_id, part_number, as_of, as_of),
    )
    pricing_row = cur.fetchone()
    cur.close()
    conn.close()

    price = float(pricing_row["price"]) if pricing_row else float(part["list_price"])
    return price, part["description"]


def add_line_item(draft: QuoteDraft, part_number: str, quantity: int) -> QuoteDraft:
    price, description = lookup_price(draft.account_id, part_number)
    draft.line_items.append(QuoteLineItem(
        part_number=part_number,
        description=description,
        quantity=quantity,
        unit_price=price,
    ))
    return draft


def add_custom_line_item(draft: QuoteDraft, description: str, quantity: int, unit_price: float) -> QuoteDraft:
    """Adds a line item for a part that isn't in the catalog/ERP sync yet --
    e.g. a brand new part, a one-off item, or a service line (Trip Charge,
    Labor, Fuel, Hardware, etc.). Stored with no part_number (NULL in the
    database), so it doesn't reference the parts catalog at all and won't
    show up in per-part reporting like top_quoted_parts(), only in the
    quote's own total and line items."""
    draft.line_items.append(QuoteLineItem(
        part_number=None,
        description=description,
        quantity=quantity,
        unit_price=unit_price,
        item_type="custom",
    ))
    return draft


def remove_line_item(draft: QuoteDraft, index: int) -> QuoteDraft:
    """Removes a line item from the draft by its position in the list."""
    if 0 <= index < len(draft.line_items):
        draft.line_items.pop(index)
    return draft


def compute_pretax_subtotal(draft: QuoteDraft) -> float:
    """Sum of every line item that isn't itself a tax line -- used as the
    base for calculating tax, and kept separate from draft.total (which
    includes tax once applied)."""
    return round(sum(li.line_total for li in draft.line_items if li.item_type != "tax"), 2)


def apply_state_tax(draft: QuoteDraft, state_code: str, rate: float) -> QuoteDraft:
    """Computes tax on the current pre-tax subtotal and adds it as a line
    item. Any previously-applied tax line is removed first, so calling
    this again after adding more parts replaces the old tax amount
    instead of stacking a second tax charge on top of it. Because of this,
    tax should generally be applied last, after all parts/charges are on
    the quote -- if more items are added afterward, tax needs to be
    re-applied to reflect the new subtotal."""
    draft.line_items = [li for li in draft.line_items if li.item_type != "tax"]
    subtotal = compute_pretax_subtotal(draft)
    tax_amount = round(subtotal * rate, 2)
    draft.line_items.append(QuoteLineItem(
        part_number=None,
        description=f"Sales Tax ({state_code} @ {rate * 100:.2f}%)",
        quantity=1,
        unit_price=tax_amount,
        item_type="tax",
    ))
    return draft


def remove_tax(draft: QuoteDraft) -> QuoteDraft:
    """Removes any applied tax line, e.g. if the user unchecks/decides
    against applying tax after already adding it."""
    draft.line_items = [li for li in draft.line_items if li.item_type != "tax"]
    return draft


def generate_quote_number(account_id: int, cur=None) -> str:
    """Builds an account-and-date derived quote number, e.g. WAL-2026-07-23-01.

    Far more readable at a glance than a running counter -- you can tell
    who a quote is for and roughly when it was raised without opening it.
    The two-digit suffix disambiguates multiple quotes for the same
    account on the same day.

    Accepts an existing cursor so it can participate in the caller's
    transaction; opens its own connection if called standalone.
    """
    own_conn = None
    if cur is None:
        own_conn = get_connection()
        cur = own_conn.cursor()

    try:
        cur.execute(
            "SELECT COALESCE(quote_prefix, UPPER(SUBSTRING(account_name FROM 1 FOR 3))) "
            "FROM accounts WHERE account_id = %s",
            (account_id,),
        )
        row = cur.fetchone()
        prefix = (row[0] if row and row[0] else "QTE").upper()

        today = date.today()
        base = f"{prefix}-{today.isoformat()}"

        # Count distinct quote numbers already issued for this account today.
        # Distinct because revisions share a quote_number and must not
        # consume a new sequence slot.
        cur.execute(
            "SELECT COUNT(DISTINCT quote_number) FROM quotes WHERE quote_number LIKE %s",
            (f"{base}-%",),
        )
        sequence = (cur.fetchone()[0] or 0) + 1
        return f"{base}-{sequence:02d}"
    finally:
        if own_conn is not None:
            cur.close()
            own_conn.close()


def find_open_quotes_for_job(service_order_no: str) -> list[dict]:
    """Open quotes on this service order OR its linked sibling.

    Now that a job routinely spans an initial (2xxxxx) and a return
    (5xxxxx) service order, it's easy to quote the same work twice under
    the two different numbers. This surfaces anything already open across
    the whole job family."""
    linked = get_linked_service_orders(service_order_no)
    numbers = {service_order_no}
    if linked.get("parent"):
        numbers.add(linked["parent"]["service_order_no"])
    for child in linked.get("children", []):
        numbers.add(child["service_order_no"])

    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute(
        """
        SELECT q.quote_number, q.revision_number, q.service_order_no, q.status,
               q.created_by, q.created_at, COALESCE(qt.quote_total, 0) AS quote_total
        FROM quotes q
        LEFT JOIN quote_totals qt ON qt.quote_id = q.quote_id
        WHERE q.service_order_no = ANY(%s)
          AND q.is_current = TRUE
          AND q.status IN ('draft', 'sent')
        ORDER BY q.created_at DESC
        """,
        (list(numbers),),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def search_quotes(term: str = "", account_id: int = None, status: str = None,
                  date_from=None, date_to=None, limit: int = 100) -> list[dict]:
    """Finds quotes by number, account, service order, preparer, status,
    or date range -- 'that Lakeshore quote from June'.

    Only current revisions are returned, so a quote revised three times
    appears once rather than three times."""
    conn = get_connection()
    cur = get_dict_cursor(conn)

    query = """
        SELECT q.quote_number, q.revision_number, q.service_order_no, q.status,
               q.created_by, q.created_at, q.sent_at,
               a.account_name, COALESCE(qt.quote_total, 0) AS quote_total
        FROM quotes q
        JOIN accounts a ON a.account_id = q.account_id
        LEFT JOIN quote_totals qt ON qt.quote_id = q.quote_id
        WHERE q.is_current = TRUE
    """
    params: list = []

    if term:
        query += """ AND (
            q.quote_number ILIKE %s OR a.account_name ILIKE %s
            OR q.service_order_no ILIKE %s OR q.created_by ILIKE %s
        )"""
        like = f"%{term}%"
        params.extend([like, like, like, like])
    if account_id:
        query += " AND q.account_id = %s"
        params.append(account_id)
    if status:
        query += " AND q.status = %s"
        params.append(status)
    if date_from:
        query += " AND q.created_at >= %s"
        params.append(date_from)
    if date_to:
        query += " AND q.created_at < (%s::date + INTERVAL '1 day')"
        params.append(date_to)

    query += " ORDER BY q.created_at DESC LIMIT %s"
    params.append(limit)

    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def save_quote(draft: QuoteDraft, created_by: str, expires_in_days: int = 30) -> str:
    """Persists the quote and its line items, returns the generated quote number."""
    conn = get_connection()
    cur = conn.cursor()

    quote_number = generate_quote_number(draft.account_id, cur)

    expires_at = date.today() + timedelta(days=expires_in_days)

    cur.execute(
        """
        INSERT INTO quotes (quote_number, service_order_no, account_id, created_by, expires_at,
                            status, revision_number, is_current)
        VALUES (%s, %s, %s, %s, %s, 'draft', 1, TRUE)
        RETURNING quote_id
        """,
        (quote_number, draft.service_order_no, draft.account_id, created_by, expires_at),
    )
    quote_id = cur.fetchone()[0]

    for li in draft.line_items:
        cur.execute(
            """
            INSERT INTO quote_line_items
                (quote_id, part_number, description, quantity, unit_price,
                 first_quoted_at, first_quoted_revision)
            VALUES (%s, %s, %s, %s, %s, now(), 1)
            """,
            (quote_id, li.part_number, li.description, li.quantity, li.unit_price),
        )

    cur.execute(
        "INSERT INTO quote_status_history (quote_id, status, note) VALUES (%s, 'draft', 'Quote created')",
        (quote_id,),
    )

    conn.commit()
    cur.close()
    conn.close()

    activity.log(quote_id, activity.CREATED, created_by,
                 f"{len(draft.line_items)} line item(s), total ${draft.total:,.2f}")

    return quote_number


# ============================================================
# Quote revisions
# ============================================================

def get_revisions(quote_number: str) -> list[dict]:
    """Every revision of a quote number, oldest first."""
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute(
        """
        SELECT quote_id, quote_number, revision_number, status, created_by,
               created_at, sent_at, is_current, revision_reason
        FROM quotes
        WHERE quote_number = %s
        ORDER BY revision_number ASC
        """,
        (quote_number,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_current_revision(quote_number: str) -> Optional[dict]:
    """The revision currently in force for a quote number."""
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute(
        """
        SELECT quote_id, quote_number, revision_number, service_order_no,
               account_id, status, created_by, created_at, expires_at
        FROM quotes
        WHERE quote_number = %s AND is_current = TRUE
        ORDER BY revision_number DESC
        LIMIT 1
        """,
        (quote_number,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def load_draft_from_quote(quote_number: str) -> tuple[QuoteDraft, list[dict]]:
    """Loads the current revision of a quote back into an editable
    QuoteDraft, so a revision can start from exactly what was previously
    quoted rather than a blank sheet.

    Returns (draft, carried_items_meta) where carried_items_meta carries
    the ORIGINAL quote date for each existing line -- the revised quote
    needs to show previously-quoted items alongside when they were first
    quoted, not make them look like they were all added today."""
    current = get_current_revision(quote_number)
    if current is None:
        raise UnknownQuoteError(f"Quote {quote_number} not found.")

    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute(
        """
        SELECT so.service_order_no, so.site_address, a.account_id, a.account_name,
               a.contact_name, a.contact_email
        FROM quotes q
        JOIN accounts a ON a.account_id = q.account_id
        LEFT JOIN service_orders so ON so.service_order_no = q.service_order_no
        WHERE q.quote_id = %s
        """,
        (current["quote_id"],),
    )
    head = cur.fetchone()

    cur.execute(
        """
        SELECT part_number, description, quantity, unit_price,
               first_quoted_at, first_quoted_revision
        FROM quote_line_items
        WHERE quote_id = %s
        ORDER BY id
        """,
        (current["quote_id"],),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    draft = QuoteDraft(
        service_order_no=head["service_order_no"] or current["service_order_no"],
        account_id=head["account_id"],
        account_name=head["account_name"],
        contact_name=head["contact_name"],
        contact_email=head["contact_email"],
        site_address=head["site_address"],
    )

    carried_meta = []
    for r in rows:
        # Tax is recalculated per revision rather than carried forward,
        # since adding parts changes the taxable subtotal.
        is_tax = (r["part_number"] is None
                  and str(r["description"]).lower().startswith("sales tax"))
        if is_tax:
            continue
        draft.line_items.append(QuoteLineItem(
            part_number=r["part_number"],
            description=r["description"],
            quantity=r["quantity"],
            unit_price=float(r["unit_price"]),
            item_type="part" if r["part_number"] else "custom",
        ))
        carried_meta.append({
            "description": r["description"],
            "first_quoted_at": r["first_quoted_at"],
            "first_quoted_revision": r["first_quoted_revision"],
        })

    return draft, carried_meta


def save_revision(quote_number: str, draft: QuoteDraft, created_by: str,
                  reason: str = "", expires_in_days: int = 30,
                  carried_meta: Optional[list[dict]] = None) -> int:
    """Saves a new revision of an existing quote.

    The previous revision is left completely intact (it may already have
    been sent, approved, or paid) and simply marked is_current = FALSE.
    The new revision keeps the same quote_number with revision_number
    incremented, so it reads as "Q-2026-00056 Rev 2".

    Line items that already existed keep their ORIGINAL first_quoted_at
    and first_quoted_revision; genuinely new items get today's date and
    the new revision number. That's what lets the UI and PDF show which
    items are carried over and when they were first quoted.

    Returns the new revision_number.
    """
    revisions = get_revisions(quote_number)
    if not revisions:
        raise UnknownQuoteError(f"Quote {quote_number} not found.")
    next_revision = max(r["revision_number"] for r in revisions) + 1

    carried_lookup = {}
    for m in (carried_meta or []):
        carried_lookup[m["description"]] = m

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "UPDATE quotes SET is_current = FALSE WHERE quote_number = %s",
        (quote_number,),
    )

    prior_id = revisions[-1]["quote_id"]
    expires_at = date.today() + timedelta(days=expires_in_days)

    cur.execute(
        """
        INSERT INTO quotes (quote_number, service_order_no, account_id, created_by,
                            expires_at, status, revision_number, supersedes_quote_id,
                            is_current, revision_reason)
        VALUES (%s, %s, %s, %s, %s, 'draft', %s, %s, TRUE, %s)
        RETURNING quote_id
        """,
        (quote_number, draft.service_order_no, draft.account_id, created_by,
         expires_at, next_revision, prior_id, reason or None),
    )
    new_quote_id = cur.fetchone()[0]

    new_item_count = 0
    for li in draft.line_items:
        meta = carried_lookup.get(li.description)
        if meta and meta.get("first_quoted_at"):
            first_at = meta["first_quoted_at"]
            first_rev = meta.get("first_quoted_revision", 1)
            cur.execute(
                """
                INSERT INTO quote_line_items
                    (quote_id, part_number, description, quantity, unit_price,
                     first_quoted_at, first_quoted_revision)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (new_quote_id, li.part_number, li.description, li.quantity,
                 li.unit_price, first_at, first_rev),
            )
        else:
            new_item_count += 1
            cur.execute(
                """
                INSERT INTO quote_line_items
                    (quote_id, part_number, description, quantity, unit_price,
                     first_quoted_at, first_quoted_revision)
                VALUES (%s, %s, %s, %s, %s, now(), %s)
                """,
                (new_quote_id, li.part_number, li.description, li.quantity,
                 li.unit_price, next_revision),
            )

    cur.execute(
        "INSERT INTO quote_status_history (quote_id, status, note) VALUES (%s, 'draft', %s)",
        (new_quote_id, f"Revision {next_revision} created"),
    )

    conn.commit()
    cur.close()
    conn.close()

    detail = f"Rev {next_revision}: {new_item_count} new item(s), total ${draft.total:,.2f}"
    if reason:
        detail += f" -- {reason}"
    activity.log(new_quote_id, activity.REVISED, created_by, detail)

    return next_revision


# ============================================================
# Linked service orders
# ============================================================

def get_linked_service_orders(service_order_no: str) -> dict:
    """Returns the family of service orders around a given one.

    In the real workflow a 2xxxxx number is the initial diagnostic trip
    and a 5xxxxx is the return trip to actually do the work; ~80% of jobs
    involve both. Whichever number someone types in, they need to see the
    other side of the job."""
    conn = get_connection()
    cur = get_dict_cursor(conn)

    cur.execute(
        """
        SELECT service_order_no, order_type, parent_service_order_no,
               order_date, description, erp_status, nte_amount
        FROM service_orders
        WHERE service_order_no = %s
        """,
        (service_order_no,),
    )
    this_order = cur.fetchone()
    if this_order is None:
        cur.close()
        conn.close()
        return {"this": None, "parent": None, "children": []}

    parent = None
    if this_order["parent_service_order_no"]:
        cur.execute(
            """
            SELECT service_order_no, order_type, order_date, description,
                   erp_status, nte_amount
            FROM service_orders WHERE service_order_no = %s
            """,
            (this_order["parent_service_order_no"],),
        )
        parent = cur.fetchone()

    cur.execute(
        """
        SELECT service_order_no, order_type, order_date, description,
               erp_status, nte_amount
        FROM service_orders
        WHERE parent_service_order_no = %s
        ORDER BY order_date
        """,
        (service_order_no,),
    )
    children = cur.fetchall()

    cur.close()
    conn.close()
    return {"this": this_order, "parent": parent, "children": children}


def link_service_orders(child_service_order_no: str, parent_service_order_no: str) -> None:
    """Links a return trip back to its initial trip."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE service_orders SET parent_service_order_no = %s WHERE service_order_no = %s",
        (parent_service_order_no, child_service_order_no),
    )
    conn.commit()
    cur.close()
    conn.close()


def get_quotes_for_service_order(service_order_no: str) -> list[dict]:
    """All quotes (current revisions only) tied to a service order."""
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute(
        """
        SELECT q.quote_number, q.revision_number, q.status, q.created_by,
               q.created_at, COALESCE(qt.quote_total, 0) AS quote_total
        FROM quotes q
        LEFT JOIN quote_totals qt ON qt.quote_id = q.quote_id
        WHERE q.service_order_no = %s AND q.is_current = TRUE
        ORDER BY q.created_at DESC
        """,
        (service_order_no,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def mark_quote_sent(quote_number: str, pdf_path: str) -> None:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "UPDATE quotes SET status = 'sent', sent_at = now(), pdf_path = %s "
        "WHERE quote_number = %s AND is_current = TRUE RETURNING quote_id",
        (pdf_path, quote_number),
    )
    row = cur.fetchone()
    if row:
        cur.execute(
            "INSERT INTO quote_status_history (quote_id, status, note) VALUES (%s, 'sent', 'PDF generated and sent to customer')",
            (row[0],),
        )
    conn.commit()
    cur.close()
    conn.close()
