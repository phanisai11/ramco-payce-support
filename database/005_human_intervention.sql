-- Audit timestamp: first human (agent) intervention on a ticket.

ALTER TABLE app.ticket_current
  ADD COLUMN IF NOT EXISTS human_intervened_at timestamptz;
