-- 003_linked_orders_revisions_intake.sql
--
-- The big workflow migration. Adds four things the real ASSA-style
-- process needs that the original schema had no concept of:
--
--   1. LINKED SERVICE ORDERS. A 2xxxxx number is the initial diagnostic
--      trip; a 5xxxxx number is the return trip to actually do the work.
--      ~80% of jobs involve both, and the quote team needs to see them
--      together. A return order now points at its initial order.
--
--   2. QUOTE REVISIONS. When a tech goes back out and the door still
--      isn't fixed, the quote gets revised -- sometimes after the
--      original was already paid. Revisions create a NEW row and leave
--      the original untouched, so there's a permanent record of exactly
--      what the customer approved at each stage. Revisions share a
--      quote_number and are distinguished by revision_number
--      ("Q-2026-00056 Rev 2").
--
--   3. QUOTE ACTIVITY LOG. Who did what, to which quote, when. The old
--      quote_status_history only recorded status changes and never
--      recorded WHO made them.
--
--   4. CCR INTAKE. Replaces the "CCR emails a scratch sheet to the quote
--      team" step with a queue inside the app.
--
-- Safe to run against an already-populated database -- only adds, never
-- drops data. Run once per database:
--   psql -h <host> -p <port> -U <user> -d <db> -f sql/migrations/003_linked_orders_revisions_intake.sql

-- ============================================================
-- 1. Linked service orders + NTE
-- ============================================================

ALTER TABLE service_orders
    ADD COLUMN IF NOT EXISTS order_type VARCHAR(20) NOT NULL DEFAULT 'initial';
    -- 'initial' (2xxxxx, diagnostic trip) | 'return' (5xxxxx, repair trip)

ALTER TABLE service_orders
    ADD COLUMN IF NOT EXISTS parent_service_order_no VARCHAR(20);

ALTER TABLE service_orders
    ADD COLUMN IF NOT EXISTS nte_amount NUMERIC(10, 2);
    -- "Not To Exceed" ceiling the account pre-authorized for this job.
    -- NULL = none on file. The app warns when a quote total exceeds it.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'service_orders_parent_fk'
    ) THEN
        ALTER TABLE service_orders
            ADD CONSTRAINT service_orders_parent_fk
            FOREIGN KEY (parent_service_order_no)
            REFERENCES service_orders(service_order_no);
    END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_service_orders_parent
    ON service_orders (parent_service_order_no);

-- Backfill order_type from the number prefix for existing rows: the
-- 2xxxxx / 5xxxxx convention is the ERP's own, so it can be inferred.
UPDATE service_orders
SET order_type = CASE
        WHEN service_order_no LIKE '2%' THEN 'initial'
        WHEN service_order_no LIKE '5%' THEN 'return'
        ELSE order_type
    END;

-- ============================================================
-- 2. Quote revisions
-- ============================================================

ALTER TABLE quotes
    ADD COLUMN IF NOT EXISTS revision_number INTEGER NOT NULL DEFAULT 1;

ALTER TABLE quotes
    ADD COLUMN IF NOT EXISTS supersedes_quote_id INTEGER REFERENCES quotes(quote_id);

ALTER TABLE quotes
    ADD COLUMN IF NOT EXISTS is_current BOOLEAN NOT NULL DEFAULT TRUE;
    -- FALSE once a newer revision supersedes this row. Reporting and
    -- follow-up queries filter on this so a quote revised three times
    -- isn't counted three times in the pipeline.

ALTER TABLE quotes
    ADD COLUMN IF NOT EXISTS revision_reason VARCHAR(300);

-- quote_number was UNIQUE, which revisions violate (Rev 1 and Rev 2 share
-- the number). Replace it with a composite unique on (number, revision).
DO $$
DECLARE
    conname_var TEXT;
BEGIN
    SELECT conname INTO conname_var
    FROM pg_constraint
    WHERE conrelid = 'quotes'::regclass
      AND contype = 'u'
      AND pg_get_constraintdef(oid) LIKE '%quote_number%'
      AND pg_get_constraintdef(oid) NOT LIKE '%revision_number%'
    LIMIT 1;

    IF conname_var IS NOT NULL THEN
        EXECUTE format('ALTER TABLE quotes DROP CONSTRAINT %I', conname_var);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'quotes_number_revision_unique'
    ) THEN
        ALTER TABLE quotes
            ADD CONSTRAINT quotes_number_revision_unique
            UNIQUE (quote_number, revision_number);
    END IF;
END$$;

CREATE INDEX IF NOT EXISTS idx_quotes_current ON quotes (is_current);
CREATE INDEX IF NOT EXISTS idx_quotes_number ON quotes (quote_number);

-- Line items need to remember when they were FIRST quoted, so a revision
-- can show carried-over items alongside the date they were originally
-- quoted rather than looking like they were all added today.
ALTER TABLE quote_line_items
    ADD COLUMN IF NOT EXISTS first_quoted_at TIMESTAMP;

ALTER TABLE quote_line_items
    ADD COLUMN IF NOT EXISTS first_quoted_revision INTEGER NOT NULL DEFAULT 1;

-- Backfill: existing line items were first quoted when their quote was created.
UPDATE quote_line_items li
SET first_quoted_at = q.created_at
FROM quotes q
WHERE q.quote_id = li.quote_id
  AND li.first_quoted_at IS NULL;

-- ============================================================
-- 3. Quote activity log (who did what, when)
-- ============================================================

CREATE TABLE IF NOT EXISTS quote_activity (
    id             SERIAL PRIMARY KEY,
    quote_id       INTEGER REFERENCES quotes(quote_id) ON DELETE CASCADE,
    action         VARCHAR(50) NOT NULL,
        -- created | revised | line_item_added | line_item_removed |
        -- tax_applied | pdf_generated | sent | status_changed
    detail         VARCHAR(500),
    performed_by   VARCHAR(100) NOT NULL,
    performed_at   TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_quote_activity_quote
    ON quote_activity (quote_id, performed_at);

-- ============================================================
-- 4. CCR intake queue (replaces the emailed scratch sheet)
-- ============================================================

CREATE TABLE IF NOT EXISTS intake_requests (
    id                  SERIAL PRIMARY KEY,
    service_order_no    VARCHAR(20) REFERENCES service_orders(service_order_no),
    issue_description   TEXT,        -- what the customer reported / what broke
    work_performed      TEXT,        -- what the tech actually did on site
    parts_requested     TEXT,        -- free-text parts list off the scratch sheet
    submitted_by        VARCHAR(100) NOT NULL,
    submitted_at        TIMESTAMP NOT NULL DEFAULT now(),
    status              VARCHAR(20) NOT NULL DEFAULT 'pending',
        -- pending (waiting on the quote team) | quoted | closed
    quote_id            INTEGER REFERENCES quotes(quote_id),
    notes               VARCHAR(500)
);

CREATE INDEX IF NOT EXISTS idx_intake_status ON intake_requests (status, submitted_at);
