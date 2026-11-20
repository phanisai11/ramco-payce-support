-- Timestamp of entry into `resolved` so a periodic job can auto-close
-- tickets that have sat resolved > 48h. Backfill from updated_at for
-- tickets already resolved.
ALTER TABLE app.ticket_current
  ADD COLUMN IF NOT EXISTS resolved_at timestamptz;

UPDATE app.ticket_current
SET resolved_at = updated_at
WHERE status = 'resolved' AND resolved_at IS NULL;

CREATE INDEX IF NOT EXISTS ticket_current_resolved_at_idx
  ON app.ticket_current (resolved_at)
  WHERE status = 'resolved';
