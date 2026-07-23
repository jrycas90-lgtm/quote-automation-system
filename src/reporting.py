"""
reporting.py

Pipeline analytics -- questions that were effectively unanswerable in the
spreadsheet workflow (nobody was tallying this by hand) become simple
queries once quotes live in a real database.

Usage:
    python src/reporting.py
"""

from __future__ import annotations
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from db import get_connection, get_dict_cursor


def win_rate_summary() -> list[dict]:
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute(
        """
        SELECT
            status,
            COUNT(*) AS quote_count,
            ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct_of_total,
            ROUND(SUM(quote_total)::numeric, 2) AS total_value
        FROM quote_totals qt
        WHERE status IN ('sent', 'accepted', 'declined', 'expired')
          AND EXISTS (SELECT 1 FROM quotes q WHERE q.quote_id = qt.quote_id AND q.is_current)
        GROUP BY status
        ORDER BY total_value DESC
        """
    )
    result = cur.fetchall()
    cur.close()
    conn.close()
    return result


def win_rate_pct() -> float:
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE status = 'accepted') AS won,
            COUNT(*) FILTER (WHERE status IN ('accepted', 'declined', 'expired')) AS resolved
        FROM quotes
        WHERE is_current = TRUE
        """
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row["resolved"]:
        return 0.0
    return round(100.0 * row["won"] / row["resolved"], 1)


def revenue_by_account() -> list[dict]:
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute(
        """
        SELECT
            a.account_name,
            COUNT(*) FILTER (WHERE q.status = 'accepted') AS accepted_quotes,
            ROUND(SUM(qt.quote_total) FILTER (WHERE q.status = 'accepted')::numeric, 2) AS accepted_revenue,
            COUNT(*) AS total_quotes
        FROM quotes q
        JOIN accounts a ON a.account_id = q.account_id
        JOIN quote_totals qt ON qt.quote_id = q.quote_id
        WHERE q.is_current = TRUE
        GROUP BY a.account_name
        ORDER BY accepted_revenue DESC NULLS LAST
        """
    )
    result = cur.fetchall()
    cur.close()
    conn.close()
    return result


def avg_time_to_close() -> dict:
    """Average days between a quote being sent and being accepted/declined --
    the quote-to-close cycle time."""
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute(
        """
        SELECT
            ROUND(AVG(EXTRACT(DAY FROM h.changed_at - q.sent_at)) FILTER (WHERE h.status = 'accepted')::numeric, 1) AS avg_days_to_accept,
            ROUND(AVG(EXTRACT(DAY FROM h.changed_at - q.sent_at)) FILTER (WHERE h.status = 'declined')::numeric, 1) AS avg_days_to_decline
        FROM quote_status_history h
        JOIN quotes q ON q.quote_id = h.quote_id
        WHERE h.status IN ('accepted', 'declined')
          AND q.is_current = TRUE
        """
    )
    result = cur.fetchone()
    cur.close()
    conn.close()
    return result


def top_quoted_parts(n: int = 10) -> list[dict]:
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute(
        """
        SELECT
            li.part_number,
            li.description,
            COUNT(DISTINCT li.quote_id) AS times_quoted,
            SUM(li.quantity) AS total_quantity,
            ROUND(SUM(li.line_total)::numeric, 2) AS total_quoted_value
        FROM quote_line_items li
        JOIN quotes q ON q.quote_id = li.quote_id
        WHERE li.part_number IS NOT NULL AND q.is_current = TRUE
        GROUP BY li.part_number, li.description
        ORDER BY total_quoted_value DESC
        LIMIT %s
        """,
        (n,),
    )
    result = cur.fetchall()
    cur.close()
    conn.close()
    return result


def least_quoted_parts(n: int = 10) -> list[dict]:
    """Same idea as top_quoted_parts, but ascending -- catalog parts that
    get quoted least often. Useful for spotting slow-moving inventory or
    catalog items nobody's actually using. Excludes custom/manual line
    items (no part_number) since those aren't real catalog parts."""
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute(
        """
        SELECT
            li.part_number,
            li.description,
            COUNT(DISTINCT li.quote_id) AS times_quoted,
            SUM(li.quantity) AS total_quantity,
            ROUND(SUM(li.line_total)::numeric, 2) AS total_quoted_value
        FROM quote_line_items li
        JOIN quotes q ON q.quote_id = li.quote_id
        WHERE li.part_number IS NOT NULL AND q.is_current = TRUE
        GROUP BY li.part_number, li.description
        ORDER BY times_quoted ASC, total_quoted_value ASC
        LIMIT %s
        """,
        (n,),
    )
    result = cur.fetchall()
    cur.close()
    conn.close()
    return result


