"""
queries.py

Small query helpers specific to the API layer -- list/detail views that
didn't already exist in src/ (which was built around the CLI/Streamlit
workflow, not a REST API). Reuses src/db.py's connection logic rather
than duplicating it.
"""

from __future__ import annotations
import sys
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).resolve().parent.parent / "src"))
from db import get_connection, get_dict_cursor


def list_parts() -> list[dict]:
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute("SELECT part_number, description, category, list_price FROM parts ORDER BY part_number")
    result = cur.fetchall()
    cur.close()
    conn.close()
    return result


def list_quotes(status: Optional[str] = None, account_id: Optional[int] = None, limit: int = 50) -> list[dict]:
    conn = get_connection()
    cur = get_dict_cursor(conn)

    query = """
        SELECT quote_number, account_name, status, created_at, quote_total, account_id
        FROM quote_totals qt
        WHERE EXISTS (
            SELECT 1 FROM quotes q WHERE q.quote_id = qt.quote_id AND q.is_current
        )
    """
    params: list = []
    if status:
        query += " AND status = %s"
        params.append(status)
    if account_id:
        query += " AND account_id = %s"
        params.append(account_id)
    query += " ORDER BY created_at DESC LIMIT %s"
    params.append(limit)

    cur.execute(query, tuple(params))
    result = cur.fetchall()
    cur.close()
    conn.close()
    return result


def get_quote_detail(quote_number: str) -> Optional[dict]:
    conn = get_connection()
    cur = get_dict_cursor(conn)

    cur.execute(
        """
        SELECT q.quote_id, q.quote_number, q.revision_number, q.service_order_no,
               q.status, q.created_at, q.sent_at, q.expires_at,
               a.account_id, a.account_name, a.contact_name, a.contact_email,
               so.site_address
        FROM quotes q
        JOIN accounts a ON a.account_id = q.account_id
        LEFT JOIN service_orders so ON so.service_order_no = q.service_order_no
        WHERE q.quote_number = %s AND q.is_current = TRUE
        """,
        (quote_number,),
    )
    quote = cur.fetchone()
    if quote is None:
        cur.close()
        conn.close()
        return None

    cur.execute(
        """
        SELECT part_number, description, quantity, unit_price, line_total
        FROM quote_line_items
        WHERE quote_id = %s
        ORDER BY id
        """,
        (quote["quote_id"],),
    )
    line_items = cur.fetchall()
    cur.close()
    conn.close()

    total = sum(float(li["line_total"]) for li in line_items)

    return {
        **quote,
        "line_items": line_items,
        "total": round(total, 2),
    }
