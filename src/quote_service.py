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


class UnknownServiceOrderError(Exception):
    pass


class UnknownPartError(Exception):
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


def save_quote(draft: QuoteDraft, created_by: str, expires_in_days: int = 30) -> str:
    """Persists the quote and its line items, returns the generated quote number."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT COALESCE(MAX(quote_id), 0) + 1 FROM quotes")
    next_id = cur.fetchone()[0]
    quote_number = f"Q-{datetime.now().year}-{next_id:05d}"

    expires_at = date.today() + timedelta(days=expires_in_days)

    cur.execute(
        """
        INSERT INTO quotes (quote_number, service_order_no, account_id, created_by, expires_at, status)
        VALUES (%s, %s, %s, %s, %s, 'draft')
        RETURNING quote_id
        """,
        (quote_number, draft.service_order_no, draft.account_id, created_by, expires_at),
    )
    quote_id = cur.fetchone()[0]

    for li in draft.line_items:
        cur.execute(
            """
            INSERT INTO quote_line_items (quote_id, part_number, description, quantity, unit_price)
            VALUES (%s, %s, %s, %s, %s)
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

    return quote_number


def mark_quote_sent(quote_number: str, pdf_path: str) -> None:
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "UPDATE quotes SET status = 'sent', sent_at = now(), pdf_path = %s WHERE quote_number = %s RETURNING quote_id",
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
