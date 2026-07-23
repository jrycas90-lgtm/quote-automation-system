"""
account_alerts.py

Account-specific alerts/instructions -- free-form notes staff should see
every time they prepare a quote for a given account, e.g. "no Hardware
or Fuel charges for this account," "submit via their portal to Jane Doe,"
"onsite work pre-approved up to $2,000." Each account can have several;
each is its own row so they can be added or removed individually without
disturbing the others. Displayed prominently on the New Quote page as
soon as a service order is looked up.
"""

from __future__ import annotations
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from db import get_connection, get_dict_cursor


def get_alerts_for_account(account_id: int) -> list[dict]:
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute(
        "SELECT id, message, created_at FROM account_alerts WHERE account_id = %s ORDER BY created_at ASC",
        (account_id,),
    )
    result = cur.fetchall()
    cur.close()
    conn.close()
    return result


def add_alert(account_id: int, message: str) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO account_alerts (account_id, message) VALUES (%s, %s)",
        (account_id, message),
    )
    conn.commit()
    cur.close()
    conn.close()


def remove_alert(alert_id: int) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM account_alerts WHERE id = %s", (alert_id,))
    conn.commit()
    cur.close()
    conn.close()


def get_all_accounts() -> list[dict]:
    """For the Settings page's account picker."""
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute("SELECT account_id, account_name FROM accounts ORDER BY account_name")
    result = cur.fetchall()
    cur.close()
    conn.close()
    return result
