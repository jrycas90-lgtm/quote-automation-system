-- 005_contractors_second_tech.sql
--
-- Three additions:
--
-- 1. SECOND TECHNICIAN. Plenty of repairs are two-person jobs, and the
--    additional tech is billable. Recorded per quote alongside the
--    primary tech (both internal-only, same as the primary).
--
-- 2. GENERAL CONTRACTORS. Sometimes the work is subcontracted to a GC
--    (e.g. a repair out of our own service area). The GC charges US one
--    price; we charge the CUSTOMER another. The customer must never see
--    the GC's pricing, and must not even know a GC was involved.
--
--    Modelled as a cost column on each line item rather than a second,
--    separate quote. Two disconnected quotes would drift apart the
--    moment someone edited one and not the other -- and a GC quote that
--    silently disagrees with the customer quote about WHAT work is being
--    done is worse than useless. One set of line items, two price
--    columns, two renderings.
--
--    unit_price      = what the customer pays  (customer-facing PDF)
--    contractor_cost = what the GC charges us  (internal + GC PDF only)
--
-- 3. QUOTE NUMBER PREFIX. Quote numbers become account-derived
--    (WAL-2026-07-23-01). Existing quote numbers are left completely
--    alone -- renumbering historical quotes would break every reference
--    anyone has to them.
--
-- Safe to run against a populated database. Apply with:
--   python scripts/migrate.py

-- ============================================================
-- 1. Second technician
-- ============================================================

ALTER TABLE quotes
    ADD COLUMN IF NOT EXISTS secondary_technician_id INTEGER REFERENCES technicians(technician_id);

ALTER TABLE quotes
    ADD COLUMN IF NOT EXISTS tech_count INTEGER NOT NULL DEFAULT 1;

-- ============================================================
-- 2. General contractors
-- ============================================================

CREATE TABLE IF NOT EXISTS contractors (
    contractor_id   SERIAL PRIMARY KEY,
    company_name    VARCHAR(150) NOT NULL,
    contact_name    VARCHAR(120),
    contact_email   VARCHAR(150),
    phone           VARCHAR(40),
    region          VARCHAR(80),
    notes           VARCHAR(300),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_contractors_active ON contractors (is_active);

ALTER TABLE quotes
    ADD COLUMN IF NOT EXISTS contractor_id INTEGER REFERENCES contractors(contractor_id);

CREATE INDEX IF NOT EXISTS idx_quotes_contractor ON quotes (contractor_id);

-- What the GC charges us for this line. NULL means no contractor cost
-- captured (either no GC on the job, or not yet entered).
ALTER TABLE quote_line_items
    ADD COLUMN IF NOT EXISTS contractor_cost NUMERIC(10, 2);

-- Seed roster. Fictional, consistent with the rest of the synthetic data.
INSERT INTO contractors (company_name, contact_name, contact_email, phone, region) VALUES
    ('George''s Hardware & Door',   'George Alvarez',  'george@georgeshardware.example',  '(406) 555-0142', 'Montana'),
    ('Summit Door Services',        'Rhonda Piatt',    'rpiatt@summitdoorsvc.example',    '(208) 555-0177', 'Idaho'),
    ('Gulf Coast Access Partners',  'Andre Boudreaux', 'andre@gulfcoastaccess.example',   '(504) 555-0163', 'Louisiana'),
    ('Northline Contracting',       'Kim Vasquez',     'kvasquez@northlinegc.example',    '(701) 555-0119', 'North Dakota')
ON CONFLICT DO NOTHING;

-- ============================================================
-- 3. Quote number prefix support
-- ============================================================

-- A short account-derived prefix used when generating new quote numbers
-- (e.g. "WAL" for Walmart -> WAL-2026-07-23-01). Backfilled from the
-- account name; editable per account afterwards for cases where the
-- first three letters collide or read badly.
ALTER TABLE accounts
    ADD COLUMN IF NOT EXISTS quote_prefix VARCHAR(6);

UPDATE accounts
SET quote_prefix = UPPER(REGEXP_REPLACE(SUBSTRING(REGEXP_REPLACE(account_name, '[^A-Za-z]', '', 'g') FROM 1 FOR 3), '[^A-Za-z]', '', 'g'))
WHERE quote_prefix IS NULL;
