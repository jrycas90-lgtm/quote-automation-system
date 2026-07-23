"""
intake.py

CCR intake queue -- replaces the "CCR emails a scratch sheet to the quote
team" step.

In the original workflow a customer calls, the CCR creates a service
order in the ERP, dispatch schedules a tech, and after the visit the CCR
emails the quote team a scratch sheet listing the parts needed and what
the tech found. That email is the handoff, and it's the part of the
process with no tracking: nobody can see what's waiting, how long it's
been waiting, or whether it was ever picked up.

This module makes that handoff a queue inside the app instead.
"""

from __future__ import annotations
import sys
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).resolve().parent))
from db import get_connection, get_dict_cursor


class UnknownServiceOrderError(Exception):
    pass


def create_request(service_order_no: str, issue_description: str,
                   work_performed: str, parts_requested: str,
                   submitted_by: str) -> int:
    """Files a new intake request for the quote team. Validates the
    service order exists first -- a request against a number the ERP sync
    has never seen is almost always a typo, and catching it here saves
    the quote team chasing it down later."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT 1 FROM service_orders WHERE service_order_no = %s",
        (service_order_no,),
    )
    if cur.fetchone() is None:
        cur.close()
        conn.close()
        raise UnknownServiceOrderError(
            f"Service order {service_order_no} isn't in the system yet. "
            f"Check the number, or wait for the next ERP sync."
        )

    cur.execute(
        """
        INSERT INTO intake_requests
            (service_order_no, issue_description, work_performed,
             parts_requested, submitted_by, status)
        VALUES (%s, %s, %s, %s, %s, 'pending')
        RETURNING id
        """,
        (service_order_no, issue_description or None, work_performed or None,
         parts_requested or None, submitted_by),
    )
    request_id = cur.fetchone()[0]

    conn.commit()
    cur.close()
    conn.close()
    return request_id


def list_requests(status: Optional[str] = None) -> list[dict]:
    """Intake queue, newest first. Pass a status to filter, or None for all."""
    conn = get_connection()
    cur = get_dict_cursor(conn)

    query = """
        SELECT r.id, r.service_order_no, r.issue_description, r.work_performed,
               r.parts_requested, r.submitted_by, r.submitted_at, r.status,
               r.quote_id, a.account_name, so.site_address, so.order_type
        FROM intake_requests r
        LEFT JOIN service_orders so ON so.service_order_no = r.service_order_no
        LEFT JOIN accounts a ON a.account_id = so.account_id
    """
    params: list = []
    if status:
        query += " WHERE r.status = %s"
        params.append(status)
    query += " ORDER BY r.submitted_at DESC"

    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def mark_quoted(request_id: int, quote_id: Optional[int] = None) -> None:
    """Marks a request as handled once the quote team has built the quote."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE intake_requests SET status = 'quoted', quote_id = %s WHERE id = %s",
        (quote_id, request_id),
    )
    conn.commit()
    cur.close()
    conn.close()


def pending_count() -> int:
    """How many requests are waiting on the quote team -- used for a
    badge/count so the queue doesn't get silently ignored."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM intake_requests WHERE status = 'pending'")
    count = cur.fetchone()[0]
    cur.close()
    conn.close()
    return count
