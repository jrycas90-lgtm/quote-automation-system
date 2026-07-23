"""
reset_demo_quotes.py

Clears existing quote history and regenerates it using the current
account-and-date quote numbering (e.g. UNI-2026-07-23-01), so a demo
database doesn't show a confusing mix of old Q-2026-00042 numbers
alongside new ones.

WHY REGENERATE RATHER THAN JUST DELETE: the quote history IS the demo.
Deleting it empties the Dashboard -- no win rate, no revenue by account,
no top parts, no follow-up list -- which makes the system look far less
capable than it is. This wipes and reseeds so the numbering is consistent
AND the pipeline still has data in it.

THIS IS DESTRUCTIVE. It deletes every quote, line item, status history
entry, and activity record in the target database. It is intended for
demo/synthetic data only. Never point it at a database holding real
quotes -- there is no undo.

Two foreign keys into `quotes` do NOT cascade (quotes.supersedes_quote_id,
intake_requests.quote_id), so those are unlinked explicitly first;
otherwise the delete fails partway through.

Usage:
    python scripts/reset_demo_quotes.py            # prompts for confirmation
    python scripts/reset_demo_quotes.py --yes      # skip the prompt
    python scripts/reset_demo_quotes.py --count 60 # how many quotes to seed
"""

from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent / "src"))
sys.path.append(str(Path(__file__).resolve().parent))
from db import get_connection


def current_counts() -> dict:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE quote_number LIKE 'Q-%') AS old_format,
            COUNT(*) FILTER (WHERE quote_number NOT LIKE 'Q-%') AS new_format,
            COUNT(*) AS total
        FROM quotes
        """
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    return {"old_format": row[0], "new_format": row[1], "total": row[2]}


def wipe_quotes() -> int:
    """Removes all quotes and everything hanging off them."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        # Unlink the two non-cascading references first
        cur.execute("UPDATE intake_requests SET quote_id = NULL, status = 'pending' WHERE quote_id IS NOT NULL")
        cur.execute("UPDATE quotes SET supersedes_quote_id = NULL WHERE supersedes_quote_id IS NOT NULL")

        # These cascade from quotes, but delete explicitly so the counts
        # are visible and the intent is obvious to anyone reading this.
        cur.execute("DELETE FROM quote_activity")
        cur.execute("DELETE FROM quote_status_history")
        cur.execute("DELETE FROM quote_line_items")
        cur.execute("DELETE FROM quotes")
        deleted = cur.rowcount

        conn.commit()
        return deleted
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset demo quote history to the current numbering")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    parser.add_argument("--count", type=int, default=55, help="how many demo quotes to generate")
    args = parser.parse_args()

    counts = current_counts()
    print(f"Target database currently holds {counts['total']} quote(s): "
          f"{counts['old_format']} old-format (Q-...), {counts['new_format']} new-format.")

    if counts["total"] == 0:
        print("Nothing to clear.")
    else:
        print("\nThis will DELETE every quote, line item, status history entry, and")
        print("activity record in this database, then regenerate demo history.")
        print("Intended for synthetic/demo data only -- there is no undo.\n")
        if not args.yes:
            answer = input("Type 'reset' to continue: ").strip().lower()
            if answer != "reset":
                print("Aborted -- nothing was changed.")
                return 1

        deleted = wipe_quotes()
        print(f"Deleted {deleted} quote(s) and all related records.")

    print(f"\nGenerating {args.count} demo quotes with current numbering...")
    import generate_demo_quotes
    generate_demo_quotes.main(n_quotes=args.count)

    after = current_counts()
    print(f"\nDone. {after['total']} quote(s) now present "
          f"({after['old_format']} old-format, {after['new_format']} new-format).")
    if after["old_format"]:
        print("NOTE: some old-format numbers remain -- rerun if that's unexpected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
