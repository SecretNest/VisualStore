import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, get } from "../api";
import { formatWhen } from "../format";
import type { Folder, Group } from "../types";

const PAGE = 50;
const STATUSES = ["open", "auto_resolved", "resolved", "dismissed"];

export default function Groups() {
  const { name = "" } = useParams();
  const [folders, setFolders] = useState<Folder[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [status, setStatus] = useState<string>("open");
  const [folderId, setFolderId] = useState<string>("");
  const [identical, setIdentical] = useState(false);
  const [minSize, setMinSize] = useState(2);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const query = useCallback(
    (off: number) => {
      const q = new URLSearchParams({ limit: String(PAGE), offset: String(off), min_size: String(minSize) });
      if (status) q.set("status", status);
      if (folderId) q.set("folder_id", folderId);
      if (identical) q.set("has_identical", "true");
      return `/api/p/${name}/groups?${q.toString()}`;
    },
    [name, status, folderId, identical, minSize],
  );

  const load = useCallback(async () => {
    try {
      const [first, c, f] = await Promise.all([
        get<Group[]>(query(0)),
        get<Record<string, number>>(`/api/p/${name}/groups/counts`),
        get<Folder[]>(`/api/p/${name}/folders`),
      ]);
      setGroups(first);
      setOffset(first.length);
      setCounts(c);
      setFolders(f);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load groups.");
    }
  }, [name, query]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [load]);

  async function more() {
    const next = await get<Group[]>(query(offset));
    setGroups((prev) => [...prev, ...next]);
    setOffset(offset + next.length);
  }

  return (
    <section>
      <h1>Groups</h1>
      <p className="muted">
        {counts.open ?? 0} open · {counts.auto_resolved ?? 0} automatic · {counts.resolved ?? 0} resolved ·{" "}
        {counts.dismissed ?? 0} dismissed
        {counts.dirty ? ` · ${counts.dirty} files waiting for regrouping` : ""}
      </p>
      <form className="inline" onSubmit={(e) => e.preventDefault()}>
        <label>
          Status
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">any</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s.replace("_", " ")}
              </option>
            ))}
          </select>
        </label>
        <label>
          Folder
          <select value={folderId} onChange={(e) => setFolderId(e.target.value)}>
            <option value="">any</option>
            {folders.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </select>
        </label>
        <label className="inline-label">
          <input type="checkbox" checked={identical} onChange={(e) => setIdentical(e.target.checked)} /> byte-identical
          only
        </label>
        <label>
          Min size
          <input
            type="number"
            min={2}
            value={minSize}
            style={{ width: "4rem" }}
            onChange={(e) => setMinSize(Number(e.target.value) || 2)}
          />
        </label>
      </form>
      {groups.length === 0 ? (
        <p className="muted">No groups match.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Files</th>
              <th>Folders</th>
              <th>First member</th>
              <th>Status</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((g) => (
              <tr key={g.id} className={`group-${g.status}`}>
                <td>
                  <Link to={`/p/${name}/groups/${g.id}`}>{g.member_count} files</Link>
                  {g.has_identical && <span className="tag">identical</span>}
                </td>
                <td>{[...new Set(g.members.map((m) => m.folder_name))].join(", ")}</td>
                <td className="muted">{g.members[0]?.relative_path}</td>
                <td>{g.status.replace("_", " ")}</td>
                <td className="muted">{formatWhen(g.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {groups.length >= PAGE && groups.length === offset && (
        <button type="button" onClick={() => void more()}>
          Load more
        </button>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
