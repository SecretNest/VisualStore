import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError, get, post } from "../api";
import { formatWhen } from "../format";
import { setLastProfile } from "../lastProfile";
import type { Job, Overview as OverviewData } from "../types";

function JobRow({ job }: { job: Job }) {
  const p = job.progress;
  const progress =
    p.total && job.state === "running"
      ? ` ${p.done ?? 0}/${p.total}`
      : p.message
        ? ` · ${p.message}`
        : "";
  return (
    <tr className={`job-${job.state}`}>
      <td>{job.type}</td>
      <td>
        {job.state}
        {progress}
        {job.attempts > 0 && ` (attempt ${job.attempts})`}
      </td>
      <td className="muted">{job.error ?? ""}</td>
      <td className="muted">{formatWhen(job.created_at)}</td>
    </tr>
  );
}

export default function Overview() {
  const { name = "" } = useParams();
  const [data, setData] = useState<OverviewData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [backingUp, setBackingUp] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setData(await get<OverviewData>(`/api/p/${name}/overview`));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load the overview.");
    }
  }, [name]);

  useEffect(() => {
    setLastProfile(name);
    void refresh();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [name, refresh]);

  async function backupNow() {
    if (backingUp) return;
    setBackingUp(true);
    try {
      await post(`/api/p/${name}/backups`, {});
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not queue the backup.");
    } finally {
      setBackingUp(false);
    }
  }

  const banner = error && (
    <p className="error" role="alert">
      {error}
    </p>
  );
  if (!data) return banner || <p className="muted">Loading…</p>;

  return (
    <section>
      {banner}
      <h1>{data.profile.display_name}</h1>
      <div className="cards">
        <div className="card">
          <h2>Jobs</h2>
          <p>
            {data.jobs.running} running · {data.jobs.queued} queued · {data.jobs.failed} failed
          </p>
        </div>
        <div className="card">
          <h2>Backups</h2>
          <p>Metadata: {formatWhen(data.last_backup.metadata)}</p>
          <p>Vectors: {formatWhen(data.last_backup.vectors)}</p>
          <button type="button" onClick={() => void backupNow()} disabled={backingUp}>
            Back up now
          </button>
        </div>
        <div className="card">
          <h2>Groups</h2>
          <p>
            {data.groups.open ?? 0} open · {data.groups.auto_resolved ?? 0} automatic · {data.groups.resolved ?? 0}{" "}
            resolved · {data.groups.dismissed ?? 0} dismissed
            {data.dirty ? ` · ${data.dirty} files waiting for regrouping` : ""}
          </p>
        </div>
        <div className="card">
          <h2>Data</h2>
          <p>{(data.usage_bytes / 1024 / 1024).toFixed(1)} MB on disk</p>
        </div>
      </div>
      {data.folders.some((f) => f.enabled && !f.available) && (
        <p className="error" role="alert">
          Some Folder paths are not available inside the container: check the media mounts.
        </p>
      )}
      <h2>Folders</h2>
      {data.folders.length === 0 ? (
        <p className="muted">No Folders yet.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Folder</th>
                <th>Role</th>
                <th>New</th>
                <th>Fingerprinted</th>
                <th>Not media</th>
                <th>Unreadable</th>
              </tr>
            </thead>
            <tbody>
              {data.folders.map((f) => (
                <tr key={f.id} className={f.enabled ? "" : "muted"}>
                  <td>
                    {f.name}
                    {!f.enabled && <span className="tag">disabled</span>}
                    {!f.available && <span className="tag danger">path not available</span>}
                  </td>
                  <td>{f.role}</td>
                  <td>{f.counts.new ?? 0}</td>
                  <td>{f.counts.fingerprinted ?? 0}</td>
                  <td>{f.counts.not_media ?? 0}</td>
                  <td>{f.counts.unreadable ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <h2>Recent jobs</h2>
      {data.recent_jobs.length === 0 ? (
        <p className="muted">No jobs yet.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Type</th>
                <th>State</th>
                <th>Error</th>
                <th>Created</th>
              </tr>
            </thead>
            <tbody>
              {data.recent_jobs.map((j) => (
                <JobRow key={j.id} job={j} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
