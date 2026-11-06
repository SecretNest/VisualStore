export interface Profile {
  name: string;
  display_name: string;
  created_at: string;
  sort_order: number;
}

export interface Job {
  id: string;
  type: string;
  params: Record<string, unknown>;
  state: "queued" | "running" | "done" | "failed" | "cancelled";
  priority: number;
  kind: "io" | "compute";
  attempts: number;
  progress: { done?: number; total?: number; message?: string; result?: unknown };
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface Overview {
  profile: Profile;
  jobs: Record<Job["state"], number>;
  recent_jobs: Job[];
  last_backup: { metadata: string | null; vectors: string | null };
  usage_bytes: number;
}

export interface SystemStatus {
  version: string;
  compute_processes: number;
  alive_workers: number;
  in_flight: number;
  profiles: number;
}
