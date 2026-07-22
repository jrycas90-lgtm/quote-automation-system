-- 01_schema.sql
--
-- Replaces the "Master Price List" spreadsheet + "Quote Template" workbook
-- with a real relational schema. Key upgrades over the spreadsheet version:
--
--   1. account_pricing has effective_date/expired_date, so pricing history
--      is queryable instead of being overwritten in place (no more "what
--      did we quote them last time" archaeology through old emails).
--   2. service_orders is the sync target for data pulled from the ERP
--      (the original workflow's ERP) -- the "500 number" auto-populates
--      instead of being hand-typed into a scratch sheet.
--   3. quotes + quote_line_items + quote_status_history give a full,
--      queryable audit trail: who quoted what, when, at what price, and
--      what happened to it (sent / viewed / accepted / expired).

DROP TABLE IF EXISTS quote_status_history CASCADE;
DROP TABLE IF EXISTS quote_line_items CASCADE;
DROP TABLE IF EXISTS quotes CASCADE;
DROP TABLE IF EXISTS service_orders CASCADE;
DROP TABLE IF EXISTS account_pricing CASCADE;
DROP TABLE IF EXISTS parts CASCADE;
DROP TABLE IF EXISTS accounts CASCADE;

-- ============================================================
-- Reference data
-- ============================================================

CREATE TABLE accounts (
    account_id      SERIAL PRIMARY KEY,
    account_number  VARCHAR(20)  NOT NULL UNIQUE,   -- what the ERP calls it
    account_name    VARCHAR(150) NOT NULL,
    contact_name    VARCHAR(100),
    contact_email   VARCHAR(150),
    billing_city    VARCHAR(100),
    billing_state   VARCHAR(2),
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE
);

CREATE TABLE parts (
    part_number     VARCHAR(30) PRIMARY KEY,
    description     VARCHAR(200) NOT NULL,
    category        VARCHAR(50),
    list_price      NUMERIC(10, 2) NOT NULL   -- fallback price if an account has no override
);

-- ============================================================
-- Pricing -- this table IS the Master Price List, normalized.
-- One row per (account, part, time period) instead of one giant
-- cross-tab sheet with a column per account.
-- ============================================================

CREATE TABLE account_pricing (
    id              SERIAL PRIMARY KEY,
    account_id      INTEGER REFERENCES accounts(account_id),
    part_number     VARCHAR(30) REFERENCES parts(part_number),
    price           NUMERIC(10, 2) NOT NULL,
    effective_date  DATE NOT NULL,
    expired_date    DATE,                      -- NULL = still in effect
    UNIQUE (account_id, part_number, effective_date)
);

CREATE INDEX idx_pricing_account_part ON account_pricing (account_id, part_number);

-- ============================================================
-- Service orders -- the sync target for ERP data (the original
-- workflow's ERP system). In production this table is populated by a
-- scheduled job reading from the ERP, not typed in by hand.
-- ============================================================

CREATE TABLE service_orders (
    service_order_no   VARCHAR(20) PRIMARY KEY,   -- the "500 number"
    account_id          INTEGER REFERENCES accounts(account_id),
    order_date          DATE NOT NULL,
    site_address         VARCHAR(200),
    description          VARCHAR(300),
    erp_status           VARCHAR(30),               -- status as of last sync
    synced_at            TIMESTAMP NOT NULL DEFAULT now()
);

-- ============================================================
-- Quotes -- replaces the "Quotation" tab + manual PDF export.
-- ============================================================

CREATE TABLE quotes (
    quote_id            SERIAL PRIMARY KEY,
    quote_number         VARCHAR(20) NOT NULL UNIQUE,
    service_order_no     VARCHAR(20) REFERENCES service_orders(service_order_no),
    account_id           INTEGER REFERENCES accounts(account_id),
    created_by            VARCHAR(100) NOT NULL,
    created_at            TIMESTAMP NOT NULL DEFAULT now(),
    status                VARCHAR(20) NOT NULL DEFAULT 'draft',
        -- draft -> sent -> accepted / declined / expired
    sent_at               TIMESTAMP,
    expires_at             DATE,
    pdf_path                VARCHAR(300)
);

CREATE TABLE quote_line_items (
    id                SERIAL PRIMARY KEY,
    quote_id           INTEGER REFERENCES quotes(quote_id) ON DELETE CASCADE,
    part_number         VARCHAR(30) REFERENCES parts(part_number),
    description          VARCHAR(200),
    quantity              INTEGER NOT NULL,
    unit_price             NUMERIC(10, 2) NOT NULL,
    line_total              NUMERIC(10, 2) GENERATED ALWAYS AS (quantity * unit_price) STORED
);

CREATE TABLE quote_status_history (
    id             SERIAL PRIMARY KEY,
    quote_id        INTEGER REFERENCES quotes(quote_id) ON DELETE CASCADE,
    status           VARCHAR(20) NOT NULL,
    changed_at        TIMESTAMP NOT NULL DEFAULT now(),
    note               VARCHAR(300)
);

CREATE INDEX idx_quotes_account   ON quotes (account_id);
CREATE INDEX idx_quotes_status    ON quotes (status);
CREATE INDEX idx_quotes_created   ON quotes (created_at);

-- Convenience view: current quote totals in one place, since summing
-- line items is needed by almost every quote-facing query.
CREATE OR REPLACE VIEW quote_totals AS
SELECT
    q.quote_id,
    q.quote_number,
    q.account_id,
    a.account_name,
    q.status,
    q.created_at,
    q.sent_at,
    q.expires_at,
    COALESCE(SUM(li.line_total), 0) AS quote_total
FROM quotes q
JOIN accounts a ON a.account_id = q.account_id
LEFT JOIN quote_line_items li ON li.quote_id = q.quote_id
GROUP BY q.quote_id, q.quote_number, q.account_id, a.account_name,
         q.status, q.created_at, q.sent_at, q.expires_at;
