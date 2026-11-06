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
  folders: {
    id: string;
    name: string;
    role: "archive" | "inbox";
    enabled: boolean;
    counts: Record<string, number>;
  }[];
  groups: Record<string, number>;
  dirty: number;
}

export interface IgnoredDirectory {
  id: string;
  directory: string;
  recursive: boolean;
}

export interface Folder {
  id: string;
  name: string;
  path: string;
  role: "archive" | "inbox";
  compare_scope: "all" | "other_directories" | "other_folders";
  enabled: boolean;
  priority: number;
  created_at: string;
  ignored: IgnoredDirectory[];
}

export interface Extension {
  id: string;
  extension: string;
  media_type: "image" | "animated" | "video" | "other";
  enabled: boolean;
}

export type Decision = "keep" | "delete" | "undecided";

export interface GroupMember {
  file_id: string;
  decision: Decision;
  directory: string;
  name: string;
  size: number;
  sha1: string | null;
  state: string;
  folder_id: string;
  folder_name: string;
  folder_path: string;
  role: "archive" | "inbox";
  folder_priority: number;
  relative_path: string;
  absolute_path: string;
}

export type Band = "identical" | "near_duplicate" | "variant" | "candidate";

export interface Group {
  id: string;
  member_count: number;
  min_distance: number | null;
  has_identical: boolean;
  band: Band;
  status: "open" | "auto_resolved" | "resolved" | "dismissed";
  updated_at: string;
  members: GroupMember[];
}

export interface Pair {
  file_a: string;
  file_b: string;
  distance: number | null;
  source: "sha1" | "vector" | "import";
  judgment: "none" | "related_keep" | "unrelated";
  judged_at: string | null;
  judgment_origin: "user" | "import_v1" | "rule" | null;
  model_id: string | null;
}

export interface Neighbour {
  file_id: string;
  distance: number;
}

export interface GroupDetail {
  group: Group;
  pairs: Pair[];
  neighbours: Record<string, Neighbour | null>;
}

export interface TrashEntry {
  id: string;
  type: string;
  file_id: string | null;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  at: string;
}

export interface SystemStatus {
  version: string;
  compute_processes: number;
  alive_workers: number;
  in_flight: number;
  profiles: number;
}

export interface ModelInfo {
  id: string;
  dim: number;
  input_size: number;
  description: string;
  available: boolean;
}

export interface EvalReport {
  file: string;
  size: number;
  time: number;
}

export interface Candidate {
  file_id: string;
  distance: number;
  judgment: Pair["judgment"];
  relative_path: string;
  folder_name: string;
  group_id: string | null;
}

export interface FileDetail {
  id: string;
  folder_id: string;
  folder_name: string;
  directory: string;
  name: string;
  size: number;
  mtime: number;
  sha1: string | null;
  state: string;
  media_type: "image" | "animated" | "video" | "other";
  relative_path: string;
  absolute_path: string;
  group_id: string | null;
  pairs: Pair[];
  candidates: Candidate[];
}
