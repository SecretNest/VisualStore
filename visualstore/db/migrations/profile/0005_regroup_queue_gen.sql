-- A mark set while the regroup job works on the same file must survive that job: the job
-- clears only the generation it saw (audit 2026-11-07).
ALTER TABLE regroup_queue ADD COLUMN gen INTEGER NOT NULL DEFAULT 0;
