-- Modality-processor output is derived data → append-only side table.
-- attachment_manifest stays immutable (migration 001 trigger); each
-- processor run appends a row here, latest wins on read.
CREATE TABLE IF NOT EXISTS app.attachment_processing (
  tenant_id text NOT NULL,
  attachment_id uuid NOT NULL,
  manifest_version integer NOT NULL CHECK (manifest_version > 0),
  event_id uuid NOT NULL,
  processor text NOT NULL,
  filename text,
  extracted_text text,
  extracted_summary text,
  processed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, attachment_id, manifest_version, processor),
  FOREIGN KEY (tenant_id, attachment_id, manifest_version)
    REFERENCES app.attachment_manifest (tenant_id, attachment_id, manifest_version),
  FOREIGN KEY (tenant_id) REFERENCES app.tenant_profile (tenant_id)
);

ALTER TABLE app.attachment_processing ENABLE ROW LEVEL SECURITY;
ALTER TABLE app.attachment_processing FORCE ROW LEVEL SECURITY;

CREATE POLICY attachment_processing_isolation ON app.attachment_processing
  USING (tenant_id = app.current_tenant_id())
  WITH CHECK (tenant_id = app.current_tenant_id());

CREATE TRIGGER attachment_processing_append_only
BEFORE UPDATE OR DELETE ON app.attachment_processing
FOR EACH ROW EXECUTE FUNCTION app.reject_append_only_mutation();

-- Extraction columns moved off the immutable manifest.
ALTER TABLE app.attachment_manifest
  DROP COLUMN IF EXISTS extracted_text,
  DROP COLUMN IF EXISTS extracted_summary,
  DROP COLUMN IF EXISTS processor,
  DROP COLUMN IF EXISTS processed_at;
