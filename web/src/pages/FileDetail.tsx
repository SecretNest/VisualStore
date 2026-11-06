import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, get, post, put } from "../api";
import Thumb from "../components/Thumb";
import { formatBytes } from "../format";
import type { FileDetail as Detail, Folder, Job } from "../types";

export default function FileDetail() {
  const { name = "", fileId = "" } = useParams();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [newName, setNewName] = useState("");
  const [moveTo, setMoveTo] = useState({ folder_id: "", directory: "" });

  const load = useCallback(async () => {
    try {
      const [d, f] = await Promise.all([get<Detail>(`/api/p/${name}/files/${fileId}`), get<Folder[]>(`/api/p/${name}/folders`)]);
      setDetail(d);
      setFolders(f);
      setNewName(d.name);
      setMoveTo({ folder_id: d.folder_id, directory: d.directory });
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load the file.");
    }
  }, [name, fileId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function judge(other: string, judgment: "related_keep" | "unrelated") {
    setError(null);
    try {
      await put(`/api/p/${name}/pairs/${fileId}/${other}/judgment`, { judgment });
      setNotice(judgment === "unrelated" ? "Marked unrelated; groups are being recomputed." : "Marked related; both files are kept.");
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not record the judgment.");
    }
  }

  async function act(path: string, body: unknown, what: string) {
    setError(null);
    try {
      const job = await post<Job>(path, body);
      setNotice(`${what} queued as job ${job.id}. The page updates once it has run.`);
      window.setTimeout(() => void load(), 2000);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : `Could not ${what.toLowerCase()}.`);
    }
  }

  if (error && !detail)
    return (
      <p className="error" role="alert">
        {error}
      </p>
    );
  if (!detail) return <p className="muted">Loading…</p>;

  return (
    <section>
      <h1>
        <code>{detail.relative_path}</code>
      </h1>
      <div className="file-head">
        <Thumb name={name} fileId={detail.id} size={160} />
        <dl>
          <dt>Folder</dt>
          <dd>{detail.folder_name}</dd>
          <dt>Size</dt>
          <dd>{formatBytes(detail.size)}</dd>
          <dt>Type</dt>
          <dd>{detail.media_type}</dd>
          <dt>State</dt>
          <dd>{detail.state}</dd>
          <dt>SHA-1</dt>
          <dd>
            <code>{detail.sha1 ?? "—"}</code>
          </dd>
          <dt>Group</dt>
          <dd>{detail.group_id ? <Link to={`/p/${name}/groups/${detail.group_id}`}>open group</Link> : "none"}</dd>
        </dl>
      </div>
      <p>
        <a href={`/api/p/${name}/files/${detail.id}/content`} target="_blank" rel="noreferrer">
          Open original
        </a>
      </p>

      <h2>Rename / move</h2>
      <form
        className="inline"
        onSubmit={(e) => {
          e.preventDefault();
          void act(`/api/p/${name}/files/${detail.id}/rename`, { name: newName }, "Rename");
        }}
      >
        <label>
          New name
          <input value={newName} onChange={(e) => setNewName(e.target.value)} required />
        </label>
        <button type="submit" disabled={newName === detail.name}>
          Rename
        </button>
      </form>
      <form
        className="inline"
        onSubmit={(e) => {
          e.preventDefault();
          void act(`/api/p/${name}/files/${detail.id}/move`, moveTo, "Move");
        }}
      >
        <label>
          Folder
          <select value={moveTo.folder_id} onChange={(e) => setMoveTo({ ...moveTo, folder_id: e.target.value })} required>
            <option value="">choose</option>
            {folders.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Directory (relative, empty for the root)
          <input value={moveTo.directory} onChange={(e) => setMoveTo({ ...moveTo, directory: e.target.value })} />
        </label>
        <button type="submit" disabled={!moveTo.folder_id}>
          Move
        </button>
      </form>

      <h2>Candidate pairs</h2>
      <p className="muted">Similar beyond the grouping threshold but within the candidate distance. Judging a pair changes the groups.</p>
      {detail.candidates.length === 0 ? (
        <p className="muted">No candidates.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>File</th>
              <th>Distance</th>
              <th>Judgment</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {detail.candidates.map((c) => (
              <tr key={c.file_id}>
                <td className="with-thumb">
                  <Thumb name={name} fileId={c.file_id} />
                  <Link to={`/p/${name}/files/${c.file_id}`}>{c.relative_path}</Link> <span className="muted">· {c.folder_name}</span>
                </td>
                <td>{c.distance.toFixed(3)}</td>
                <td>{c.judgment.replace("_", " ")}</td>
                <td className="actions">
                  <button type="button" onClick={() => void judge(c.file_id, "related_keep")} disabled={c.judgment === "related_keep"}>
                    Related, keep both
                  </button>
                  <button type="button" className="danger" onClick={() => void judge(c.file_id, "unrelated")}>
                    Unrelated
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {notice && (
        <p className="muted" role="status">
          {notice}
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