def total_quoted_value_by_account() -> list[dict]:
    """Total quoted value per account across ALL quote statuses, not just
    accepted -- shows overall quoting activity/pipeline per account,
    complementing revenue_by_account() (which is accepted-only)."""
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute(
        """
        SELECT
            a.account_name,
            COUNT(*) AS total_quotes,
            ROUND(SUM(qt.quote_total)::numeric, 2) AS total_quoted_value
        FROM quotes q
        JOIN accounts a ON a.account_id = q.account_id
        JOIN quote_totals qt ON qt.quote_id = q.quote_id
        WHERE q.is_current = TRUE
        GROUP BY a.account_name
        ORDER BY total_quoted_value DESC NULLS LAST
        """
    )
    result = cur.fetchall()
    cur.close()
    conn.close()
    return result


def print_report():
    print("=" * 70)
    print("QUOTE PIPELINE REPORT")
    print("=" * 70)

    print(f"\nOverall win rate: {win_rate_pct()}%\n")

    print("Status breakdown:")
    for row in win_rate_summary():
        print(f"  {row['status']:<10} {row['quote_count']:>4} quotes "
              f"({row['pct_of_total']:>5}%)  ${row['total_value']:>12,.2f}")

    timing = avg_time_to_close()
    print(f"\nAvg days to accept: {timing['avg_days_to_accept']}")
    print(f"Avg days to decline: {timing['avg_days_to_decline']}")

    print("\nRevenue by account (accepted quotes):")
    for row in revenue_by_account():
        revenue = row["accepted_revenue"] or 0
        print(f"  {row['account_name']:<30} {row['accepted_quotes']:>2} accepted / "
              f"{row['total_quotes']:>2} total   ${revenue:>10,.2f}")

    print("\nTop 10 most-quoted parts:")
    for row in top_quoted_parts():
        print(f"  {row['part_number']:<10} {row['description']:<35} "
              f"quoted {row['times_quoted']:>3}x   ${row['total_quoted_value']:>10,.2f}")


if __name__ == "__main__":
    print_report()


def intake_to_quote_cycle_time() -> dict:
    """How long intake requests sit before the quote goes out.

    This is the number that justifies the whole system: before the intake
    queue existed, the handoff was an email, and "how long is a request
    waiting?" was unanswerable. Measured from when the CCR submitted the
    request to when the quote team marked it quoted.
    """
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute(
        """
        SELECT
            COUNT(*) AS quoted_count,
            ROUND(AVG(EXTRACT(EPOCH FROM (q.created_at - r.submitted_at)) / 3600)::numeric, 1)
                AS avg_hours,
            ROUND(MAX(EXTRACT(EPOCH FROM (q.created_at - r.submitted_at)) / 3600)::numeric, 1)
                AS max_hours
        FROM intake_requests r
        JOIN quotes q ON q.quote_id = r.quote_id
        WHERE r.status = 'quoted' AND r.quote_id IS NOT NULL
        """
    )
    closed = cur.fetchone()

    cur.execute(
        """
        SELECT
            COUNT(*) AS pending_count,
            ROUND(AVG(EXTRACT(EPOCH FROM (now() - submitted_at)) / 3600)::numeric, 1)
                AS avg_waiting_hours,
            ROUND(MAX(EXTRACT(EPOCH FROM (now() - submitted_at)) / 3600)::numeric, 1)
                AS longest_waiting_hours
        FROM intake_requests
        WHERE status = 'pending'
        """
    )
    pending = cur.fetchone()

    cur.close()
    conn.close()
    return {
        "quoted_count": closed["quoted_count"] or 0,
        "avg_hours": closed["avg_hours"],
        "max_hours": closed["max_hours"],
        "pending_count": pending["pending_count"] or 0,
        "avg_waiting_hours": pending["avg_waiting_hours"],
        "longest_waiting_hours": pending["longest_waiting_hours"],
    }


def intake_backlog() -> list[dict]:
    """Intake requests still waiting on a quote, oldest first -- the
    working list for whoever is clearing the queue."""
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute(
        """
        SELECT r.service_order_no, a.account_name, r.submitted_by, r.submitted_at,
               ROUND(EXTRACT(EPOCH FROM (now() - r.submitted_at)) / 3600::numeric, 1)
                   AS hours_waiting
        FROM intake_requests r
        LEFT JOIN service_orders so ON so.service_order_no = r.service_order_no
        LEFT JOIN accounts a ON a.account_id = so.account_id
        WHERE r.status = 'pending'
        ORDER BY r.submitted_at ASC
        """
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows
