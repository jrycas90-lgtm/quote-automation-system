# Quote Automation System

A database-backed quoting platform for commercial door and access-control service work, built to replace a spreadsheet-and-email workflow with something auditable.

**Live demo:** https://quote-automation-demo.streamlit.app/

---

## Background

Service quoting in the field-service trades often runs on a pair of Excel workbooks: a master price list cross-tabbing every customer against every part, and a quote template that pulls prices across with a lookup formula. It works until it doesn't. Two people open the price list at once and one silently overwrites the other. Nobody can answer what a customer was quoted six months ago without digging through an inbox. A finished quote gets exported to PDF, emailed, and then exists nowhere the business can see it, so no one knows how many quotes are outstanding, how long they have been sitting, or how many actually close.

This project rebuilds that workflow as a real application. It comes out of firsthand experience administering commercial door hardware quotes, and its design decisions are shaped by problems that came up in practice rather than by a generic CRUD example.

Everything in the repository is synthetic. Accounts, parts, pricing, technicians, and contractors are fabricated, and no proprietary data from any employer appears anywhere in it.

---

## The workflow it models

A job usually begins with a customer call. A representative opens a service order, dispatch schedules a technician, and the technician diagnoses the problem on site. If the repair falls within the account's pre-authorized spending limit and the parts are on the truck, the work is completed on that first visit. If not, a second service order is raised for the return trip once parts are approved and ordered.

Roughly 80 percent of jobs therefore span two linked service orders, and quoting has to account for both. The system models that relationship directly rather than treating each service order as an isolated event.

---

## What it does

**Intake.** Customer service representatives submit the service order number, what the technician found, and the parts needed. This replaces emailing a scratch sheet to the quoting team, and makes the handoff measurable: how long requests wait, and how many are outstanding at any point.

**Quote building.** Entering a service order number populates the account, contact, and site automatically. Parts price against the account's negotiated rates with a list-price fallback. Trip charges, labor, fuel, and hardware are added as distinct charge types, and sales tax is calculated from the state of the service location, honoring account-level exemptions.

**Revisions.** When a technician returns and the original repair did not hold, the quote is revised rather than edited. The prior version is preserved intact, since it may already have been approved and paid, and the new revision carries forward previously quoted items along with the date they were first quoted. What is new and what is not stays obvious.

**Templates.** Recurring work such as modernization kits is defined once by a supervisor and applied in a click. Template lines either price per account or hold a fixed flat rate, since packaged work is sold at a set price regardless of customer.

**Subcontractors.** Work outside the service area is sometimes placed with a general contractor who charges a different rate than the customer pays. The system tracks both prices against the same line items and renders two documents: the customer's quote, and a contractor copy at contractor rates. Contractor pricing and identity never appear on customer-facing output.

**Pricing history.** Account pricing is stored with effective and expiration dates rather than being overwritten, so historical rates remain queryable. The interface shows what a part has cost an account over time, what it was actually quoted at on each job, and any parts quoted at inconsistent prices.

**Audit trail.** Every action against a quote is recorded with the user who performed it and a timestamp. Opening a quote a colleague revised last week shows exactly what changed and who changed it.

**Reporting.** Win rate, revenue by account, most and least quoted parts, quotes needing follow-up, intake turnaround time, and margin by contractor. Every report exports to CSV.

---

## Design decisions worth noting

**Price history instead of price overwrites.** The `account_pricing` table stores effective and expiration dates rather than a single mutable price. This addresses the failure mode of the spreadsheet original directly, where changing a price destroyed any record of what came before it.

**Revisions as new records.** A revision creates a new row and marks the prior one superseded rather than mutating it. Quotes are approved and paid against specific numbers, and editing one in place would erase the record of what the customer actually agreed to. Reporting filters to current revisions so a quote revised three times is not counted three times.

**Contractor pricing on shared line items.** Customer and contractor prices live on the same line items rather than in two separate quotes. Two disconnected documents would drift apart the moment one was edited and the other was not, and a contractor quote that disagrees about the scope of work is worse than no quote at all. The rendering layer takes an explicit audience argument and defaults to the customer view, so the safe output is the one produced by default.

**Technicians without accounts.** Technicians are tracked for record-keeping but have no logins. They never interact with the system, so issuing credentials would create attack surface for users who do not exist. Their identities are excluded from customer-facing documents, and that exclusion is enforced by tests that fail if it is ever violated.

**One business-logic layer, several interfaces.** Quote construction, PDF generation, follow-up detection, and reporting are shared by the web interface and a typed REST API. Business rules live in one place regardless of how they are invoked.

**Warnings rather than hard blocks.** Spending-limit overages and pricing anomalies surface as warnings instead of preventing a quote from being issued. Each has legitimate exceptions, such as a genuine no-charge warranty part or a real bulk order, and the judgment belongs to the person rather than the system.

---

## Tech stack

PostgreSQL, Python, FastAPI, Streamlit, ReportLab, Altair, pytest, Docker.

Schema changes are managed through versioned migrations with a runner that tracks what has been applied to each database. The test suite covers quote construction, revision integrity, pricing rules, and the confidentiality guarantees around technician and contractor data.

---

## Repository layout

```
src/        business logic: quoting, pricing, tax, revisions, reporting, PDF generation
api/        REST layer over the same logic
sql/        schema, seed data, and versioned migrations
scripts/    data generation, migration runner, maintenance utilities
tests/      integration tests against a real database
docs/       workflow comparison, sample output, API examples
```

---

## License

MIT
