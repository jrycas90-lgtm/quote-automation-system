-- 006_quote_templates.sql
--
-- Reusable quote templates for recurring work.
--
-- Modernization kits are the driving case: the same handful of items
-- every time, 8 hours of labor included, sold at a flat rate. Rebuilding
-- that line by line on every quote is wasted effort and an easy place to
-- forget a component.
--
-- Two pricing modes per template line, because both are real:
--
--   fixed_price IS NULL  -> price is looked up per account at quote time,
--                           exactly like adding the part by hand. Use for
--                           ordinary parts where each account has its own
--                           negotiated rate.
--
--   fixed_price IS SET   -> that price is used as-is, regardless of
--                           account. Use for flat-rate packages like a mod
--                           kit, where the whole point is that the price
--                           doesn't vary.
--
-- Templates are managed by supervisors/admins in Settings; quote staff
-- apply them but don't edit them. Applying a template only POPULATES a
-- draft -- nothing is saved until the user reviews it and generates the
-- quote as normal.
--
-- Safe to run against a populated database. Apply with:
--   python scripts/migrate.py

CREATE TABLE IF NOT EXISTS quote_templates (
    template_id   SERIAL PRIMARY KEY,
    name          VARCHAR(100) NOT NULL UNIQUE,
    description   VARCHAR(300),
    created_by    VARCHAR(100),
    created_at    TIMESTAMP NOT NULL DEFAULT now(),
    is_active     BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_quote_templates_active ON quote_templates (is_active);

CREATE TABLE IF NOT EXISTS quote_template_items (
    id            SERIAL PRIMARY KEY,
    template_id   INTEGER REFERENCES quote_templates(template_id) ON DELETE CASCADE,
    part_number   VARCHAR(30) REFERENCES parts(part_number),   -- NULL for custom/flat lines
    description   VARCHAR(200) NOT NULL,
    quantity      INTEGER NOT NULL DEFAULT 1,
    fixed_price   NUMERIC(10, 2),                              -- NULL = look up account pricing
    sort_order    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_template_items_template
    ON quote_template_items (template_id, sort_order);

-- Seed two realistic modernization kits. Flat-rate lines, since the whole
-- point of a mod kit is that it's sold at a set price with labor included.
INSERT INTO quote_templates (name, description, created_by)
VALUES
    ('Mod Kit - Standard',
     'Standard modernization kit. Flat rate, includes 8 hours labor.',
     'system'),
    ('Mod Kit - ADA',
     'ADA modernization kit with compliant operator. Flat rate, includes 8 hours labor.',
     'system')
ON CONFLICT (name) DO NOTHING;

INSERT INTO quote_template_items (template_id, part_number, description, quantity, fixed_price, sort_order)
SELECT t.template_id, NULL,
       'Modernization Kit - Standard (includes 8 hrs labor)', 1, 2560.00, 1
FROM quote_templates t
WHERE t.name = 'Mod Kit - Standard'
  AND NOT EXISTS (
      SELECT 1 FROM quote_template_items i WHERE i.template_id = t.template_id
  );

INSERT INTO quote_template_items (template_id, part_number, description, quantity, fixed_price, sort_order)
SELECT t.template_id, NULL,
       'Modernization Kit - ADA (includes 8 hrs labor)', 1, 2750.00, 1
FROM quote_templates t
WHERE t.name = 'Mod Kit - ADA'
  AND NOT EXISTS (
      SELECT 1 FROM quote_template_items i WHERE i.template_id = t.template_id
  );

-- Trip charge is billed on top of the flat kit price, and is a normal
-- editable line rather than part of the package.
INSERT INTO quote_template_items (template_id, part_number, description, quantity, fixed_price, sort_order)
SELECT t.template_id, NULL, 'Trip Charge', 1, 75.00, 2
FROM quote_templates t
WHERE t.name IN ('Mod Kit - Standard', 'Mod Kit - ADA')
  AND NOT EXISTS (
      SELECT 1 FROM quote_template_items i
      WHERE i.template_id = t.template_id AND i.description = 'Trip Charge'
  );
