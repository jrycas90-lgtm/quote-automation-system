-- 004_technicians.sql
--
-- Adds a technician roster so we can record WHO did the work, for
-- internal record keeping.
--
-- Design notes:
--
--   * Technicians are a ROSTER, not user accounts. Techs never log into
--     this system -- they talk to the CCR, who relays what was found and
--     what's needed. Giving them accounts would create credentials and
--     attack surface for people who will never use them. This table has
--     no password, no role, no login of any kind.
--
--   * The technician is assigned at the SERVICE ORDER level (that's what
--     dispatch actually does) and captured on the QUOTE as well, so the
--     record survives even if the service order is later reassigned.
--
--   * Technician identity is INTERNAL ONLY. It is deliberately never
--     rendered on the customer-facing PDF -- see src/pdf_generator.py,
--     which selects columns explicitly and does not include it.
--
-- Safe to run against a populated database. Apply with:
--   python scripts/migrate.py

CREATE TABLE IF NOT EXISTS technicians (
    technician_id   SERIAL PRIMARY KEY,
    full_name       VARCHAR(120) NOT NULL,
    employee_code   VARCHAR(20) UNIQUE,
    region          VARCHAR(60),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_technicians_active ON technicians (is_active);

ALTER TABLE service_orders
    ADD COLUMN IF NOT EXISTS technician_id INTEGER REFERENCES technicians(technician_id);

ALTER TABLE quotes
    ADD COLUMN IF NOT EXISTS technician_id INTEGER REFERENCES technicians(technician_id);

CREATE INDEX IF NOT EXISTS idx_quotes_technician ON quotes (technician_id);

-- Intake captures which tech went out, since the CCR is talking to them
-- when the request is raised.
ALTER TABLE intake_requests
    ADD COLUMN IF NOT EXISTS technician_id INTEGER REFERENCES technicians(technician_id);

-- Seed roster. Entirely fictional names, consistent with the rest of the
-- synthetic data in this project -- no real technician appears anywhere.
INSERT INTO technicians (full_name, employee_code, region) VALUES
    ('Ray Delgado',      'TECH-1041', 'Denver'),
    ('Marcus Boone',     'TECH-1052', 'Denver'),
    ('Priya Raman',      'TECH-1063', 'Madison'),
    ('Danny Kowalczyk',  'TECH-1074', 'Madison'),
    ('Alicia Fontaine',  'TECH-1085', 'Charleston'),
    ('Theo Nakamura',    'TECH-1096', 'Kansas City'),
    ('Wendell Pruitt',   'TECH-1107', 'Memphis'),
    ('Sofia Aguilar',    'TECH-1118', 'Columbus'),
    ('Grant Whitaker',   'TECH-1129', 'Boise'),
    ('Nadia Haddad',     'TECH-1130', 'Salt Lake City')
ON CONFLICT (employee_code) DO NOTHING;

-- Give existing service orders a plausible assigned tech so the feature
-- has data to show immediately. Matches on region where possible, and
-- falls back to any active tech otherwise.
UPDATE service_orders so
SET technician_id = t.technician_id
FROM accounts a, technicians t
WHERE so.account_id = a.account_id
  AND so.technician_id IS NULL
  AND t.region = a.billing_city;

UPDATE service_orders
SET technician_id = (SELECT technician_id FROM technicians WHERE is_active ORDER BY technician_id LIMIT 1)
WHERE technician_id IS NULL;

-- Backfill quotes from their service order's assigned tech.
UPDATE quotes q
SET technician_id = so.technician_id
FROM service_orders so
WHERE q.service_order_no = so.service_order_no
  AND q.technician_id IS NULL;
