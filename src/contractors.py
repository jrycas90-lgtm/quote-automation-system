"""
contractors.py

General contractors (GCs) -- subcontractors who do the work when a job
is outside our own service area.

The commercial shape of this: the GC charges US one price, we charge the
CUSTOMER another. Send George's Hardware to a Walmart in Montana; Walmart
pays $800, George's bills us $650, and the $150 difference is our margin.

Two hard rules, both about what the customer sees:

  1. The customer must never see contractor pricing.
  2. The customer must not know a contractor was involved at all.

So contractor data lives on the same line items as the customer price
(one source of truth for WHAT work is being done) but is rendered only
on the internal/GC-facing document -- never on the customer PDF. See
src/pdf_generator.py, which takes an explicit `audience` argument and
defaults to the customer view.
"""

from __future__ import annotations
import sys
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).resolve().parent))
from db import get_connection, get_dict_cursor


def list_contractors(active_only: bool = True) -> list[dict]:
    conn = get_connection()
    cur = get_dict_cursor(conn)
    query = """
        SELECT contractor_id, company_name, contact_name, contact_email,
               phone, region, is_active
        FROM contractors
    """
    if active_only:
        query += " WHERE is_active = TRUE"
    query += " ORDER BY company_name"
    cur.execute(query)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_contractor(contractor_id: Optional[int]) -> Optional[dict]:
    if not contractor_id:
        return None
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute(
        """
        SELECT contractor_id, company_name, contact_name, contact_email, phone, region
        FROM contractors WHERE contractor_id = %s
        """,
        (contractor_id,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def add_contractor(company_name: str, contact_name: str = None, contact_email: str = None,
                   phone: str = None, region: str = None) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO contractors (company_name, contact_name, contact_email, phone, region)
        VALUES (%s, %s, %s, %s, %s) RETURNING contractor_id
        """,
        (company_name, contact_name or None, contact_email or None, phone or None, region or None),
    )
    cid = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return cid


def set_active(contractor_id: int, is_active: bool) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE contractors SET is_active = %s WHERE contractor_id = %s",
                (is_active, contractor_id))
    conn.commit()
    cur.close()
    conn.close()


def assign_to_quote(quote_id: int, contractor_id: Optional[int]) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE quotes SET contractor_id = %s WHERE quote_id = %s",
                (contractor_id, quote_id))
    conn.commit()
    cur.close()
    conn.close()


def set_line_costs(quote_id: int, costs_by_description: dict[str, float]) -> None:
    """Records what the GC charges us for each line.

    Keyed by description because that's what's stable between the
    customer view and the GC view of the same job -- the line items are
    identical, only the prices differ."""
    if not costs_by_description:
        return
    conn = get_connection()
    cur = conn.cursor()
    for description, cost in costs_by_description.items():
        cur.execute(
            """
            UPDATE quote_line_items SET contractor_cost = %s
            WHERE quote_id = %s AND description = %s
            """,
            (cost, quote_id, description),
        )
    conn.commit()
    cur.close()
    conn.close()


def get_margin(quote_id: int) -> dict:
    """Customer total vs contractor cost for a quote.

    Returns customer_total, contractor_total, margin, margin_pct, and
    whether every line has a contractor cost recorded -- an incomplete
    cost picture makes the margin misleading, so the caller can say so
    rather than quietly showing a wrong number.
    """
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute(
        """
        SELECT
            COALESCE(SUM(line_total), 0) AS customer_total,
            COALESCE(SUM(contractor_cost * quantity), 0) AS contractor_total,
            COUNT(*) AS line_count,
            COUNT(contractor_cost) AS costed_lines
        FROM quote_line_items
        WHERE quote_id = %s
        """,
        (quote_id,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()

    customer_total = float(row["customer_total"] or 0)
    contractor_total = float(row["contractor_total"] or 0)
    margin = round(customer_total - contractor_total, 2)
    margin_pct = round((margin / customer_total * 100), 1) if customer_total else 0.0

    return {
        "customer_total": round(customer_total, 2),
        "contractor_total": round(contractor_total, 2),
        "margin": margin,
        "margin_pct": margin_pct,
        "line_count": row["line_count"] or 0,
        "costed_lines": row["costed_lines"] or 0,
        "complete": (row["costed_lines"] or 0) == (row["line_count"] or 0) and (row["line_count"] or 0) > 0,
    }


def contractor_margin_report() -> list[dict]:
    """Margin by contractor across all current quotes -- which GCs are
    actually worth using."""
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute(
        """
        SELECT c.company_name, c.region,
               COUNT(DISTINCT q.quote_id) AS jobs,
               ROUND(COALESCE(SUM(li.line_total), 0)::numeric, 2) AS customer_total,
               ROUND(COALESCE(SUM(li.contractor_cost * li.quantity), 0)::numeric, 2) AS contractor_total,
               ROUND((COALESCE(SUM(li.line_total), 0)
                      - COALESCE(SUM(li.contractor_cost * li.quantity), 0))::numeric, 2) AS margin
        FROM contractors c
        JOIN quotes q ON q.contractor_id = c.contractor_id AND q.is_current = TRUE
        JOIN quote_line_items li ON li.quote_id = q.quote_id
        GROUP BY c.contractor_id, c.company_name, c.region
        ORDER BY margin DESC
        """
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows
