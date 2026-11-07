import { get } from "./api";
import type { Job } from "./types";

export const FINISHED = new Set(["done", "failed", "cancelled"]);

/** Poll one job until it has finished or `tries` polls have passed; returns the last state seen. */
export async function waitForJob(name: string, id: string, tries = 60, delayMs = 500): Promise<Job> {
  let job = await get<Job>(`/api/p/${name}/jobs/${id}`);
  for (let i = 0; i < tries && !FINISHED.has(job.state); i++) {
    await new Promise((r) => window.setTimeout(r, delayMs));
    job = await get<Job>(`/api/p/${name}/jobs/${id}`);
  }
  return job;
}

/** What a finished job has to say: its error, or the `skipped` note some jobs leave in the result. */
export function jobOutcome(job: Job): string | null {
  if (job.state === "failed") return job.error ?? "failed";
  const result = (job.progress as { result?: { skipped?: string } } | null)?.result;
  return result?.skipped ?? null;
}
