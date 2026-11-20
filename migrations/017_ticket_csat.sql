-- Post-resolution CSAT capture: one rating per ticket, submitted via the
-- customer survey channel (dashboard API today, webhook later).
ALTER TABLE app.ticket_current
  ADD COLUMN IF NOT EXISTS csat_score smallint
    CHECK (csat_score IS NULL OR csat_score BETWEEN 1 AND 5),
  ADD COLUMN IF NOT EXISTS csat_at timestamptz,
  ADD COLUMN IF NOT EXISTS csat_comment text;

CREATE INDEX IF NOT EXISTS ticket_current_csat_idx
  ON app.ticket_current (tenant_id, csat_score)
  WHERE csat_score IS NOT NULL;
