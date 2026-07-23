"""
pricing_checks.py

Two guardrails that run over a quote before it goes out.

1. NTE ENFORCEMENT. An account pre-authorizes a "Not To Exceed" amount
   for a job. If the quote comes in over that, the work can't just
   proceed -- the customer has to approve an increase before parts are
   ordered. The NTE was previously displayed but nothing acted on it, so
   it was possible to send a quote straight past the ceiling.

2. PRICING ANOMALIES. Quotes over a dollar threshold used to be routed
   to a supervisor "because sometimes the parts were not priced
   properly." The dollar amount is a proxy for the real concern, and a
   poor one: it waves through a mispriced $400 quote and stops a
   perfectly correct $4,000 one. These checks look at the actual thing --
   prices well off the account's usual rate, zero-dollar lines, quantities
   that look like typos -- which is the kind of check a person can't do
   reliably by eye across every line.

Nothing here blocks anything. It surfaces warnings for a human to judge,
because every one of these has a legitimate exception (a genuinely free
warranty part, a real bulk order).
"""

from __future__ import annotations
import sys
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).resolve().parent))
from db import get_connection, get_dict_cursor

# A line more than this far from the part's usual price is worth a look.
PRICE_VARIANCE_THRESHOLD = 0.25   # 25%
# Quantities above this are usually a typo (e.g. "12" typed as "112").
HIGH_QUANTITY_THRESHOLD = 50


def get_nte(service_order_no: Optional[str]) -> Optional[float]:
    """The NTE ceiling on a service order, or None if none is on file."""
    if not service_order_no:
        return None
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute(
        "SELECT nte_amount FROM service_orders WHERE service_order_no = %s",
        (service_order_no,),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row is None or row["nte_amount"] is None:
        return None
    return float(row["nte_amount"])


def check_nte(service_order_no: Optional[str], quote_total: float) -> Optional[dict]:
    """Compares a quote total against the NTE on its service order.

    Returns None when there's no NTE on file or the quote is within it;
    otherwise a dict describing the overage, including how much extra
    authorization is needed."""
    nte = get_nte(service_order_no)
    if nte is None or quote_total <= nte:
        return None
    overage = round(quote_total - nte, 2)
    return {
        "nte": nte,
        "quote_total": round(quote_total, 2),
        "overage": overage,
        "message": (
            f"Quote total ${quote_total:,.2f} exceeds the ${nte:,.2f} NTE on file "
            f"by ${overage:,.2f}. The customer needs to authorize an increase "
            f"before parts are ordered."
        ),
    }


def _reference_prices(account_id: int, part_numbers: list[str]) -> dict[str, float]:
    """The price we'd normally expect for each part on this account --
    its negotiated rate if there is one, otherwise catalog list price."""
    if not part_numbers:
        return {}
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute(
        """
        SELECT p.part_number,
               COALESCE(
                   (SELECT ap.price
                    FROM account_pricing ap
                    WHERE ap.account_id = %s
                      AND ap.part_number = p.part_number
                      AND ap.effective_date <= CURRENT_DATE
                      AND (ap.expired_date IS NULL OR ap.expired_date > CURRENT_DATE)
                    ORDER BY ap.effective_date DESC
                    LIMIT 1),
                   p.list_price
               ) AS expected_price
        FROM parts p
        WHERE p.part_number = ANY(%s)
        """,
        (account_id, part_numbers),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {r["part_number"]: float(r["expected_price"]) for r in rows}


def check_line_items(account_id: int, line_items: list) -> list[dict]:
    """Scans line items for pricing that looks wrong.

    `line_items` are QuoteLineItem-style objects (part_number,
    description, quantity, unit_price, item_type). Returns a list of
    warning dicts -- empty means nothing looked unusual.
    """
    warnings: list[dict] = []
    catalog_parts = [
        li.part_number for li in line_items
        if getattr(li, "part_number", None) and getattr(li, "item_type", "part") != "tax"
    ]
    expected = _reference_prices(account_id, catalog_parts) if catalog_parts else {}

    for li in line_items:
        if getattr(li, "item_type", "part") == "tax":
            continue

        desc = li.description
        qty = li.quantity
        price = float(li.unit_price)

        if price == 0:
            warnings.append({
                "severity": "high",
                "line": desc,
                "message": f"{desc}: priced at $0.00 -- is this intentional (warranty/no-charge)?",
            })

        if qty > HIGH_QUANTITY_THRESHOLD:
            warnings.append({
                "severity": "medium",
                "line": desc,
                "message": f"{desc}: quantity of {qty} is unusually high -- possible typo?",
            })

        part_number = getattr(li, "part_number", None)
        if part_number and part_number in expected and price > 0:
            ref = expected[part_number]
            if ref > 0:
                variance = (price - ref) / ref
                if abs(variance) >= PRICE_VARIANCE_THRESHOLD:
                    direction = "above" if variance > 0 else "below"
                    warnings.append({
                        "severity": "high" if abs(variance) >= 0.5 else "medium",
                        "line": desc,
                        "message": (
                            f"{desc} ({part_number}): quoted at ${price:,.2f}, "
                            f"which is {abs(variance) * 100:.0f}% {direction} the expected "
                            f"${ref:,.2f} for this account."
                        ),
                    })

    return warnings


def run_all_checks(account_id: int, service_order_no: Optional[str],
                   line_items: list, quote_total: float) -> dict:
    """Convenience wrapper: every check in one call.

    Returns {"nte": <dict|None>, "pricing": [warnings], "has_issues": bool}
    """
    nte_issue = check_nte(service_order_no, quote_total)
    pricing = check_line_items(account_id, line_items)
    return {
        "nte": nte_issue,
        "pricing": pricing,
        "has_issues": bool(nte_issue) or bool(pricing),
    }
