# API Usage & Verified Sample Responses

The REST API in `api/` wraps the same business logic used by the Streamlit app (`app.py`) — `src/quote_service.py`, `src/pdf_generator.py`, `src/follow_up.py`, and `src/reporting.py` — behind typed, documented HTTP endpoints. Every response below was captured from actually running the API against the live database, not written by hand.

## Running it

```bash
uvicorn api.main:app --reload
```

Interactive docs (auto-generated from the Pydantic schemas in `api/schemas.py`) are then live at **http://127.0.0.1:8000/docs** — you can try every endpoint directly from the browser without writing any client code.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness check |
| GET | `/service-orders/{service_order_no}` | Auto-populate lookup — the "500 number" step |
| GET | `/parts` | List the part catalog |
| GET | `/accounts/{account_id}/pricing/{part_number}` | Account-specific price lookup |
| POST | `/quotes` | Create a quote (service order + line items) |
| GET | `/quotes` | List quotes, filterable by `status` and `account_id` |
| GET | `/quotes/{quote_number}` | Full quote detail |
| POST | `/quotes/{quote_number}/pdf` | Generate/regenerate and return the PDF |
| POST | `/quotes/{quote_number}/send` | Generate PDF (if needed) + mark as sent |
| GET | `/reports/pipeline` | Win rate, status breakdown, revenue by account, top parts |
| GET | `/reports/follow-up?days=7` | Quotes needing follow-up |

## Verified example: service order lookup

```
GET /service-orders/500125
```
```json
{
  "service_order_no": "500125",
  "account_id": 3,
  "account_name": "Lakeshore Medical Campus",
  "contact_name": "Priya Nair",
  "contact_email": "pnair@lakeshoremed.example",
  "site_address": "1474 Broadway, Madison, WI"
}
```

Unknown service order:
```
GET /service-orders/999999
```
```
HTTP 404
{"detail": "Service order 999999 not found. Has it been synced yet? Run src/erp_sync.py."}
```

## Verified example: creating a quote

```
POST /quotes
Content-Type: application/json

{
  "service_order_no": "500125",
  "created_by": "API Test",
  "line_items": [
    {"part_number": "HW-2210", "quantity": 2},
    {"part_number": "HW-2215", "quantity": 3}
  ]
}
```

```
HTTP 201
{
  "quote_number": "Q-2026-00056",
  "account_id": 3,
  "account_name": "Lakeshore Medical Campus",
  "line_items": [
    {"part_number": "HW-2210", "description": "Electromagnetic Door Lock", "quantity": 2, "unit_price": 130.14, "line_total": 260.28},
    {"part_number": "HW-2215", "description": "Card Reader - Proximity", "quantity": 3, "unit_price": 166.35, "line_total": 499.05}
  ],
  "total": 759.33,
  "status": "draft"
}
```

Note the pricing came back correctly resolved per-account (the same `lookup_price()` logic used by the Streamlit app), without the client needing to know or send any pricing data.

## Verified example: generating the PDF

```
POST /quotes/Q-2026-00056/pdf
```
Returns the actual PDF file (`Content-Type: application/pdf`) — confirmed 200 OK, 2,820 bytes, a real rendered document, streamed directly back rather than requiring a separate download step.

## Verified example: pipeline report

```
GET /reports/pipeline
```
```json
{
  "win_rate_pct": 48.6,
  "status_breakdown": [
    {"status": "sent", "quote_count": 21, "pct_of_total": 37.5, "total_value": 61149.13},
    {"status": "accepted", "quote_count": 17, "pct_of_total": 30.4, "total_value": 38104.71},
    {"status": "declined", "quote_count": 13, "pct_of_total": 23.2, "total_value": 27846.80},
    {"status": "expired", "quote_count": 5, "pct_of_total": 8.9, "total_value": 13029.42}
  ],
  "avg_days_to_accept": 34.6,
  "avg_days_to_decline": 27.8
}
```

Same numbers as `python src/reporting.py`, just served as JSON instead of printed to a console — this is what makes it usable by another tool (a dashboard, a Slack bot, a scheduled digest) instead of only a human reading terminal output.

## Verified example: follow-up tracking

```
GET /reports/follow-up?days=7
```
```json
{
  "count": 16,
  "days_threshold": 7,
  "quotes": [
    {
      "quote_number": "Q-2026-00026",
      "account_name": "Coastal Retail Holdings",
      "contact_name": "Marcus Ibe",
      "contact_email": "mibe@coastalretail.example",
      "days_since_sent": 57,
      "quote_total": 924.72
    }
  ]
}
```

## Error handling

Validation errors (e.g., creating a quote with zero line items) return proper `422` responses with field-level detail, generated automatically by Pydantic from the schemas in `api/schemas.py` — not hand-rolled error strings. Business-logic errors (unknown service order, unknown part, unknown quote) return `404` with a clear message, mapped from the same custom exceptions (`UnknownServiceOrderError`, `UnknownPartError`) already used by the CLI and Streamlit layers — the error handling logic lives in one place, not duplicated per interface.

## Tests

```bash
pytest api/tests/
```

12 integration tests using FastAPI's `TestClient`, run against the real database — covering successful lookups, 404s, validation errors, quote creation end-to-end, and every reporting endpoint.
