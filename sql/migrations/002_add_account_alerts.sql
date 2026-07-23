-- 002_add_account_alerts.sql
--
-- Adds a per-account alerts/instructions table. Safe to run against an
-- already-populated database -- only adds, never drops.
--
-- Run this once against each database you're using:
--   psql -h <host> -p <port> -U postgres -d <dbname> -f sql/migrations/002_add_account_alerts.sql

CREATE TABLE IF NOT EXISTS account_alerts (
    id          SERIAL PRIMARY KEY,
    account_id  INTEGER REFERENCES accounts(account_id) ON DELETE CASCADE,
    message     VARCHAR(300) NOT NULL,
    created_at  TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_account_alerts_account ON account_alerts (account_id);

-- A couple of realistic demo alerts on existing synthetic accounts, so
-- the feature has something to show immediately. Safe no-ops if these
-- account names don't exist in your data.
INSERT INTO account_alerts (account_id, message)
SELECT account_id, 'Onsite work pre-approved up to $2,000 without additional sign-off.'
FROM accounts WHERE account_name = 'Lakeshore Medical Campus'
  AND NOT EXISTS (
      SELECT 1 FROM account_alerts aa
      JOIN accounts a ON a.account_id = aa.account_id
      WHERE a.account_name = 'Lakeshore Medical Campus'
        AND aa.message = 'Onsite work pre-approved up to $2,000 without additional sign-off.'
  );

INSERT INTO account_alerts (account_id, message)
SELECT account_id, 'Submit completed quotes directly to the site contact, not the general inbox.'
FROM accounts WHERE account_name = 'Lakeshore Medical Campus'
  AND NOT EXISTS (
      SELECT 1 FROM account_alerts aa
      JOIN accounts a ON a.account_id = aa.account_id
      WHERE a.account_name = 'Lakeshore Medical Campus'
        AND aa.message = 'Submit completed quotes directly to the site contact, not the general inbox.'
  );

INSERT INTO account_alerts (account_id, message)
SELECT account_id, 'No Hardware or Fuel charges for this account -- covered under service contract.'
FROM accounts WHERE account_name = 'Pinehurst School District'
  AND NOT EXISTS (
      SELECT 1 FROM account_alerts aa
      JOIN accounts a ON a.account_id = aa.account_id
      WHERE a.account_name = 'Pinehurst School District'
        AND aa.message = 'No Hardware or Fuel charges for this account -- covered under service contract.'
  );
