import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, get, post } from "../api";
import Thumb from "../components/Thumb";
import { formatWhen } from "../format";
import type { BulkPreview, Folder, Group } from "../types";

const PAGE = 50;
const STATUSES = ["open", "auto_resolved", "resolved", "dismissed"];
const BANDS = ["identical", "near_duplicate", "variant", "candidate"];

interface Counts {
  open?: number;
  auto_resolved?: number;
  resolved?: number;
  dismissed?: number;
  dirty?: number;
  bands?: Record<string, number>;
}

export default function Groups() {
  const { name = "" } = useParams();
  const [folders, setFolders] = useState<Folder[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [counts, setCounts] = useState<Counts>({});
  const [status, setStatus] = useState<string>("open");
  const [folderId, setFolderId] = useState<string>("");
  const [identical, setIdentical] = useState(false);
  const [bandFilter, setBandFilter] = useState("");
  const [minSize, setMinSize] = useState(2);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [bulk, setBulk] = useState<BulkPreview | null>(null);
  const [typed, setTyped] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  const query = useCallback(
    (off: number) => {
      const q = new URLSearchParams({ limit: String(PAGE), offset: String(off), min_size: String(minSize) });
      if (status) q.set("status", status);
      if (folderId) q.set("folder_id", folderId);
      if (identical) q.set("has_identical", "true");
      if (bandFilter) q.set("band", bandFilter);
      return `/api/p/${name}/groups?${q.toString()}`;
    },
    [name, status, folderId, identical, bandFilter, minSize],
  );

  const offsetRef = useRef(0);
  useEffect(() => {
    offsetRef.current = offset;
  }, [offset]);

  const refreshCounts = useCallback(async () => {
    try {
      setCounts(await get<Counts>(`/api/p/${name}/groups/counts`));
    } catch {
      /* keep the last counts; the next full load reports errors */
    }
  }, [name]);

  const load = useCallback(async () => {
    try {
      const [first, c, f] = await Promise.all([
        get<Group[]>(query(0)),
        get<Counts>(`/api/p/${name}/groups/counts`),
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
      if (document.visibilityState !== "visible") return;
      // Once the user paged past the first page, a full reload would discard "Load more".
      if (offsetRef.current > PAGE) void refreshCounts();
      else void load();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [load, refreshCounts]);

  const filterBody = {
    band: bandFilter || null,
    folder_id: folderId || null,
    has_identical: identical ? true : null,
    min_size: minSize,
  };

  async function previewBulk() {
    setError(null);
    setNotice(null);
    try {
      setBulk(await post<BulkPreview>(`/api/p/${name}/groups/bulk/preview`, filterBody));
      setTyped("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not preview.");
    }
  }

  async function executeBulk() {
    if (!bulk) return;
    setError(null);
    try {
      const job = await post<{ id: string }>(`/api/p/${name}/groups/bulk/execute`, {
        ...filterBody,
        token: bulk.token,
        confirm: bulk.typed_confirmation_required ? Number(typed) : null,
      });
      setNotice(`Bulk resolution queued as job ${job.id}.`);
      setBulk(null);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not execute.");
    }
  }

  async function more() {
    const next = await get<Group[]>(query(offset));
    setGroups((prev) => [...prev, ...next]);
    setOffset(offset + next.length);
  }

  const num = (key: "open" | "auto_resolved" | "resolved" | "dismissed" | "dirty") => counts[key] ?? 0;
  const bands = counts.bands ?? {};
  const bandLine = Object.entries(bands)
    .map(([k, v]) => `${k.replace("_", " ")} ${v}`)
    .join(", ");

  return (
    <section>
      <h1>Groups</h1>
      <p className="muted">
        {num("open")} open · {num("auto_resolved")} automatic · {num("resolved")} resolved · {num("dismissed")} dismissed
        {num("dirty") ? ` · ${num("dirty")} files waiting for regrouping` : ""}
        {bandLine ? ` · open by band: ${bandLine}` : ""}
      </p>
      <form className="inline" onSubmit={(e) => e.preventDefault()}>
        <label>
          Status
          <select value={status} onChange={(e) => { setStatus(e.target.value); setBulk(null); }}>
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
          <select value={folderId} onChange={(e) => { setFolderId(e.target.value); setBulk(null); }}>
            <option value="">any</option>
            {folders.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Band
          <select value={bandFilter} onChange={(e) => { setBandFilter(e.target.value); setBulk(null); }}>
            <option value="">any</option>
            {BANDS.map((b) => (
              <option key={b} value={b}>
                {b.replace("_", " ")}
              </option>
            ))}
          </select>
        </label>
        <label className="inline-label">
          <input type="checkbox" checked={identical} onChange={(e) => { setIdentical(e.target.checked); setBulk(null); }} /> byte-identical
          only
        </label>
        <label>
          Min size
          <input
            type="number"
            min={2}
            value={minSize}
            style={{ width: "4rem" }}
            onChange={(e) => { setMinSize(Number(e.target.value) || 2); setBulk(null); }}
          />
        </label>
      </form>
      <details className="bulk" open={bulk !== null || notice !== null}>
        <summary>Default rules for this filter (open groups only)</summary>
        <p className="muted">
          Applies the default resolution (keep archive files, delete inbox files; inbox-only groups keep the first by
          path) to every open group matching the filter. Groups the rules cannot decide are skipped.
        </p>
        <div className="actions-row">
          <button type="button" onClick={() => void previewBulk()}>
            Preview
          </button>
          {bulk && (
            <>
              <span role="status">
                {bulk.matched} groups match, {bulk.planned} get the default resolution ({bulk.skipped} skipped),{" "}
                {bulk.files_to_delete} files will be moved to the trash
                {bulk.folders.length > 0 ? ` from ${bulk.folders.join(", ")}` : ""}.
              </span>
              {bulk.typed_confirmation_required && (
                <label className="inline-label">
                  Type {bulk.files_to_delete} to confirm
                  <input value={typed} onChange={(e) => setTyped(e.target.value)} style={{ width: "6rem" }} />
                </label>
              )}
              <button
                type="button"
                className="danger"
                disabled={bulk.planned === 0 || (bulk.typed_confirmation_required && Number(typed) !== bulk.files_to_delete)}
                onClick={() => void executeBulk()}
              >
                Execute
              </button>
            </>
          )}
        </div>
        {notice && (
          <p className="muted">
            {notice} <Link to={`/p/${name}/jobs`}>Jobs</Link>
          </p>
        )}
      </details>
      {groups.length === 0 ? (
        <p className="muted">No groups match.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Files</th>
                <th>Band</th>
                <th>Folders</th>
                <th>First member</th>
                <th>Status</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => (
                <tr key={g.id} className={`group-${g.status}`}>
                  <td className="with-thumb">
                    <Thumb name={name} fileId={g.members[0]?.file_id} />
                    <Link to={`/p/${name}/groups/${g.id}`}>{g.member_count} files</Link>
                    {g.has_identical && <span className="tag">identical</span>}
                  </td>
                  <td>{g.band.replace("_", " ")}</td>
                  <td>{[...new Set(g.members.map((m) => m.folder_name))].join(", ")}</td>
                  <td className="muted">{g.members[0]?.relative_path}</td>
                  <td>{g.status.replace("_", " ")}</td>
                  <td className="muted">{formatWhen(g.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
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
