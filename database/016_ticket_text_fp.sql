-- Raw-text fingerprint for pre-LLM dedup: stamped alongside the canonical
-- symptom_fingerprint so an identical re-report is caught before any LLM
-- call (signals/answer/queue-suggestion all skipped).
ALTER TABLE app.ticket_current
  ADD COLUMN IF NOT EXISTS text_fp text;

CREATE INDEX IF NOT EXISTS ticket_current_text_fp_idx
  ON app.ticket_current (tenant_id, contact_id, text_fp)
  WHERE contact_id IS NOT NULL AND text_fp IS NOT NULL;
