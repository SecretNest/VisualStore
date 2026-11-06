-- Retry backoff (ADR-0008): a re-queued job is not picked before this time.
ALTER TABLE job ADD COLUMN not_before TEXT;
