"""
generate_demo_quotes.py

Populates the database with a realistic history of quotes across many
service orders -- varying statuses, ages, and outcomes -- so the follow-up
tracker and reporting dashboard have real data to demonstrate against
instead of an empty database.

This simulates several months of a quote admin's actual usage of the
system. Run this AFTER erp_sync.py.

Usage:
    python scripts/generate_demo_quotes.py
"""

from __future__ import annotations
import random
from datetime import datetime, timedelta
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent / "src"))
from db import get_connection, get_dict_cursor
from quote_service import (
    start_quote_from_service_order, add_line_item, save_quote,
)

random.seed(11)

CREATORS = ["J. Rycas", "M. Alvarez", "T. Nguyen"]


def get_all_service_orders_and_parts():
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute("SELECT service_order_no FROM service_orders")
    orders = [r["service_order_no"] for r in cur.fetchall()]
    cur.execute("SELECT part_number FROM parts")
    parts = [r["part_number"] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return orders, parts


def backdate_quote(quote_number: str, created_days_ago: int) -> None:
    """Push created_at back in time so the quote history spans months,
    not all landing on 'today' like a freshly seeded database would."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE quotes SET created_at = now() - (%s || ' days')::interval WHERE quote_number = %s",
        (created_days_ago, quote_number),
    )
    conn.commit()
    cur.close()
    conn.close()


def progress_quote_status(quote_number: str, created_days_ago: int) -> None:
    """Randomly advances a quote through sent -> accepted/declined/expired/
    still-sent, with a sent_at date consistent with how old the quote is."""
    conn = get_connection()
    cur = conn.cursor()

    if created_days_ago < 2 and random.random() < 0.3:
        cur.close()
        conn.close()
        return

    sent_days_after_creation = random.randint(0, 2)
    sent_days_ago = max(created_days_ago - sent_days_after_creation, 0)

    cur.execute(
        "UPDATE quotes SET status = 'sent', sent_at = now() - (%s || ' days')::interval, "
        "pdf_path = %s WHERE quote_number = %s RETURNING quote_id",
        (sent_days_ago, f"output/{quote_number}.pdf", quote_number),
    )
    quote_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO quote_status_history (quote_id, status, note) VALUES (%s, 'sent', 'PDF generated and sent to customer')",
        (quote_id,),
    )

    days_since_sent = sent_days_ago
    if days_since_sent > 10:
        outcome = random.choices(
            ["accepted", "declined", "expired", "sent"],
            weights=[0.45, 0.20, 0.15, 0.20],
        )[0]
    elif days_since_sent > 4:
        outcome = random.choices(
            ["accepted", "declined", "sent"],
            weights=[0.35, 0.15, 0.50],
        )[0]
    else:
        outcome = "sent"

    if outcome != "sent":
        cur.execute(
            "UPDATE quotes SET status = %s WHERE quote_number = %s",
            (outcome, quote_number),
        )
        note = {
            "accepted": "Customer accepted the quote",
            "declined": "Customer declined the quote",
            "expired": "Auto-expired: past expiration date with no response",
        }[outcome]
        cur.execute(
            "INSERT INTO quote_status_history (quote_id, status, note) VALUES (%s, %s, %s)",
            (quote_id, outcome, note),
        )

    conn.commit()
    cur.close()
    conn.close()


def main(n_quotes: int = 55):
    orders, parts = get_all_service_orders_and_parts()
    random.shuffle(orders)

    created = 0
    for service_order_no in orders:
        if created >= n_quotes:
            break

        try:
            draft = start_quote_from_service_order(service_order_no)
        except Exception:
            continue

        n_items = random.randint(1, 5)
        chosen_parts = random.sample(parts, k=min(n_items, len(parts)))
        for part_number in chosen_parts:
            qty = random.randint(1, 10)
            draft = add_line_item(draft, part_number, qty)

        created_days_ago = random.randint(0, 60)
        # Create the quote AT its backdated timestamp so the generated
        # quote number carries that date too -- otherwise every backdated
        # quote would be numbered with today's date, which reads wrong.
        created_at = datetime.now() - timedelta(days=created_days_ago)
        quote_number = save_quote(
            draft,
            created_by=random.choice(CREATORS),
            expires_in_days=30,
            created_at=created_at,
        )
        progress_quote_status(quote_number, created_days_ago)

        created += 1

    print(f"Generated {created} demo quotes with realistic status history.")


if __name__ == "__main__":
    main()
