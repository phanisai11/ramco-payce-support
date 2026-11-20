BEGIN;

-- Run with the migration role. Runtime roles receive SELECT explicitly in IaC.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE app.shared_kb_chunk (
  region text NOT NULL CHECK (region IN ('IN', 'UAE')),
  snapshot_version text NOT NULL,
  chunk_id text NOT NULL,
  kb_id text NOT NULL,
  topic text NOT NULL,
  language text NOT NULL,
  valid_from timestamptz NOT NULL,
  valid_to timestamptz,
  content_masked text NOT NULL,
  content_sha256 char(64) NOT NULL CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
  embedding_profile text NOT NULL,
  embedding vector(1536) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (region, snapshot_version, chunk_id),
  CHECK (valid_to IS NULL OR valid_to > valid_from)
);

-- B-tree predicates narrow region/snapshot/effective-time candidates.
CREATE INDEX shared_kb_chunk_filter_idx
ON app.shared_kb_chunk (
  region,
  snapshot_version,
  embedding_profile,
  valid_from,
  valid_to
);

-- Cosine HNSW serves ORDER BY embedding <=> query_vector LIMIT k.
CREATE INDEX shared_kb_chunk_embedding_hnsw_idx
ON app.shared_kb_chunk
USING hnsw (embedding vector_cosine_ops);

CREATE TRIGGER shared_kb_chunk_append_only
BEFORE UPDATE OR DELETE ON app.shared_kb_chunk
FOR EACH ROW EXECUTE FUNCTION app.reject_append_only_mutation();

REVOKE ALL ON TABLE app.shared_kb_chunk FROM PUBLIC;

COMMIT;
