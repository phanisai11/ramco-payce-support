-- Allow llm_queue analysis records (LLM queue suggestions for tickets the
-- deterministic rule base could not classify).
BEGIN;

ALTER TABLE app.ticket_analysis
  DROP CONSTRAINT ticket_analysis_kind_check;
ALTER TABLE app.ticket_analysis
  ADD CONSTRAINT ticket_analysis_kind_check
  CHECK (kind IN ('llm_signals', 'llm_answer', 'draft_eval', 'llm_queue'));

COMMIT;
