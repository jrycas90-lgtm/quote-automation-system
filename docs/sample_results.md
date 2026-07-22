# Sample Output

Real output from running this system against the included synthetic dataset (10 accounts, 20 parts, 120 synced service orders, 55+ historical quotes). Nothing here is hypothetical — captured directly from `python src/reporting.py` and `python src/follow_up.py`.

## Pipeline report (`src/reporting.py`)

```
======================================================================
QUOTE PIPELINE REPORT
======================================================================

Overall win rate: 48.6%

Status breakdown:
  sent         20 quotes ( 36.4%)  $   60,389.80
  accepted     17 quotes ( 30.9%)  $   38,104.71
  declined     13 quotes ( 23.6%)  $   27,846.80
  expired       5 quotes (  9.1%)  $   13,029.42

Avg days to accept: 34.6
Avg days to decline: 27.8

Revenue by account (accepted quotes):
  Union Station Business Park     3 accepted /  8 total   $ 11,714.83
  Summit Ridge Apartments         3 accepted /  5 total   $ 10,676.71
  Lakeshore Medical Campus        3 accepted /  8 total   $  4,215.11
  Coastal Retail Holdings         2 accepted /  5 total   $  3,820.48
  Meridian Property Group         3 accepted / 11 total   $  3,370.62

Top 10 most-quoted parts:
  HW-2290    ADA Compliant Door Operator         quoted   9x   $ 41,992.67
  HW-2270    Wireless Access Control Gateway     quoted   8x   $ 21,503.46
  HW-2205    Commercial Panic Bar Exit Device    quoted   8x   $ 14,680.67
  HW-2210    Electromagnetic Door Lock           quoted  13x   $ 10,043.95
  HW-2220    Keypad Entry Controller             quoted  13x   $  9,576.86
```

## Follow-up tracker (`src/follow_up.py --days 7`)

```
16 quote(s) need follow-up (sent >= 7 days ago):

  Q-2026-00027 | Coastal Retail Holdings | $924.72 | sent 57 days ago | contact: Marcus Ibe <mibe@coastalretail.example>
  Q-2026-00013 | Ashford Hotel Group | $2747.20 | sent 56 days ago | contact: Renee Dubois <rdubois@ashfordhg.example>
  Q-2026-00046 | Meridian Property Group | $685.60 | sent 52 days ago | contact: Dana Whitfield <dwhitfield@meridianpg.example>
  ...
```

This kind of list — quotes sitting untouched for weeks with no visibility — is exactly the thing that's invisible in a spreadsheet-based workflow and trivial once the data lives in a real database.

## Quote-building workflow (`quote_service.py`)

```
Auto-populated from service order:
  Account: Lakeshore Medical Campus
  Contact: Priya Nair <pnair@lakeshoremed.example>
  Site: 1474 Broadway, Madison, WI

Line items (price auto-looked-up per account):
  HW-2210 x4 @ $130.14 = $520.56  (Electromagnetic Door Lock)
  HW-2215 x4 @ $166.35 = $665.4  (Card Reader - Proximity)
  HW-2330 x6 @ $24.52 = $147.12  (Door Position Sensor)
Total: $1333.08

Saved as Q-2026-00001
```

Entering just the service order number populated the account, contact, and site automatically — the same behavior as the original "500 number" lookup on the scratch sheet, sourced from synced ERP data instead of a second spreadsheet.

## Sample generated quote PDF

`sample_quote_preview.png` in this folder is a rendered preview of an actual PDF this system generated (`pdf_generator.py`), not a mockup — pulled straight from a saved quote's database record.
