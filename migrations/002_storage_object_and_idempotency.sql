BEGIN;

-- Per-object immutable storage manifests (raw body, envelope, attachments).
-- Aligns intake with multiple GCS objects per event; see SAAS_TENANT_DATA_CONTRACT.md.

-- One row per immutable GCS object (raw body, envelope JSON, attachment binaries).
-- event_id is the intake event UUID; linkage to app.raw_event_manifest is enforced by
-- the application when the aggregate raw row is written (objects may be stored first).
CREATE TABLE app.storage_object (
  tenant_id text NOT NULL REFERENCES app.tenant_profile (tenant_id),
  object_id uuid NOT NULL,
  region text NOT NULL CHECK (region IN ('IN', 'UAE')),
  event_id uuid NOT NULL,
  attachment_id uuid,
  object_path text NOT NULL,
  object_generation bigint NOT NULL CHECK (object_generation > 0),
  sha256 char(64) NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
  size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, object_id),
  UNIQUE (tenant_id, object_path),
  UNIQUE (object_id)
);

-- object_id is globally unique; quarantine access remains restricted to its service role.
CREATE TABLE intake_quarantine.storage_object (
  object_id uuid PRIMARY KEY,
  ingress_id uuid NOT NULL,
  ingress_region text NOT NULL CHECK (ingress_region IN ('IN', 'UAE')),
  event_id uuid NOT NULL,
  attachment_id uuid,
  object_path text NOT NULL UNIQUE,
  object_generation bigint NOT NULL CHECK (object_generation > 0),
  sha256 char(64) NOT NULL CHECK (sha256 ~ '^[a-f0-9]{64}$'),
  size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
  created_at timestamptz NOT NULL DEFAULT now()
);

-- Idempotency is keyed before tenant resolution; keep in quarantine schema (no tenant RLS).
CREATE TABLE intake_quarantine.intake_idempotency (
  idempotency_key text PRIMARY KEY,
  request_fingerprint char(64) NOT NULL CHECK (request_fingerprint ~ '^[a-f0-9]{64}$'),
  result_json jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE app.storage_object ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.storage_object FORCE ROW LEVEL SECURITY;

CREATE POLICY storage_object_isolation ON app.storage_object
  USING (tenant_id = app.current_tenant_id())
  WITH CHECK (tenant_id = app.current_tenant_id());

CREATE TRIGGER storage_object_append_only
BEFORE UPDATE OR DELETE ON app.storage_object
FOR EACH ROW EXECUTE FUNCTION app.reject_append_only_mutation();

CREATE TRIGGER quarantine_storage_object_append_only
BEFORE UPDATE OR DELETE ON intake_quarantine.storage_object
FOR EACH ROW EXECUTE FUNCTION app.reject_append_only_mutation();

CREATE TRIGGER intake_idempotency_append_only
BEFORE UPDATE OR DELETE ON intake_quarantine.intake_idempotency
FOR EACH ROW EXECUTE FUNCTION app.reject_append_only_mutation();

REVOKE ALL ON TABLE app.storage_object FROM PUBLIC;
REVOKE ALL ON TABLE intake_quarantine.storage_object FROM PUBLIC;
REVOKE ALL ON TABLE intake_quarantine.intake_idempotency FROM PUBLIC;

COMMIT;
