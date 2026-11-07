import { FormEvent, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, del, get, patch, post } from "../api";
import { useLayout } from "../components/Layout";
import { formatBytes } from "../format";
import { setLastProfile } from "../lastProfile";
import type { Profile, SystemStatus } from "../types";

export default function Profiles() {
  const { profiles, reloadProfiles } = useLayout();
  const navigate = useNavigate();
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [usage, setUsage] = useState<Record<string, number>>({});
  const [name, setName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<Profile | null>(null);
  const [confirm, setConfirm] = useState("");

  useEffect(() => {
    void get<SystemStatus>("/api/system/status").then(setStatus);
  }, [profiles]);

  useEffect(() => {
    for (const p of profiles) {
      void get<{ bytes: number }>(`/api/profiles/${p.name}/usage`).then((u) =>
        setUsage((prev) => ({ ...prev, [p.name]: u.bytes })),
      );
    }
  }, [profiles]);

  async function create(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const created = await post<Profile>("/api/profiles", { name, display_name: displayName });
      setName("");
      setDisplayName("");
      await reloadProfiles();
      setLastProfile(created.name);
      navigate(`/p/${created.name}/overview`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create the profile.");
    }
  }

  async function rename(p: Profile) {
    const next = window.prompt("Display name", p.display_name);
    if (!next || next === p.display_name) return;
    try {
      await patch(`/api/profiles/${p.name}`, { display_name: next });
      await reloadProfiles();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not rename the profile.");
    }
  }

  async function remove(e: FormEvent) {
    e.preventDefault();
    if (!deleting) return;
    setError(null);
    try {
      await del(`/api/profiles/${deleting.name}`, { confirm_name: confirm });
      setDeleting(null);
      setConfirm("");
      await reloadProfiles();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not delete the profile.");
    }
  }

  return (
    <section>
      <h1>Profiles</h1>
      {status && (
        <p className="muted">
          VisualStore {status.version} · compute workers {status.alive_workers}/
          {status.compute_processes}
          {status.alive_workers < status.compute_processes &&
            " (some workers have died; restart the container)"}
          {" · "}
          jobs in flight {status.in_flight}
        </p>
      )}
      {profiles.length === 0 && <p>No profiles yet. Create one to get started.</p>}
      <div className="table-wrap">
        <table>
          <tbody>
            {profiles.map((p) => (
              <tr key={p.name}>
                <td>
                  <strong>{p.display_name}</strong> <code>{p.name}</code>
                </td>
                <td className="muted">
                  {usage[p.name] === undefined ? "…" : formatBytes(usage[p.name])}
                </td>
                <td className="actions">
                  <button
                    type="button"
                    onClick={() => {
                      setLastProfile(p.name);
                      navigate(`/p/${p.name}/overview`);
                    }}
                  >
                    Open
                  </button>
                  <button type="button" onClick={() => rename(p)}>
                    Rename
                  </button>
                  <button type="button" className="danger" onClick={() => setDeleting(p)}>
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>New profile</h2>
      <form onSubmit={create} className="inline">
        <label>
          Name
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="lowercase-and-digits"
            pattern="[a-z0-9]+(-[a-z0-9]+)*"
            required
          />
        </label>
        <label>
          Display name
          <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        </label>
        <button type="submit">Create</button>
      </form>

      {deleting && (
        <form onSubmit={remove} className="danger-box">
          <p>
            Deleting <strong>{deleting.display_name}</strong> removes its database, vectors,
            index and thumbnails. Media files are not touched. Type <code>{deleting.name}</code>{" "}
            to confirm.
          </p>
          <input value={confirm} onChange={(e) => setConfirm(e.target.value)} autoFocus />
          <button type="submit" className="danger" disabled={confirm !== deleting.name}>
            Delete profile
          </button>
          <button type="button" onClick={() => setDeleting(null)}>
            Cancel
          </button>
        </form>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
