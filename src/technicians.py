"""
technicians.py

Technician roster -- internal record keeping only.

Technicians do not have user accounts and never log into this system.
They report what they found to a CCR, who raises the intake request.
This module exists purely so the business can answer "who worked this
job?" after the fact.

IMPORTANT: technician identity is internal. It is never rendered on the
customer-facing quote PDF -- src/pdf_generator.py selects its columns
explicitly and does not include technician fields. If you add technician
data to a customer-facing output, that's a deliberate policy change, not
an oversight to fix.
"""

from __future__ import annotations
import sys
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).resolve().parent))
from db import get_connection, get_dict_cursor


def list_technicians(active_only: bool = True) -> list[dict]:
    conn = get_connection()
    cur = get_dict_cursor(conn)
    query = "SELECT technician_id, full_name, employee_code, region, is_active FROM technicians"
    if active_only:
        query += " WHERE is_active = TRUE"
    query += " ORDER BY full_name"
    cur.execute(query)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def get_technician(technician_id: Optional[int]) -> Optional[dict]:
    if not technician_id:
        return None
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute(
        "SELECT technician_id, full_name, employee_code, region FROM technicians WHERE technician_id = %s",
        (technician_id,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def get_technician_for_service_order(service_order_no: Optional[str]) -> Optional[dict]:
    """The tech dispatch assigned to this service order, if any. Used to
    pre-fill the quote so the quote team rarely has to pick manually."""
    if not service_order_no:
        return None
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute(
        """
        SELECT t.technician_id, t.full_name, t.employee_code, t.region
        FROM service_orders so
        JOIN technicians t ON t.technician_id = so.technician_id
        WHERE so.service_order_no = %s
        """,
        (service_order_no,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row


def set_quote_technician(quote_id: int, technician_id: Optional[int]) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE quotes SET technician_id = %s WHERE quote_id = %s",
        (technician_id, quote_id),
    )
    conn.commit()
    cur.close()
    conn.close()


def add_technician(full_name: str, employee_code: str = None, region: str = None) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO technicians (full_name, employee_code, region)
        VALUES (%s, %s, %s) RETURNING technician_id
        """,
        (full_name, employee_code or None, region or None),
    )
    tech_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    conn.close()
    return tech_id


def set_active(technician_id: int, is_active: bool) -> None:
    """Deactivate rather than delete, so historical quotes keep pointing
    at a real name after someone leaves."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE technicians SET is_active = %s WHERE technician_id = %s",
        (is_active, technician_id),
    )
    conn.commit()
    cur.close()
    conn.close()


def quotes_by_technician() -> list[dict]:
    """Internal report: workload and outcomes per technician."""
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute(
        """
        SELECT t.full_name, t.employee_code, t.region,
               COUNT(q.quote_id) AS total_quotes,
               COUNT(*) FILTER (WHERE q.status = 'accepted') AS accepted_quotes,
               ROUND(COALESCE(SUM(qt.quote_total), 0)::numeric, 2) AS total_quoted_value
        FROM technicians t
        LEFT JOIN quotes q ON q.technician_id = t.technician_id AND q.is_current = TRUE
        LEFT JOIN quote_totals qt ON qt.quote_id = q.quote_id
        GROUP BY t.technician_id, t.full_name, t.employee_code, t.region
        ORDER BY total_quotes DESC, t.full_name
        """
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows
