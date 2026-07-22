# Before / After: What This Replaces

This project is based on a real quote-administration workflow I used in a previous role, rebuilt from scratch with synthetic data — no proprietary pricing, accounts, or parts from any real employer are included anywhere in this repo.

## The original workflow

- **Master Price List**: a single Excel workbook with a tab per something and a formula-driven cross-tab of every account against every part's price. One source of truth, but a fragile one — anyone with it open could overwrite someone else's changes, and there was no way to see what a price *used to be*.
- **Quote Template**: a two-tab workbook (a "scratch sheet" and a "quotation" tab). Typing a service order number (a "500 number") into the scratch sheet triggered a lookup that auto-filled the account; the rest — part numbers, quantities — was filled in by hand, with price pulled via formula from the Master Price List.
- **Baan (ERP)**: the actual system of record for service orders, but disconnected from pricing entirely. A human had to manually copy the service order number from Baan into the spreadsheet.
- **Sending & follow-up**: the finished quotation tab was exported as a PDF and emailed manually. There was no tracking of what had been sent, to whom, or whether it needed a follow-up — that lived in memory or an inbox, not a system.

## What was actually broken

| Problem | Root cause |
|---|---|
| Pricing could silently drift | No locking, no history — a formula error or overwrite wasn't visible until a customer complained |
| No audit trail | "What did we quote them last time" meant digging through old emails |
| Manual data entry | The service order number was retyped by hand instead of pulled from Baan directly |
| Zero pipeline visibility | No way to see how many quotes were outstanding, aging, or what the win rate was |
| Didn't scale | More reps, accounts, and parts made the spreadsheet more fragile, not more capable |

## How this project addresses each one

| Original problem | This project's fix |
|---|---|
| Master Price List as a fragile cross-tab | `account_pricing` table with `effective_date`/`expired_date` — full price history, queryable, no overwrite risk |
| Manual "500 number" entry | `baan_sync.py` syncs service orders from an ERP export automatically — the number exists in the system the moment it exists in the ERP |
| VLOOKUP-based pricing on the scratch sheet | `quote_service.lookup_price()` — same auto-populate behavior, backed by a real query with a list-price fallback |
| Manual PDF export | `pdf_generator.py` renders a PDF straight from the stored quote data, not from whatever happened to be on screen |
| No tracking of sent/outstanding quotes | `quotes` + `quote_status_history` tables give every quote a full status timeline |
| No follow-up visibility | `follow_up.py` flags quotes sent N+ days ago with no response — this didn't exist at all before |
| No reporting | `reporting.py` answers win rate, revenue by account, and quote-to-close time in a few queries instead of a manual tally |

## What I'd do next in a real deployment

- Replace `data/baan_export.csv` with an actual scheduled pull from Baan (ODBC, ERP export job, or REST API depending on the Baan/Infor LN version)
- Add authentication so the Streamlit app is only accessible to quote admins
- Auto-send the PDF via email on quote generation instead of a manual "Mark as Sent" click
- Add a scheduled job (cron or Airflow) to run `follow_up.py` daily and post reminders to Slack/email instead of requiring someone to check the dashboard
