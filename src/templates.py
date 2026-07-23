"""
templates.py

Reusable quote templates for recurring work -- modernization kits being
the driving case.

Two pricing modes per line, because both occur in practice:

  fixed_price is None -> the part is priced per account at apply time,
                         exactly as if someone added it by hand. Correct
                         for ordinary parts where each account has its
                         own negotiated rate.

  fixed_price is set  -> that price is used as-is regardless of account.
                         Correct for flat-rate packages like a mod kit,
                         where a fixed price IS the product.

Applying a template only populates a draft. Nothing is written to the
database until the user reviews the lines and generates the quote
normally -- templates are a starting point, not an autopilot.
"""

from __future__ import annotations
import sys
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).resolve().parent))
from db import get_connection, get_dict_cursor


def list_templates(active_only: bool = True) -> list[dict]:
    conn = get_connection()
    cur = get_dict_cursor(conn)
    query = """
        SELECT t.template_id, t.name, t.description, t.created_by, t.created_at, t.is_active,
               COUNT(i.id) AS item_count,
               COALESCE(SUM(COALESCE(i.fixed_price, 0) * i.quantity), 0) AS fixed_total
        FROM quote_templates t
        LEFT JOIN quote_template_items i ON i.template_id = t.template_id
    """
    if active_only:
        query += " WHERE t.is_active = TRUE"
    query += " GROUP BY t.template_id ORDER BY t.name"
    cur.execute(query)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_template_items(template_id: int) -> list[dict]:
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute(
        """
        SELECT id, part_number, description, quantity, fixed_price, sort_order
        FROM quote_template_items
        WHERE template_id = %s
        ORDER BY sort_order, id
        """,
        (template_id,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def create_template(name: str, description: str, created_by: str) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO quote_templates (name, description, created_by)
        VALUES (%s, %s, %s) RETURNING template_id
        """,
        (name, description or None, created_by),
    )
    tid = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return tid


def add_item(template_id: int, description: str, quantity: int = 1,
             part_number: Optional[str] = None, fixed_price: Optional[float] = None) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM quote_template_items WHERE template_id = %s",
        (template_id,),
    )
    next_order = cur.fetchone()[0]
    cur.execute(
        """
        INSERT INTO quote_template_items
            (template_id, part_number, description, quantity, fixed_price, sort_order)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (template_id, part_number or None, description, quantity, fixed_price, next_order),
    )
    conn.commit()
    cur.close()
    conn.close()


def remove_item(item_id: int) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM quote_template_items WHERE id = %s", (item_id,))
    conn.commit()
    cur.close()
    conn.close()


def delete_template(template_id: int) -> None:
    """Deactivates rather than deletes, so a template that's been used
    historically doesn't vanish from the record."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE quote_templates SET is_active = FALSE WHERE template_id = %s", (template_id,))
    conn.commit()
    cur.close()
    conn.close()


def apply_to_draft(draft, template_id: int) -> dict:
    """Adds a template's lines to an existing QuoteDraft.

    Catalog parts without a fixed price go through the normal per-account
    pricing lookup, so a template stays correct across accounts with
    different negotiated rates. Fixed-price lines are added at their set
    price.

    Returns a summary of what was added, so the UI can tell the user
    plainly rather than making them diff the line items themselves.
    """
    # Imported here rather than at module scope to avoid a circular import:
    # quote_service doesn't depend on templates, and shouldn't have to.
    from quote_service import add_line_item, add_custom_line_item, UnknownPartError

    items = get_template_items(template_id)
    added, skipped = 0, []

    for item in items:
        qty = item["quantity"] or 1
        fixed = float(item["fixed_price"]) if item["fixed_price"] is not None else None

        if item["part_number"] and fixed is None:
            # Ordinary catalog part -- price it for this specific account
            try:
                add_line_item(draft, item["part_number"], qty)
                added += 1
            except UnknownPartError:
                skipped.append(item["description"])
        else:
            # Flat-rate line, or a description-only line with a set price
            add_custom_line_item(draft, item["description"], qty, fixed or 0.0)
            added += 1

    return {"added": added, "skipped": skipped, "total_items": len(items)}
