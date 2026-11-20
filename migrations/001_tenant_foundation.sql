BEGIN;

CREATE SCHEMA IF NOT EXISTS app;
CREATE SCHEMA IF NOT EXISTS intake_quarantine;

REVOKE ALL ON SCHEMA intake_quarantine FROM PUBLIC;

CREATE OR REPLACE FUNCTION app.current_tenant_id()
RETURNS text
LANGUAGE sql
STABLE
AS $$
  SELECT NULLIF(current_setting('app.tenant_id', true), '');
$$;

CREATE OR REPLACE FUNCTION app.reject_append_only_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION '% is append-only; create a superseding row', TG_TABLE_NAME
    USING ERRCODE = '55000';
END;
$$;

CREATE TABLE app.tenant_profile (
  tenant_id text PRIMARY KEY CHECK (tenant_id ~ '^C-[0-9]{4,}$'),
  tenant_uuid uuid NOT NULL UNIQUE,
  region text NOT NULL CHECK (region IN ('IN', 'UAE')),
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE intake_quarantine.raw_event_manifest (
  event_id uuid PRIMARY KEY,
  ingress_id uuid NOT NULL,
  ingress_region text NOT NULL CHECK (ingress_region IN ('IN', 'UAE')),
  source text NOT NULL,
  source_event_id text NOT NULL,
  object_id uuid NOT NULL UNIQUE,
  object_path text NOT NULL UNIQUE,
  object_generation bigint NOT NULL CHECK (object_generation > 0),
  sha256 char(64) NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
  size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
  received_at timestamptz NOT NULL,
  UNIQUE (ingress_id, source, source_event_id)
);

CREATE TABLE app.raw_event_manifest (
  tenant_id text NOT NULL,
  event_id uuid NOT NULL,
  region text NOT NULL CHECK (region IN ('IN', 'UAE')),
  source text NOT NULL,
  source_event_id text NOT NULL,
  object_id uuid NOT NULL,
  object_path text NOT NULL,
  object_generation bigint NOT NULL CHECK (object_generation > 0),
  sha256 char(64) NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
  size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
  received_at timestamptz NOT NULL,
  quarantine_event_id uuid,
  PRIMARY KEY (tenant_id, event_id),
  UNIQUE (tenant_id, object_id),
  UNIQUE (tenant_id, object_path),
  UNIQUE (tenant_id, source, source_event_id),
  FOREIGN KEY (tenant_id) REFERENCES app.tenant_profile (tenant_id)
);

CREATE TABLE app.attachment_manifest (
  tenant_id text NOT NULL,
  attachment_id uuid NOT NULL,
  event_id uuid NOT NULL,
  manifest_version integer NOT NULL CHECK (manifest_version > 0),
  region text NOT NULL CHECK (region IN ('IN', 'UAE')),
  object_id uuid NOT NULL,
  object_path text NOT NULL,
  object_generation bigint NOT NULL CHECK (object_generation > 0),
  sha256 char(64) NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
  size_bytes bigint NOT NULL CHECK (size_bytes BETWEEN 0 AND 52428800),
  detected_media_type text,
  scan_status text NOT NULL CHECK (scan_status IN ('clean', 'blocked', 'pending')),
  pii_class text NOT NULL CHECK (pii_class IN ('none', 'sensitive', 'restricted')),
  processing_status text NOT NULL CHECK (
    processing_status IN ('metadata_only', 'redacted_ready', 'human_only')
  ),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, attachment_id, manifest_version),
  UNIQUE (tenant_id, object_id),
  UNIQUE (tenant_id, object_path),
  FOREIGN KEY (tenant_id, event_id)
    REFERENCES app.raw_event_manifest (tenant_id, event_id)
);

CREATE TABLE app.canonical_ticket_event (
  tenant_id text NOT NULL,
  event_id uuid NOT NULL,
  canonical_version integer NOT NULL CHECK (canonical_version > 0),
  ticket_id text NOT NULL,
  case_id text NOT NULL,
  region text NOT NULL CHECK (region IN ('IN', 'UAE')),
  raw_sha256 char(64) NOT NULL CHECK (raw_sha256 ~ '^[a-f0-9]{64}$'),
  canonical_json jsonb NOT NULL,
  normalizer_version text NOT NULL,
  dlp_policy_version text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, event_id, canonical_version),
  FOREIGN KEY (tenant_id, event_id)
    REFERENCES app.raw_event_manifest (tenant_id, event_id)
);

