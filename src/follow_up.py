"""
follow_up.py

Surfaces quotes that need attention -- something the original spreadsheet
workflow had no way to do at all. "Sent" quotes with no status change
after N days are flagged as needing a follow-up call/email; quotes past
their expiration date are flagged for expiry.

Usage:
    python src/follow_up.py --days 7
"""

from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from db import get_connection, get_dict_cursor


def get_quotes_needing_follow_up(days_since_sent: int = 7) -> list[dict]:
    conn = get_connection()
    cur = get_dict_cursor(conn)

    cur.execute(
        """
        SELECT
            q.quote_number,
            a.account_name,
            a.contact_name,
            a.contact_email,
            q.sent_at,
            EXTRACT(DAY FROM now() - q.sent_at)::int AS days_since_sent,
            qt.quote_total,
            q.expires_at
        FROM quotes q
        JOIN accounts a ON a.account_id = q.account_id
        JOIN quote_totals qt ON qt.quote_id = q.quote_id
        WHERE q.status = 'sent'
          AND q.sent_at <= now() - (%s || ' days')::interval
        ORDER BY q.sent_at ASC
        """,
        (days_since_sent,),
    )
    results = cur.fetchall()
    cur.close()
    conn.close()
    return results


def get_expired_quotes() -> list[dict]:
    conn = get_connection()
    cur = get_dict_cursor(conn)

    cur.execute(
        """
        SELECT quote_number, account_id
        FROM quotes
        WHERE status = 'sent' AND expires_at < CURRENT_DATE
        """
    )
    to_expire = cur.fetchall()

    for row in to_expire:
        cur.execute(
            "UPDATE quotes SET status = 'expired' WHERE quote_number = %s RETURNING quote_id",
            (row["quote_number"],),
        )
        quote_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO quote_status_history (quote_id, status, note) VALUES (%s, 'expired', 'Auto-expired: past expiration date with no response')",
            (quote_id,),
        )

    conn.commit()
    cur.close()
    conn.close()
    return to_expire


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check for quotes needing follow-up")
    parser.add_argument("--days", type=int, default=7,
                         help="Flag quotes sent at least this many days ago with no response")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    expired = get_expired_quotes()
    if expired:
        print(f"Auto-expired {len(expired)} quote(s) past their expiration date:")
        for q in expired:
            print(f"  {q['quote_number']}")
        print()

    needs_follow_up = get_quotes_needing_follow_up(args.days)
    if not needs_follow_up:
        print(f"No quotes need follow-up (sent >= {args.days} days ago with no response).")
    else:
        print(f"{len(needs_follow_up)} quote(s) need follow-up (sent >= {args.days} days ago):\n")
        for q in needs_follow_up:
            print(f"  {q['quote_number']} | {q['account_name']} | "
                  f"${q['quote_total']:.2f} | sent {q['days_since_sent']} days ago | "
                  f"contact: {q['contact_name']} <{q['contact_email']}>")
