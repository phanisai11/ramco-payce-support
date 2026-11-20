-- Per-customer dedup: same verified contact re-reporting the same symptom
-- (any channel) links to the primary ticket instead of spawning parallel
-- work. contact_id + symptom_fingerprint are stamped on every ticket at
-- ingest; dedup_of points at the primary ticket when matched.
ALTER TABLE app.ticket_current
  ADD COLUMN IF NOT EXISTS contact_id text,
  ADD COLUMN IF NOT EXISTS symptom_fingerprint text,
  ADD COLUMN IF NOT EXISTS dedup_of text;

CREATE INDEX IF NOT EXISTS ticket_current_dedup_idx
  ON app.ticket_current (tenant_id, contact_id, symptom_fingerprint)
  WHERE contact_id IS NOT NULL AND dedup_of IS NULL;
