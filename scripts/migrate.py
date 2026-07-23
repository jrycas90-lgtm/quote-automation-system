"""
migrate.py

Applies any database migrations that haven't been run yet, in order, and
records what it applied.

Why this exists: migrations were being run by hand, one psql command at a
time, across two databases (local Docker and the deployed Supabase
instance), with the only record of "did I run 003 on Supabase?" being
memory. That's how environments silently drift apart -- and you don't
find out until the deployed app crashes on a column that doesn't exist.

This tracks applied migrations in a `schema_migrations` table so the
answer is a query, not a recollection.

Usage:
    python scripts/migrate.py            # apply anything outstanding
    python scripts/migrate.py --status   # show what's applied, change nothing
    python scripts/migrate.py --dry-run  # show what WOULD be applied

Connection settings come from the same environment variables everything
else uses (QUOTE_DB_HOST etc.), so point it at whichever database you
mean to migrate -- see scripts/dev-env.ps1 / dev-env.sh.

Note: the existing migrations (001-003) are all written to be safe to
re-run, so if a database already had them applied by hand, letting this
runner apply them again is harmless -- it just brings the tracking table
in line with reality.
"""

from __future__ import annotations
import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent / "src"))
from db import get_connection

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "sql" / "migrations"


def ensure_tracking_table(conn) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename    VARCHAR(200) PRIMARY KEY,
            applied_at  TIMESTAMP NOT NULL DEFAULT now()
        )
        """
    )
    conn.commit()
    cur.close()


def applied_migrations(conn) -> set[str]:
    cur = conn.cursor()
    cur.execute("SELECT filename FROM schema_migrations")
    rows = {r[0] for r in cur.fetchall()}
    cur.close()
    return rows


def available_migrations() -> list[Path]:
    if not MIGRATIONS_DIR.exists():
        return []
    return sorted(MIGRATIONS_DIR.glob("*.sql"), key=lambda p: p.name)


def apply_migration(conn, path: Path) -> None:
    """Runs one migration file inside a single transaction, so a failure
    part-way through rolls back rather than leaving the schema in a
    half-migrated state."""
    sql_text = path.read_text()
    cur = conn.cursor()
    try:
        cur.execute(sql_text)
        cur.execute(
            "INSERT INTO schema_migrations (filename) VALUES (%s) ON CONFLICT DO NOTHING",
            (path.name,),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def show_status(conn) -> None:
    done = applied_migrations(conn)
    available = available_migrations()
    if not available:
        print("No migration files found in sql/migrations/.")
        return
    print(f"{'STATUS':<10} MIGRATION")
    print("-" * 60)
    for path in available:
        print(f"{'applied' if path.name in done else 'PENDING':<10} {path.name}")
    pending = [p for p in available if p.name not in done]
    print("-" * 60)
    print(f"{len(available) - len(pending)} applied, {len(pending)} pending.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply outstanding database migrations")
    parser.add_argument("--status", action="store_true", help="show what's applied, change nothing")
    parser.add_argument("--dry-run", action="store_true", help="show what would be applied, change nothing")
    args = parser.parse_args()

    conn = get_connection()
    try:
        ensure_tracking_table(conn)

        if args.status:
            show_status(conn)
            return 0

        done = applied_migrations(conn)
        pending = [p for p in available_migrations() if p.name not in done]

        if not pending:
            print("Database is up to date -- nothing to apply.")
            return 0

        if args.dry_run:
            print(f"{len(pending)} migration(s) would be applied:")
            for path in pending:
                print(f"  {path.name}")
            return 0

        print(f"Applying {len(pending)} migration(s)...")
        for path in pending:
            print(f"  {path.name} ... ", end="", flush=True)
            try:
                apply_migration(conn, path)
                print("done")
            except Exception as e:
                print("FAILED")
                print(f"\nMigration {path.name} failed and was rolled back:\n  {e}")
                print("No further migrations were applied.")
                return 1
        print("All migrations applied.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
