-- 001_add_tax_support.sql
--
-- Adds tax exemption tracking (account-level) and a configurable state
-- sales tax rate table. Written to be SAFE TO RUN AGAINST AN
-- ALREADY-POPULATED DATABASE (local Docker or Supabase) -- it only adds,
-- never drops, so none of your existing accounts/quotes/history are
-- affected.
--
-- Run this once against each database you're using:
--   psql -h <host> -p <port> -U postgres -d <dbname> -f sql/migrations/001_add_tax_support.sql
--
-- Design notes:
--   - Tax EXEMPTION is tracked per account (accounts.tax_exempt), since a
--     customer's exemption certificate applies to them as a business
--     entity, not to a specific address.
--   - Tax RATE is looked up by the state of the specific service
--     location on a quote (parsed from site_address), since one account
--     can have locations in multiple states with different rates -- e.g.
--     the same customer might have a site in WI and another in NJ.
--   - Rates below are BASE STATE rates only -- they do NOT include
--     county/city/local district add-ons, which vary too granularly to
--     model in a simple system like this. Treat these as a reasonable
--     2026 starting point, not a guarantee of accuracy -- verify against
--     your state's Department of Revenue and adjust via Settings > Tax
--     Rates in the app before relying on these for real invoicing.

ALTER TABLE accounts ADD COLUMN IF NOT EXISTS tax_exempt BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS state_tax_rates (
    state_code  CHAR(2) PRIMARY KEY,
    state_name  VARCHAR(50) NOT NULL,
    rate        NUMERIC(6, 4) NOT NULL DEFAULT 0  -- decimal, e.g. 0.0725 = 7.25%
);

INSERT INTO state_tax_rates (state_code, state_name, rate) VALUES
('AL', 'Alabama', 0.0400),
('AK', 'Alaska', 0.0000),
('AZ', 'Arizona', 0.0560),
('AR', 'Arkansas', 0.0650),
('CA', 'California', 0.0725),
('CO', 'Colorado', 0.0290),
('CT', 'Connecticut', 0.0635),
('DE', 'Delaware', 0.0000),
('FL', 'Florida', 0.0600),
('GA', 'Georgia', 0.0400),
('HI', 'Hawaii', 0.0400),
('ID', 'Idaho', 0.0600),
('IL', 'Illinois', 0.0625),
('IN', 'Indiana', 0.0700),
('IA', 'Iowa', 0.0600),
('KS', 'Kansas', 0.0650),
('KY', 'Kentucky', 0.0600),
('LA', 'Louisiana', 0.0445),
('ME', 'Maine', 0.0550),
('MD', 'Maryland', 0.0600),
('MA', 'Massachusetts', 0.0625),
('MI', 'Michigan', 0.0600),
('MN', 'Minnesota', 0.0688),
('MS', 'Mississippi', 0.0700),
('MO', 'Missouri', 0.0423),
('MT', 'Montana', 0.0000),
('NE', 'Nebraska', 0.0550),
('NV', 'Nevada', 0.0685),
('NH', 'New Hampshire', 0.0000),
('NJ', 'New Jersey', 0.0663),
('NM', 'New Mexico', 0.0513),
('NY', 'New York', 0.0400),
('NC', 'North Carolina', 0.0475),
('ND', 'North Dakota', 0.0500),
('OH', 'Ohio', 0.0575),
('OK', 'Oklahoma', 0.0450),
('OR', 'Oregon', 0.0000),
('PA', 'Pennsylvania', 0.0600),
('RI', 'Rhode Island', 0.0700),
('SC', 'South Carolina', 0.0600),
('SD', 'South Dakota', 0.0420),
('TN', 'Tennessee', 0.0700),
('TX', 'Texas', 0.0625),
('UT', 'Utah', 0.0610),
('VT', 'Vermont', 0.0600),
('VA', 'Virginia', 0.0530),
('WA', 'Washington', 0.0650),
('WV', 'West Virginia', 0.0600),
('WI', 'Wisconsin', 0.0500),
('WY', 'Wyoming', 0.0400),
('DC', 'District of Columbia', 0.0600)
ON CONFLICT (state_code) DO NOTHING;

-- Nice-to-have demo touch: mark one existing synthetic account as
-- tax-exempt (a public school district is a realistic real-world
-- example) so the feature has something to show immediately. Safe no-op
-- if that account doesn't exist in your data.
UPDATE accounts SET tax_exempt = TRUE WHERE account_name = 'Pinehurst School District';
