"""
activity.py

Quote activity log -- who did what to a quote, and when.

The original schema had `quote_status_history`, but it only recorded
status transitions and never recorded WHO made them. This module logs
every meaningful action (creation, revisions, line item changes, tax,
PDF generation, sending) against a named user, so anyone opening a quote
a week later can see exactly what a coworker did to it and when.
"""

from __future__ import annotations
import sys
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).resolve().parent))
from db import get_connection, get_dict_cursor


# Action constants -- kept as plain strings in the DB for readability when
# querying directly, but referenced through these names in code so typos
# surface immediately.
CREATED = "created"
REVISED = "revised"
LINE_ITEM_ADDED = "line_item_added"
LINE_ITEM_REMOVED = "line_item_removed"
TAX_APPLIED = "tax_applied"
TAX_REMOVED = "tax_removed"
PDF_GENERATED = "pdf_generated"
SENT = "sent"
STATUS_CHANGED = "status_changed"

ACTION_LABELS = {
    CREATED: "Created quote",
    REVISED: "Created revision",
    LINE_ITEM_ADDED: "Added line item",
    LINE_ITEM_REMOVED: "Removed line item",
    TAX_APPLIED: "Applied tax",
    TAX_REMOVED: "Removed tax",
    PDF_GENERATED: "Generated PDF",
    SENT: "Marked as sent",
    STATUS_CHANGED: "Changed status",
}


def log(quote_id: int, action: str, performed_by: str, detail: Optional[str] = None) -> None:
    """Records one activity entry. Deliberately never raises on failure --
    an audit-log write should not be able to break the user's actual
    workflow (e.g. lose a quote they just built). Failures are silent
    here by design; the underlying action still completes."""
    if not quote_id:
        return
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO quote_activity (quote_id, action, detail, performed_by)
            VALUES (%s, %s, %s, %s)
            """,
            (quote_id, action, detail, performed_by or "unknown"),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


def get_activity(quote_id: int) -> list[dict]:
    """Full activity trail for one quote, oldest first."""
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute(
        """
        SELECT action, detail, performed_by, performed_at
        FROM quote_activity
        WHERE quote_id = %s
        ORDER BY performed_at ASC, id ASC
        """,
        (quote_id,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_activity_for_quote_number(quote_number: str) -> list[dict]:
    """Activity across EVERY revision of a quote number, oldest first.

    This is the view that actually matters day to day: opening
    "Q-2026-00056" should show the whole story -- original creation, what
    was added in Rev 2, who sent it -- not just whatever happened to the
    revision you're currently looking at."""
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute(
        """
        SELECT a.action, a.detail, a.performed_by, a.performed_at,
               q.revision_number
        FROM quote_activity a
        JOIN quotes q ON q.quote_id = a.quote_id
        WHERE q.quote_number = %s
        ORDER BY a.performed_at ASC, a.id ASC
        """,
        (quote_number,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def describe(action: str) -> str:
    """Human-readable label for an action code."""
    return ACTION_LABELS.get(action, action.replace("_", " ").capitalize())
