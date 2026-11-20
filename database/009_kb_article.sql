-- KB articles persisted in Postgres (source of truth for RAG).
-- Seeded from data-room/data/kb_articles.json. Region ALL fans out to
-- per-region retrieval chunks in LocalKbStore; tenant scoping stays
-- topic-hint based (no tenant_id column by design).
BEGIN;

CREATE TABLE app.kb_article (
  article_id    text PRIMARY KEY,
  title         text NOT NULL,
  topic         text NOT NULL DEFAULT 'general',
  region        text NOT NULL DEFAULT 'ALL' CHECK (region IN ('ALL','IN','UAE')),
  excerpt       text NOT NULL DEFAULT '',
  valid_from    date,
  valid_to      date,
  years         int[],
  status        text NOT NULL DEFAULT 'Published'
                CHECK (status IN ('Published','Draft','Archived')),
  owner         text,
  last_reviewed date,
  helpful_pct   int,
  views_90d     int,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

INSERT INTO app.kb_article
  (article_id, title, topic, region, excerpt, valid_from, valid_to, years,
   status, owner, last_reviewed, helpful_pct, views_90d)
VALUES
  ('KB-0101', 'How to run monthly payroll', 'howto_payroll', 'ALL', 'See full article in Confluence space SUP-KB.', NULL, NULL, NULL, 'Published', 'Content Team', '2026-03-10', 81, 4120),
  ('KB-0102', 'Adding a new employee (single and bulk upload)', 'howto_onboard', 'ALL', 'See full article in Confluence space SUP-KB.', NULL, NULL, NULL, 'Published', 'Content Team', '2025-11-18', 77, 3380),
  ('KB-0103', 'Resetting passwords and SSO sign-in problems', 'access', 'ALL', 'See full article in Confluence space SUP-KB.', NULL, NULL, NULL, 'Published', 'Content Team', '2026-01-22', 74, 2950),
  ('KB-0104', 'Configuring leave policies and carry-forward', 'leave', 'ALL', 'See full article in Confluence space SUP-KB.', NULL, NULL, NULL, 'Published', 'Content Team', '2025-09-05', 69, 2210),
  ('KB-0105', 'Downloading payslips in bulk', 'payslip', 'ALL', 'See full article in Confluence space SUP-KB.', NULL, NULL, NULL, 'Published', 'Content Team', '2026-02-09', 79, 3010),
  ('KB-0110', 'Attendance integration setup (biometric and HRIS connectors)', 'integration', 'ALL', 'See full article in Confluence space SUP-KB.', NULL, NULL, NULL, 'Published', 'Integrations', '2025-06-30', 58, 880),
  ('KB-0120', 'Invoices, GST and billing cycles', 'billing', 'ALL', 'See full article in Confluence space SUP-KB.', NULL, NULL, NULL, 'Published', 'Finance Ops', '2025-12-04', 72, 640),
  ('KB-0130', 'Custom report builder', 'reports', 'ALL', 'See full article in Confluence space SUP-KB.', NULL, NULL, NULL, 'Published', 'Content Team', '2024-09-12', 64, 1560),
  ('KB-0135', 'Bank file formats: NEFT, RTGS and bulk transfer', 'bank_file', 'IN', 'See full article in Confluence space SUP-KB.', NULL, NULL, NULL, 'Published', 'Payments', '2023-11-21', 70, 1210),
  ('KB-0140', 'ESI: contribution rates and wage limit', 'esi', 'IN', 'See full article in Confluence space SUP-KB.', NULL, NULL, NULL, 'Published', 'Compliance India', '2023-08-17', 73, 1340),
  ('KB-0142', 'PF: wage ceiling and contribution calculation', 'pf_wage_ceiling', 'IN', 'The EPF statutory wage ceiling is INR 15,000 per month. PF is computed at 12% of basic+DA up to the ceiling. Employees drawing more than INR 15,000 may contribute on actual wage only through a joint option.', NULL, NULL, NULL, 'Published', 'Compliance India', '2024-02-14', 71, 5230),
  ('KB-0143', 'PF: employees above the wage ceiling (voluntary and restricted contribution)', 'pf_wage_ceiling', 'IN', 'For employees above INR 15,000 wage, employer contribution is restricted to the ceiling unless the joint option is exercised.', NULL, NULL, NULL, 'Published', 'Compliance India', '2024-02-14', 66, 2870),
  ('KB-0144', 'PF: generating and uploading the ECR file', 'pf_ecr', 'IN', 'See full article in Confluence space SUP-KB.', NULL, NULL, NULL, 'Published', 'Compliance India', '2025-07-08', 75, 1790),
  ('KB-0150', 'TDS: new regime slabs FY2026-27 and Form 16', 'tds', 'IN', 'See full article in Confluence space SUP-KB.', NULL, NULL, NULL, 'Published', 'Compliance India', '2026-04-08', 78, 2480),
  ('KB-0155', 'Professional Tax: state-wise slabs and due dates', 'pt', 'IN', 'See full article in Confluence space SUP-KB.', NULL, NULL, NULL, 'Published', 'Compliance India', '2025-10-02', 68, 960),
  ('KB-0160', 'UAE end-of-service gratuity calculation', 'gratuity', 'UAE', 'See full article in Confluence space SUP-KB.', NULL, NULL, NULL, 'Published', 'Compliance UAE', '2026-02-02', 76, 1120),
  ('KB-0201', 'WPS: generating the SIF file and uploading to the bank portal', 'wps_file_format', 'UAE', 'Generate the SIF using the v2 layout (14 columns). The establishment reference is optional. Upload via the bank portal.', NULL, NULL, NULL, 'Published', 'Compliance UAE', '2024-06-20', 67, 3140),
  ('KB-0202', 'WPS: rejection codes and how to fix them', 'wps_file_format', 'UAE', 'Rejection E104: wrong column count. Ensure the SIF has exactly 14 columns.', NULL, NULL, NULL, 'Published', 'Compliance UAE', '2024-06-20', 62, 2060),
  ('KB-0203', 'WPS: bank routing code and employee card setup', 'wps_setup', 'UAE', 'See full article in Confluence space SUP-KB.', NULL, NULL, NULL, 'Published', 'Compliance UAE', '2025-03-14', 71, 780),
  ('KB-0210', 'Exporting statutory registers (Form 5, 6, 7)', 'registers', 'IN', 'See full article in Confluence space SUP-KB.', NULL, NULL, NULL, 'Published', 'Compliance India', '2024-11-03', 60, 410),
  ('KB-0220', 'Full and final settlement process', 'fnf', 'ALL', 'See full article in Confluence space SUP-KB.', NULL, NULL, NULL, 'Published', 'Content Team', '2025-04-19', 65, 1010),
  ('KB-0230', 'Arrears and retro-pay adjustments', 'arrears', 'ALL', 'See full article in Confluence space SUP-KB.', NULL, NULL, NULL, 'Published', 'Content Team', '2024-12-10', 63, 970),
  ('KB-0240', 'Data export and employee data deletion requests', 'data_requests', 'ALL', 'See full article in Confluence space SUP-KB.', NULL, NULL, NULL, 'Published', 'Security', '2026-05-21', 80, 190),
  ('KB-0250', 'Mobile app: employee self-service troubleshooting', 'ess_app', 'ALL', 'See full article in Confluence space SUP-KB.', NULL, NULL, NULL, 'Published', 'Content Team', '2025-08-27', 70, 1850),
  ('KB-0301', 'Gratuity accrual changes FY2026', 'gratuity_accrual', 'IN', 'From FY2026 gratuity accrues on Basic+DA with revised ceiling. Covers calculation worksheet and payroll config steps.', NULL, NULL, ARRAY[2026]::int[], 'Published', 'Statutory Desk', NULL, NULL, NULL),
  ('KB-0302', 'Draft: leave encashment policy', 'leave_policy', 'ALL', 'Draft policy under review.', NULL, NULL, NULL, 'Draft', NULL, NULL, NULL, NULL);

COMMIT;
