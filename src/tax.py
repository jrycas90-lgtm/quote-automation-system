"""
tax.py

State sales tax lookup and calculation.

Tax EXEMPTION is tracked at the account level (accounts.tax_exempt) --
a customer's exemption certificate applies to them as a business entity,
not to one specific address.

The applicable RATE is determined by the state of the specific service
location on a quote (parsed from site_address), since the same account
can have locations in multiple states with different rates -- e.g. one
customer might have a site in WI and another in NJ, taxed differently.

Rates in state_tax_rates are BASE STATE rates only -- they do not
include county/city/local district add-ons, which vary too granularly
to model here. Seeded with a reasonable 2026 starting point; verify and
adjust via Settings > Tax Rates before relying on these for real
invoicing -- this is not tax advice.
"""

from __future__ import annotations
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).resolve().parent))
from db import get_connection, get_dict_cursor


def extract_state_from_address(address: Optional[str]) -> Optional[str]:
    """Pulls a 2-letter state code out of a 'Street, City, ST' style
    address string. Returns None if it can't confidently find one."""
    if not address:
        return None
    match = re.search(r",\s*([A-Za-z]{2})\s*$", address.strip())
    return match.group(1).upper() if match else None


def get_tax_rate(state_code: Optional[str]) -> Optional[float]:
    """Returns the configured base state tax rate (as a decimal, e.g.
    0.05 for 5%) for a given 2-letter state code, or None if no state
    was given or no rate is configured for it."""
    if not state_code:
        return None
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute("SELECT rate FROM state_tax_rates WHERE state_code = %s", (state_code.upper(),))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return float(row["rate"]) if row else None


def get_all_tax_rates() -> list[dict]:
    """Returns all configured state tax rates, for display/editing on
    the Settings page."""
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute("SELECT state_code, state_name, rate FROM state_tax_rates ORDER BY state_name")
    result = cur.fetchall()
    cur.close()
    conn.close()
    return result


def update_tax_rate(state_code: str, rate: float) -> None:
    """Updates the rate for an existing state (all 50 + DC are seeded
    up front, so this is always an update, never an insert)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE state_tax_rates SET rate = %s WHERE state_code = %s",
        (rate, state_code.upper()),
    )
    conn.commit()
    cur.close()
    conn.close()


def is_account_tax_exempt(account_id: int) -> bool:
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute("SELECT tax_exempt FROM accounts WHERE account_id = %s", (account_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return bool(row["tax_exempt"]) if row else False


def get_all_accounts_tax_status() -> list[dict]:
    """Returns every account with its current tax-exempt flag, for
    display/editing on the Settings page."""
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute(
        "SELECT account_id, account_name, tax_exempt FROM accounts ORDER BY account_name"
    )
    result = cur.fetchall()
    cur.close()
    conn.close()
    return result


def set_account_tax_exempt(account_id: int, exempt: bool) -> None:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE accounts SET tax_exempt = %s WHERE account_id = %s",
        (exempt, account_id),
    )
    conn.commit()
    cur.close()
    conn.close()