CREATE TABLE app.routing_decision (
  tenant_id text NOT NULL,
  decision_id uuid NOT NULL,
  event_id uuid NOT NULL,
  canonical_version integer NOT NULL,
  input_fingerprint char(64) NOT NULL CHECK (input_fingerprint ~ '^[a-f0-9]{64}$'),
  policy_version text NOT NULL,
  lane text NOT NULL CHECK (
    lane IN ('block', 'status', 'file_check', 'incident', 'auto_answer', 'draft_for_agent', 'human')
  ),
  queue_id text,
  auto_route boolean NOT NULL,
  decided_by text NOT NULL CHECK (
    decided_by IN ('rule', 'policy_table', 'routing_scorer', 'human', 'suggest', 'llm')
  ),
  rule_id text,
  reason_codes text[] NOT NULL CHECK (cardinality(reason_codes) > 0),
  confidence numeric(5,4) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  supersedes_decision_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, decision_id),
  FOREIGN KEY (tenant_id, event_id, canonical_version)
    REFERENCES app.canonical_ticket_event (tenant_id, event_id, canonical_version),
  FOREIGN KEY (tenant_id, supersedes_decision_id)
    REFERENCES app.routing_decision (tenant_id, decision_id),
  CHECK (
    NOT auto_route
    OR (
      queue_id IS NOT NULL
      AND rule_id IS NOT NULL
      AND decided_by IN ('rule', 'policy_table', 'routing_scorer')
    )
  ),
  CHECK (NOT (auto_route AND decided_by IN ('suggest', 'llm')))
);

CREATE TABLE app.ticket_current (
  tenant_id text NOT NULL,
  ticket_id text NOT NULL,
  event_id uuid NOT NULL,
  canonical_version integer NOT NULL,
  current_decision_id uuid,
  queue_id text,
  status text NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, ticket_id),
  FOREIGN KEY (tenant_id, event_id, canonical_version)
    REFERENCES app.canonical_ticket_event (tenant_id, event_id, canonical_version),
  FOREIGN KEY (tenant_id, current_decision_id)
    REFERENCES app.routing_decision (tenant_id, decision_id)
);

ALTER TABLE app.tenant_profile ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.tenant_profile FORCE ROW LEVEL SECURITY;
ALTER TABLE app.raw_event_manifest ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.raw_event_manifest FORCE ROW LEVEL SECURITY;
ALTER TABLE app.attachment_manifest ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.attachment_manifest FORCE ROW LEVEL SECURITY;
ALTER TABLE app.canonical_ticket_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.canonical_ticket_event FORCE ROW LEVEL SECURITY;
ALTER TABLE app.routing_decision ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.routing_decision FORCE ROW LEVEL SECURITY;
ALTER TABLE app.ticket_current ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.ticket_current FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_profile_isolation ON app.tenant_profile
  USING (tenant_id = app.current_tenant_id())
  WITH CHECK (tenant_id = app.current_tenant_id());
CREATE POLICY raw_event_isolation ON app.raw_event_manifest
  USING (tenant_id = app.current_tenant_id())
  WITH CHECK (tenant_id = app.current_tenant_id());
CREATE POLICY attachment_isolation ON app.attachment_manifest
  USING (tenant_id = app.current_tenant_id())
  WITH CHECK (tenant_id = app.current_tenant_id());
CREATE POLICY canonical_event_isolation ON app.canonical_ticket_event
  USING (tenant_id = app.current_tenant_id())
  WITH CHECK (tenant_id = app.current_tenant_id());
CREATE POLICY routing_decision_isolation ON app.routing_decision
  USING (tenant_id = app.current_tenant_id())
  WITH CHECK (tenant_id = app.current_tenant_id());
CREATE POLICY ticket_current_isolation ON app.ticket_current
  USING (tenant_id = app.current_tenant_id())
  WITH CHECK (tenant_id = app.current_tenant_id());

CREATE TRIGGER raw_event_append_only
BEFORE UPDATE OR DELETE ON app.raw_event_manifest
FOR EACH ROW EXECUTE FUNCTION app.reject_append_only_mutation();
CREATE TRIGGER attachment_manifest_append_only
BEFORE UPDATE OR DELETE ON app.attachment_manifest
FOR EACH ROW EXECUTE FUNCTION app.reject_append_only_mutation();
CREATE TRIGGER canonical_event_append_only
BEFORE UPDATE OR DELETE ON app.canonical_ticket_event
FOR EACH ROW EXECUTE FUNCTION app.reject_append_only_mutation();
CREATE TRIGGER routing_decision_append_only
BEFORE UPDATE OR DELETE ON app.routing_decision
FOR EACH ROW EXECUTE FUNCTION app.reject_append_only_mutation();

REVOKE ALL ON ALL TABLES IN SCHEMA app FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA app FROM PUBLIC;

COMMIT;
