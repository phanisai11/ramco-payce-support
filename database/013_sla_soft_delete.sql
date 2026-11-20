-- Uniform soft delete for SLA rules: rows are never physically removed;
-- deleted_at marks them and every read path filters them out.
ALTER TABLE app.sla_rule
  ADD COLUMN IF NOT EXISTS deleted_at timestamptz;

CREATE INDEX IF NOT EXISTS sla_rule_live_idx
  ON app.sla_rule (tenant_id, region)
  WHERE deleted_at IS NULL;
