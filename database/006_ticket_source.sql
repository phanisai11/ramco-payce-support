-- Channel/source on the ticket projection for per-channel analytics.
ALTER TABLE app.ticket_current
  ADD COLUMN IF NOT EXISTS source text;
