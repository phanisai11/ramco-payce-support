-- Ticket conversation thread + saved LLM analyses (append-only, tenant-isolated).

CREATE TABLE app.ticket_reply (
  tenant_id text NOT NULL,
  reply_id uuid NOT NULL,
  ticket_id text NOT NULL,
  author text NOT NULL CHECK (author IN ('agent', 'customer', 'bot', 'system')),
  body text NOT NULL CHECK (length(body) BETWEEN 1 AND 20000),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, reply_id),
  FOREIGN KEY (tenant_id, ticket_id)
    REFERENCES app.ticket_current (tenant_id, ticket_id)
);

CREATE TABLE app.ticket_analysis (
  tenant_id text NOT NULL,
  analysis_id uuid NOT NULL,
  ticket_id text NOT NULL,
  canonical_version integer NOT NULL CHECK (canonical_version > 0),
  kind text NOT NULL CHECK (kind IN ('llm_signals', 'llm_answer', 'draft_eval')),
  payload_json jsonb NOT NULL,
  model_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, analysis_id),
  FOREIGN KEY (tenant_id, ticket_id)
    REFERENCES app.ticket_current (tenant_id, ticket_id)
);

ALTER TABLE app.ticket_reply ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.ticket_reply FORCE ROW LEVEL SECURITY;
ALTER TABLE app.ticket_analysis ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.ticket_analysis FORCE ROW LEVEL SECURITY;

CREATE POLICY ticket_reply_isolation ON app.ticket_reply
  USING (tenant_id = app.current_tenant_id())
  WITH CHECK (tenant_id = app.current_tenant_id());
CREATE POLICY ticket_analysis_isolation ON app.ticket_analysis
  USING (tenant_id = app.current_tenant_id())
  WITH CHECK (tenant_id = app.current_tenant_id());

CREATE TRIGGER ticket_reply_append_only
BEFORE UPDATE OR DELETE ON app.ticket_reply
FOR EACH ROW EXECUTE FUNCTION app.reject_append_only_mutation();
CREATE TRIGGER ticket_analysis_append_only
BEFORE UPDATE OR DELETE ON app.ticket_analysis
FOR EACH ROW EXECUTE FUNCTION app.reject_append_only_mutation();
