-- SLA / penalty rules persisted in Postgres (source of truth).
-- tenant_id is a real FK to app.tenant_profile; NULL = region-generic rule.
-- Mirrors the former routing_policy/sla.yaml seed data.
BEGIN;

CREATE TABLE app.sla_rule (
  rule_id             text PRIMARY KEY,
  tenant_id           text REFERENCES app.tenant_profile (tenant_id),
  region              text CHECK (region IN ('IN', 'UAE')),
  unanswered_hours    numeric(10, 2) NOT NULL CHECK (unanswered_hours > 0),
  penalty_amount      numeric(12, 2) NOT NULL CHECK (penalty_amount >= 0),
  currency            text NOT NULL,
  valid_from          date,
  valid_to            date,
  status              text NOT NULL DEFAULT 'approved'
                      CHECK (status IN ('approved','in_progress','outdated','draft','expired')),
  applies_to_statuses text[] NOT NULL DEFAULT '{open,pending_customer}',
  description         text,
  policy_version      text NOT NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  CHECK (tenant_id IS NOT NULL OR region IS NOT NULL)
);

ALTER TABLE app.sla_rule ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.sla_rule FORCE ROW LEVEL SECURITY;

-- Tenants see their own rules plus region-generic (tenant_id IS NULL) rules.
CREATE POLICY sla_rule_isolation ON app.sla_rule
  USING (tenant_id IS NULL OR tenant_id = app.current_tenant_id())
  WITH CHECK (tenant_id IS NULL OR tenant_id = app.current_tenant_id());

INSERT INTO app.sla_rule
  (rule_id, tenant_id, region, unanswered_hours, penalty_amount, currency,
   valid_from, valid_to, status, applies_to_statuses, description, policy_version)
VALUES
  -- C-1001 (IN)
  ('sla-c1001-unanswered-4h',  'C-1001', NULL,   4,  200.00, 'INR',
   '2026-01-01', NULL,         'approved',    '{open,pending_customer}',
   'C-1001 first-tier penalty over 4h unanswered', 'sla-g3-synthetic-v3'),
  ('sla-c1001-unanswered-24h', 'C-1001', NULL,  24,  800.00, 'INR',
   '2026-01-01', NULL,         'approved',    '{open,pending_customer}',
   'C-1001 escalated penalty over 24h unanswered', 'sla-g3-synthetic-v3'),
  -- C-1002 (IN)
  ('sla-c1002-unanswered-6h',  'C-1002', NULL,   6,  300.00, 'INR',
   '2026-01-01', NULL,         'approved',    '{open,pending_customer}',
   'C-1002 first-tier penalty over 6h unanswered', 'sla-g3-synthetic-v3'),
  ('sla-c1002-unanswered-36h', 'C-1002', NULL,  36, 1200.00, 'INR',
   '2026-01-01', NULL,         'in_progress', '{open,pending_customer}',
   'C-1002 escalated penalty over 36h unanswered', 'sla-g3-synthetic-v3'),
  -- Expired legacy rule (audit only; never applies)
  ('sla-c1001-unanswered-2h-legacy', 'C-1001', NULL, 2, 50.00, 'INR',
   '2025-01-01', '2025-12-31', 'approved',    '{open,pending_customer}',
   'Legacy C-1001 2h tier — expired end of 2025', 'sla-g3-synthetic-v3'),
  -- Generic region fallbacks (tenant_id NULL)
  ('sla-in-unanswered-4h',  NULL, 'IN',  4,  250.00, 'INR',
   '2026-01-01', NULL,      'approved', '{open,pending_customer}',
   'Generic IN first-tier penalty', 'sla-g3-synthetic-v3'),
  ('sla-in-unanswered-24h', NULL, 'IN', 24, 1000.00, 'INR',
   '2026-01-01', NULL,      'approved', '{open,pending_customer}',
   'Generic IN escalated penalty', 'sla-g3-synthetic-v3'),
  ('sla-uae-unanswered-8h', NULL, 'UAE', 8,  150.00, 'AED',
   '2026-01-01', '2026-12-31', 'approved', '{open,pending_customer}',
   'UAE unanswered-ticket penalty tier', 'sla-g3-synthetic-v3');

COMMIT;
