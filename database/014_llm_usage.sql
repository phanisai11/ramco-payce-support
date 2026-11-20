-- Per-call LLM usage ledger: every recorded analysis that carries token
-- counts appends one row here, inside the same transaction as the analysis
-- — no async gap, no lost usage. Append-only; cost_usd is estimated at
-- write time from the model's rate card (llm_pricing.py).
CREATE TABLE IF NOT EXISTS app.llm_usage (
  usage_id       uuid PRIMARY KEY,
  tenant_id      text NOT NULL REFERENCES app.tenant_profile (tenant_id),
  ticket_id      text NOT NULL,
  analysis_id    uuid,
  kind           text NOT NULL,
  model_id       text,
  input_tokens   integer NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
  output_tokens  integer NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
  cost_usd       numeric(12,6) NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
  created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS llm_usage_tenant_idx
  ON app.llm_usage (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS llm_usage_ticket_idx
  ON app.llm_usage (tenant_id, ticket_id);

ALTER TABLE app.llm_usage ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.llm_usage FORCE ROW LEVEL SECURITY;

CREATE POLICY llm_usage_isolation ON app.llm_usage
  USING (tenant_id = app.current_tenant_id())
  WITH CHECK (tenant_id = app.current_tenant_id());

CREATE TRIGGER llm_usage_append_only
BEFORE UPDATE OR DELETE ON app.llm_usage
FOR EACH ROW EXECUTE FUNCTION app.reject_append_only_mutation();
