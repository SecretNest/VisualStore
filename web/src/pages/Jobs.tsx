import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError, get, post, put } from "../api";
import { formatBytes, formatWhen } from "../format";
import type { EvalReport, Folder, Job, ModelInfo, TrashEntry } from "../types";

type Settings = Record<string, boolean | number | string>;
const STATES = ["queued", "running", "done", "failed", "cancelled"];

const FINISHED = new Set(["done", "failed", "cancelled"]);

/** Record the finished job ids in `seen`; true when the list holds one not seen before. */
function newlyFinished(seen: Set<string>, list: Job[]): boolean {
  let fresh = false;
  for (const j of list) {
    if (!FINISHED.has(j.state) || seen.has(j.id)) continue;
    seen.add(j.id);
    fresh = true;
  }
  return fresh;
}

export default function Jobs() {
  const { name = "" } = useParams();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [stateFilter, setStateFilter] = useState("");
  const [settings, setSettings] = useState<Settings>({});
  const [trash, setTrash] = useState<TrashEntry[]>([]);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [selectedTrash, setSelectedTrash] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [move, setMove] = useState({ source_folder_id: "", target_folder_id: "", directories: "" });
  const [imp, setImp] = useState({ db_path: "", rows: [{ name: "", path: "" }] });
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [chosenModel, setChosenModel] = useState("");
  const [reports, setReports] = useState<EvalReport[]>([]);
  const [evalForm, setEvalForm] = useState({ db_path: "", negatives: 5000 });

  const finishedRef = useRef<Set<string>>(new Set());
  const load = useCallback(async () => {
    try {
      const q = stateFilter ? `?state=${stateFilter}&limit=100` : "?limit=100";
      const [j, s, t, f, m, r] = await Promise.all([
        get<Job[]>(`/api/p/${name}/jobs${q}`),
        get<Settings>(`/api/p/${name}/settings`),
        get<TrashEntry[]>(`/api/p/${name}/trash`),
        get<Folder[]>(`/api/p/${name}/folders`),
        get<ModelInfo[]>("/api/models"),
        get<EvalReport[]>(`/api/p/${name}/eval/reports`),
      ]);
      setJobs(j);
      newlyFinished(finishedRef.current, j);
      setSettings(s);
      setTrash(t);
      setFolders(f);
      setModels(m);
      setReports(r);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load.");
    }
  }, [name, stateFilter]);

  // The poll fetches only the job list; the trash, folders, models and report are reloaded
  // once a job finishes, since that is when they can have changed.
  const loadJobs = useCallback(async () => {
    try {
      const q = stateFilter ? `?state=${stateFilter}&limit=100` : "?limit=100";
      const list = await get<Job[]>(`/api/p/${name}/jobs${q}`);
      setJobs(list);
      if (newlyFinished(finishedRef.current, list)) await load();
    } catch {
      /* the next full load reports errors */
    }
  }, [name, stateFilter, load]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void loadJobs(); // only the job list moves
    }, 3000);
    return () => window.clearInterval(timer);
  }, [load, loadJobs]);

  async function run(action: () => Promise<unknown>, done?: string) {
    setError(null);
    setNotice(null);
    try {
      await action();
      await load();
      if (done) setNotice(done);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Request failed.");
    }
  }

  function submitMove(e: FormEvent) {
    e.preventDefault();
    const directories = move.directories
      .split("\n")
      .map((d) => d.trim())
      .filter(Boolean);
    void run(async () => {
      const job = await post<Job>(`/api/p/${name}/archive-move`, { ...move, directories });
      setNotice(`Archive move queued as job ${job.id}.`);
    });
  }

  function submitImport(e: FormEvent) {
    e.preventDefault();
    const folder_paths = Object.fromEntries(imp.rows.filter((r) => r.name && r.path).map((r) => [r.name, r.path]));
    void run(async () => {
      const job = await post<Job>(`/api/p/${name}/import-v1`, { db_path: imp.db_path, folder_paths });
      setNotice(`Import queued as job ${job.id}. Watch the job list for progress.`);
    });
  }

  function submitSwitch(e: FormEvent) {
    e.preventDefault();
    void run(async () => {
      const job = await post<Job>(`/api/p/${name}/model-switch`, { model_id: chosenModel });
      setNotice(`Model switch queued as job ${job.id}.`);
    });
  }

  function submitEval(e: FormEvent) {
    e.preventDefault();
    void run(async () => {
      const job = await post<Job>(`/api/p/${name}/eval`, evalForm);
      setNotice(`Evaluation queued as job ${job.id}.`);
    });
  }

  const restore = (ids: string[]) =>
    void run(async () => {
      await post(`/api/p/${name}/trash/restore`, { action_ids: ids });
      setSelectedTrash(new Set());
    }, "Restore queued.");

  const inboxes = folders.filter((f) => f.role === "inbox");
  const archives = folders.filter((f) => f.role === "archive");

  return (
    <section>
      <h1>Jobs</h1>
      <label>
        State
        <select value={stateFilter} onChange={(e) => setStateFilter(e.target.value)}>
          <option value="">any</option>
          {STATES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </label>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Type</th>
              <th>State</th>
              <th>Progress</th>
              <th>Error</th>
              <th>Created</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id} className={`job-${j.state}`}>
                <td>{j.type}</td>
                <td>
                  {j.state}
                  {j.attempts > 0 && ` (attempt ${j.attempts})`}
                </td>
                <td className="muted">
                  {j.progress.total ? `${j.progress.done ?? 0}/${j.progress.total} ` : ""}
                  {j.progress.message ?? ""}
                  {j.state === "queued" && j.not_before && ` · retrying at ${formatWhen(j.not_before)}`}
                  {j.state === "done" && j.progress.result !== undefined && (
                    <details>
                      <summary>result</summary>
                      <pre className="result">{JSON.stringify(j.progress.result, null, 1)}</pre>
                    </details>
                  )}
                </td>
                <td className="muted">{j.error ?? ""}</td>
                <td className="muted">{formatWhen(j.created_at)}</td>
                <td className="actions">
                  {j.state === "failed" && (
                    <button type="button" onClick={() => void run(() => post(`/api/p/${name}/jobs/${j.id}/retry`))}>
                      Retry
                    </button>
                  )}
                  {j.state === "queued" && (
                    <button type="button" onClick={() => void run(() => post(`/api/p/${name}/jobs/${j.id}/cancel`))}>
                      Cancel
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>Settings</h2>
      <div className="table-wrap">
        <table>
          <tbody>
            {Object.entries(settings).map(([key, value]) => (
              <tr key={key}>
                <td>
                  <code>{key}</code>
                </td>
                <td>
                  {key === "index.quantization" ? (
                    <select
                      aria-label={key}
                      value={String(value)}
                      onChange={(e) =>
                        void run(async () => {
                          const r = await put<{ rebuild_queued?: boolean }>(`/api/p/${name}/settings/${key}`, {
                            value: e.target.value,
                          });
                          if (r.rebuild_queued) setNotice("Index option changed: a rebuild has been queued.");
                        })
                      }
                    >
                      <option value="f16">f16 (2 bytes per dimension)</option>
                      <option value="i8">i8 (1 byte per dimension, less exact)</option>
                    </select>
                  ) : typeof value === "string" ? (
                    <code>{value}</code>
                  ) : typeof value === "boolean" ? (
                    <input
                      type="checkbox"
                      aria-label={key}
                      checked={value}
                      onChange={(e) => void run(() => put(`/api/p/${name}/settings/${key}`, { value: e.target.checked }))}
                    />
                  ) : (
                    <input
                      type="number"
                      aria-label={key}
                      defaultValue={value}
                      step={Number.isInteger(value) ? 1 : 0.01}
                      onBlur={(e) => {
                        const n = Number(e.target.value);
                        if (n !== value)
                          void run(async () => {
                            const r = await put<{ rebuild_queued?: boolean }>(`/api/p/${name}/settings/${key}`, {
                              value: Number.isInteger(value) ? Math.round(n) : n,
                            });
                            if (r.rebuild_queued) setNotice("Index option changed: a rebuild has been queued.");
                          });
                      }}
                    />
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>Model</h2>
      <p className="muted">
        Active: <code>{String(settings["model.id"] ?? "")}</code>
      </p>
      <form className="inline" onSubmit={submitSwitch}>
        <label>
          Model
          <select value={chosenModel} onChange={(e) => setChosenModel(e.target.value)} required>
            <option value="">choose</option>
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.id} · {m.dim}-d{m.available ? " (downloaded)" : ""}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" disabled={!chosenModel}>
          Switch model
        </button>
      </form>

      <h2>Evaluation against an ImageStore (v1) database</h2>
      <form className="inline" onSubmit={submitEval}>
        <label>
          v1 .db path in the container
          <input
            value={evalForm.db_path}
            onChange={(e) => setEvalForm({ ...evalForm, db_path: e.target.value })}
            placeholder="/import/imagestore.db"
            required
          />
        </label>
        <label>
          Random negative pairs
          <input
            type="number"
            min={100}
            value={evalForm.negatives}
            onChange={(e) => setEvalForm({ ...evalForm, negatives: Number(e.target.value) || 5000 })}
          />
        </label>
        <button type="submit">Run evaluation</button>
      </form>
      {reports.length > 0 && (
        <ul>
          {reports.map((r) => (
            <li key={r.file}>
              <a href={`/api/p/${name}/eval/reports/${r.file}`} target="_blank" rel="noreferrer">
                {r.file}
              </a>{" "}
              <span className="muted">({formatBytes(r.size)})</span>
            </li>
          ))}
        </ul>
      )}

      <h2>Trash</h2>
      {trash.length === 0 ? (
        <p className="muted">Nothing in the trash.</p>
      ) : (
        <>
          <div className="actions-row">
            <button type="button" onClick={() => setSelectedTrash(new Set(trash.map((t) => t.id)))}>
              Select all
            </button>
            <button type="button" disabled={selectedTrash.size === 0} onClick={() => restore([...selectedTrash])}>
              Restore selected ({selectedTrash.size})
            </button>
          </div>
          <div className="table-wrap">
            <table>
              <tbody>
                {trash.map((t) => (
                  <tr key={t.id}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`select ${String(t.before.relative_path)}`}
                        checked={selectedTrash.has(t.id)}
                        onChange={(e) => {
                          const next = new Set(selectedTrash);
                          if (e.target.checked) next.add(t.id);
                          else next.delete(t.id);
                          setSelectedTrash(next);
                        }}
                      />
                    </td>
                    <td>
                      <code>{String(t.before.relative_path)}</code>
                    </td>
                    <td className="muted">{formatWhen(t.at)}</td>
                    <td className="actions">
                      <button type="button" onClick={() => restore([t.id])}>
                        Restore
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <h2>Move an inbox into an archive</h2>
      <form className="inline" onSubmit={submitMove}>
        <label>
          Source (inbox)
          <select value={move.source_folder_id} onChange={(e) => setMove({ ...move, source_folder_id: e.target.value })} required>
            <option value="">choose</option>
            {inboxes.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Target (archive)
          <select value={move.target_folder_id} onChange={(e) => setMove({ ...move, target_folder_id: e.target.value })} required>
            <option value="">choose</option>
            {archives.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Directories (optional, one per line)
          <textarea value={move.directories} onChange={(e) => setMove({ ...move, directories: e.target.value })} rows={2} />
        </label>
        <button type="submit">Move</button>
      </form>

      <h2>Import an ImageStore (v1) database</h2>
      <form onSubmit={submitImport}>
        <label>
          Path of the v1 .db inside the container
          <input
            value={imp.db_path}
            onChange={(e) => setImp({ ...imp, db_path: e.target.value })}
            placeholder="/import/imagestore.db"
            required
          />
        </label>
        {imp.rows.map((row, i) => (
          <div className="inline" key={i}>
            <label>
              v1 Folder name
              <input
                value={row.name}
                onChange={(e) => setImp({ ...imp, rows: imp.rows.map((r, j) => (j === i ? { ...r, name: e.target.value } : r)) })}
              />
            </label>
            <label>
              Container path
              <input
                value={row.path}
                onChange={(e) => setImp({ ...imp, rows: imp.rows.map((r, j) => (j === i ? { ...r, path: e.target.value } : r)) })}
                placeholder="/media/images"
              />
            </label>
          </div>
        ))}
        <div className="actions-row">
          <button type="button" onClick={() => setImp({ ...imp, rows: [...imp.rows, { name: "", path: "" }] })}>
            Add row
          </button>
          <button type="submit" disabled={folders.length > 0}>
            Import
          </button>
          {folders.length > 0 && <span className="muted">Import needs a profile without Folders.</span>}
        </div>
      </form>

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
