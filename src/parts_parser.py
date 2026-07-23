"""
parts_parser.py

Turns the free-text parts list a CCR types on an intake request into
structured line items the quote builder can use directly.

This is the last manual re-keying step in the workflow: the CCR types
what the tech told them, and without this the quote team reads it off
one screen and types it into another -- which is exactly where
transcription errors happen (wrong part, wrong quantity, dropped line).

Deliberately permissive about format, because people type these under
time pressure in whatever shape is natural:

    HW-2201 x2
    HW-2201 x 2
    2x HW-2201
    HW-2201 qty 2
    HW-2201, 2
    HW-2201 - 2 ea
    HW-2201                      (no quantity -> assume 1)
    (2) HW-2201
    Replace closer on north door (no part number -> flagged as custom)

Nothing here is auto-committed to a quote. Parsed results are always
shown for confirmation first, since free text will occasionally be
ambiguous and a silently wrong quote is worse than a slow one.
"""

from __future__ import annotations
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).resolve().parent))
from db import get_connection, get_dict_cursor


# A part number as used in the catalog: letters, then a dash, then digits
# (e.g. HW-2201). Kept loose enough to tolerate lowercase and stray spaces.
PART_PATTERN = re.compile(r"\b([A-Za-z]{1,4})\s*-\s*(\d{2,6})\b")

# Quantity expressed AFTER the part: "x2", "x 2", "qty 2", ", 2", "- 2 ea"
QTY_AFTER = re.compile(
    r"(?:x|qty\.?|quantity|,|-|:)\s*(\d{1,4})\b(?:\s*(?:ea|each|pcs?|units?)\b)?",
    re.IGNORECASE,
)

# Quantity expressed BEFORE the part: "2x HW-2201", "(2) HW-2201"
QTY_BEFORE = re.compile(r"^\s*\(?(\d{1,4})\)?\s*(?:x|-)?\s*(?=[A-Za-z]{1,4}\s*-\s*\d)", re.IGNORECASE)


def _load_catalog() -> dict[str, str]:
    """part_number -> description, for validating what the CCR typed."""
    conn = get_connection()
    cur = get_dict_cursor(conn)
    cur.execute("SELECT part_number, description FROM parts")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {r["part_number"].upper(): r["description"] for r in rows}


def parse_parts_text(text: Optional[str]) -> list[dict]:
    """Parses a free-text parts list into structured entries.

    Returns one dict per non-empty line:
        {
          "raw":          the original line, always kept so the user can
                          see exactly what was typed,
          "part_number":  matched catalog part number, or None,
          "description":  catalog description, or the raw text for
                          unmatched lines,
          "quantity":     parsed quantity (defaults to 1),
          "matched":      True if this maps to a real catalog part,
        }

    Unmatched lines are returned too rather than dropped -- a line the
    parser doesn't understand is exactly the line a human needs to look
    at, and silently discarding it would lose parts off the quote.
    """
    if not text or not text.strip():
        return []

    catalog = _load_catalog()
    results: list[dict] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Strip common list bullets people paste in
        line = re.sub(r"^[\-\*\u2022\d]+[\.\)]?\s*", "", line) if re.match(r"^[\-\*\u2022]\s", line) else line

        quantity = 1
        part_number = None

        before = QTY_BEFORE.match(line)
        part_match = PART_PATTERN.search(line)

        if part_match:
            candidate = f"{part_match.group(1).upper()}-{part_match.group(2)}"
            if candidate in catalog:
                part_number = candidate

            if before:
                quantity = int(before.group(1))
            else:
                # Only look for a quantity AFTER the part number, so the
                # digits inside the part number itself aren't mistaken
                # for a quantity.
                tail = line[part_match.end():]
                after = QTY_AFTER.search(tail)
                if after:
                    quantity = int(after.group(1))

        quantity = max(1, min(quantity, 9999))

        results.append({
            "raw": raw_line.strip(),
            "part_number": part_number,
            "description": catalog.get(part_number, line) if part_number else line,
            "quantity": quantity,
            "matched": part_number is not None,
        })

    return results


def summarize(parsed: list[dict]) -> str:
    """Short human summary, e.g. '3 of 4 lines matched the catalog'."""
    if not parsed:
        return "No parts listed."
    matched = sum(1 for p in parsed if p["matched"])
    return f"{matched} of {len(parsed)} line(s) matched the parts catalog."
