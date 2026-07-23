"""
erp_sync.py

Syncs service orders from the ERP into the local database.

This is the piece that replaces the manual step in the original workflow --
a quote admin typing a "500 number" into a scratch sheet by hand. Here, the
sync reads from `data/erp_export.csv` (standing in for a real ERP
export/ODBC feed) and upserts into `service_orders`. Once a service order
exists in the ERP, it exists here automatically -- no typing required.

In production this would instead pull from:
  - a scheduled flat-file export the ERP already produces, or
  - a direct ODBC/linked-server connection into the ERP's tables, or
  - a REST API if the ERP exposes one

...but the sync logic (upsert by service_order_no, map account_number ->
account_id, log what changed) stays the same regardless of the source.

Usage:
    python src/erp_sync.py --file data/erp_export.csv
"""

from __future__ import annotations
import argparse
import csv
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from db import get_connection


def sync_service_orders(csv_path: str) -> dict:
    conn = get_connection()
    cur = conn.cursor()

    inserted = 0
    updated = 0
    skipped_unknown_account = 0
    pending_links: list[tuple[str, str]] = []
    linked = 0

    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cur.execute(
                "SELECT account_id FROM accounts WHERE account_number = %s",
                (row["account_number"],),
            )
            result = cur.fetchone()
            if result is None:
                skipped_unknown_account += 1
                continue
            account_id = result[0]

            cur.execute(
                "SELECT service_order_no FROM service_orders WHERE service_order_no = %s",
                (row["service_order_no"],),
            )
            exists = cur.fetchone() is not None

            # order_type / parent / NTE are newer columns. Fall back
            # gracefully if an older export doesn't have them: the
            # 2xxxxx-initial / 5xxxxx-return convention is the ERP's own,
            # so order_type can be inferred from the number itself.
            so_no = row["service_order_no"]
            order_type = (row.get("order_type") or "").strip()
            if not order_type:
                order_type = "initial" if so_no.startswith("2") else (
                    "return" if so_no.startswith("5") else "initial"
                )
            parent_so = (row.get("parent_service_order_no") or "").strip() or None
            nte_raw = (row.get("nte_amount") or "").strip()
            nte_amount = float(nte_raw) if nte_raw else None

            cur.execute(
                """
                INSERT INTO service_orders
                    (service_order_no, account_id, order_date, site_address, description,
                     erp_status, order_type, nte_amount, synced_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (service_order_no) DO UPDATE SET
                    account_id = EXCLUDED.account_id,
                    order_date = EXCLUDED.order_date,
                    site_address = EXCLUDED.site_address,
                    description = EXCLUDED.description,
                    erp_status = EXCLUDED.erp_status,
                    order_type = EXCLUDED.order_type,
                    nte_amount = EXCLUDED.nte_amount,
                    synced_at = now()
                """,
                (
                    so_no,
                    account_id,
                    row["order_date"],
                    row["site_address"],
                    row["description"],
                    row["erp_status"],
                    order_type,
                    nte_amount,
                ),
            )

            # Parent links are applied in a second pass (below) so the
            # parent row is guaranteed to exist first -- the export isn't
            # necessarily ordered parents-before-children.
            if parent_so:
                pending_links.append((so_no, parent_so))

            if exists:
                updated += 1
            else:
                inserted += 1

    # Second pass: link return trips back to their initial trip now that
    # every service order row is guaranteed to exist.
    for child_so, parent_so in pending_links:
        cur.execute("SELECT 1 FROM service_orders WHERE service_order_no = %s", (parent_so,))
        if cur.fetchone() is None:
            continue
        cur.execute(
            "UPDATE service_orders SET parent_service_order_no = %s WHERE service_order_no = %s",
            (parent_so, child_so),
        )
        linked += 1

    conn.commit()
    cur.close()
    conn.close()

    return {
        "inserted": inserted,
        "updated": updated,
        "skipped_unknown_account": skipped_unknown_account,
        "linked": linked,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync service orders from ERP export")
    parser.add_argument("--file", type=str, default="data/erp_export.csv")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    result = sync_service_orders(args.file)
    print(f"Sync complete: {result['inserted']} new, {result['updated']} updated, "
          f"{result['linked']} linked to an initial trip, "
          f"{result['skipped_unknown_account']} skipped (unknown account).")
