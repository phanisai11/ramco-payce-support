-- Attachment modality processing results (TICKET_INTELLIGENCE_DESIGN §2).
-- Original bytes stay immutable in object storage; this row records the
-- customer-facing filename plus what the modality processor extracted.
ALTER TABLE app.attachment_manifest
  ADD COLUMN IF NOT EXISTS original_filename text,
  ADD COLUMN IF NOT EXISTS extracted_text text,
  ADD COLUMN IF NOT EXISTS extracted_summary text,
  ADD COLUMN IF NOT EXISTS processor text,
  ADD COLUMN IF NOT EXISTS processed_at timestamptz;
