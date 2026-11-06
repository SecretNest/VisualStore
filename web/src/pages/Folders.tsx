import { FormEvent, useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { ApiError, del, get, patch, post, put } from "../api";
import type { Extension, Folder } from "../types";

const SCOPES: Folder["compare_scope"][] = ["all", "other_directories", "other_folders"];
const MEDIA: Extension["media_type"][] = ["image", "animated", "video", "other"];

type FolderForm = {
  name: string;
  path: string;
  role: Folder["role"];
  compare_scope: Folder["compare_scope"];
  priority: number;
};

const EMPTY_FORM: FolderForm = { name: "", path: "", role: "inbox", compare_scope: "all", priority: 0 };

export default function Folders() {
  const { name = "" } = useParams();
  const [folders, setFolders] = useState<Folder[]>([]);
  const [extensions, setExtensions] = useState<Extension[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [form, setForm] = useState<FolderForm>(EMPTY_FORM);
  const [ignoredInput, setIgnoredInput] = useState<Record<string, string>>({});
  const [extForm, setExtForm] = useState<{ extension: string; media_type: Extension["media_type"] }>({
    extension: "",
    media_type: "image",
  });

  const reload = useCallback(async () => {
    try {
      setFolders(await get<Folder[]>(`/api/p/${name}/folders`));
      setExtensions(await get<Extension[]>(`/api/p/${name}/extensions`));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load Folders.");
    }
  }, [name]);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function run(action: () => Promise<unknown>, done?: string) {
    setError(null);
    setNotice(null);
    try {
      await action();
      await reload();
      if (done) setNotice(done);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Request failed.");
    }
  }

  function create(e: FormEvent) {
    e.preventDefault();
    void run(async () => {
      await post(`/api/p/${name}/folders`, form);
      setForm(EMPTY_FORM);
    }, "Folder created.");
  }

  const patchFolder = (f: Folder, body: Record<string, unknown>) =>
    void run(() => patch(`/api/p/${name}/folders/${f.id}`, body));

  return (
    <section>
      <h1>Folders</h1>
      {folders.length === 0 && (
        <p className="muted">
          No Folders yet. A Folder is one library root inside the container, for example /media/images.
        </p>
      )}
      {folders.map((f) => (
        <div className="card" key={f.id}>
          <h2>
            {f.name} <code>{f.path}</code>
          </h2>
          <p className="muted">
            {f.role} · compare with {f.compare_scope.replace(/_/g, " ")} · priority {f.priority} ·{" "}
            {f.enabled ? "enabled" : "disabled"}
          </p>
          <div className="actions-row">
            <button
              type="button"
              onClick={() => void run(() => post(`/api/p/${name}/folders/${f.id}/scan`), "Scan queued.")}
              disabled={!f.enabled}
            >
              Scan now
            </button>
            <select aria-label={`Role of ${f.name}`} value={f.role} onChange={(e) => patchFolder(f, { role: e.target.value })}>
              <option value="inbox">inbox</option>
              <option value="archive">archive</option>
            </select>
            <select
              aria-label={`Compare scope of ${f.name}`}
              value={f.compare_scope}
              onChange={(e) => patchFolder(f, { compare_scope: e.target.value })}
            >
              {SCOPES.map((s) => (
                <option key={s} value={s}>
                  {s.replace(/_/g, " ")}
                </option>
              ))}
            </select>
            <label className="inline-label">
              Priority
              <input
                type="number"
                aria-label={`Priority of ${f.name}`}
                defaultValue={f.priority}
                style={{ width: "4rem" }}
                onBlur={(e) => {
                  const n = Number(e.target.value);
                  if (n !== f.priority) patchFolder(f, { priority: n });
                }}
              />
            </label>
            <label className="inline-label">
              <input type="checkbox" checked={f.enabled} onChange={(e) => patchFolder(f, { enabled: e.target.checked })} />{" "}
              Enabled
            </label>
            <button
              type="button"
              className="danger"
              onClick={() => {
                if (window.confirm(`Remove Folder ${f.name} from this profile? Files on disk are not touched.`))
                  void run(() => del(`/api/p/${name}/folders/${f.id}`), "Folder removed.");
              }}
            >
              Remove
            </button>
          </div>
          <h3>Ignored directories</h3>
          <ul>
            {f.ignored.map((i) => (
              <li key={i.id}>
                <code>{i.directory}</code> {i.recursive ? "(and subdirectories)" : "(only this directory)"}{" "}
                <button
                  type="button"
                  className="link"
                  onClick={() => void run(() => del(`/api/p/${name}/folders/${f.id}/ignored/${i.id}`))}
                >
                  remove
                </button>
              </li>
            ))}
          </ul>
          <form
            className="inline"
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                await post(`/api/p/${name}/folders/${f.id}/ignored`, {
                  directory: ignoredInput[f.id] ?? "",
                  recursive: true,
                });
                setIgnoredInput((prev) => ({ ...prev, [f.id]: "" }));
              });
            }}
          >
            <label>
              Ignore directory (relative)
              <input
                value={ignoredInput[f.id] ?? ""}
                onChange={(e) => setIgnoredInput((prev) => ({ ...prev, [f.id]: e.target.value }))}
                placeholder="Thumbs"
                required
              />
            </label>
            <button type="submit">Add</button>
          </form>
        </div>
      ))}

      <h2>New Folder</h2>
      <form className="inline" onSubmit={create}>
        <label>
          Name
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
        </label>
        <label>
          Container path
          <input
            value={form.path}
            onChange={(e) => setForm({ ...form, path: e.target.value })}
            placeholder="/media/images"
            required
          />
        </label>
        <label>
          Role
          <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Folder["role"] })}>
            <option value="inbox">inbox (new files)</option>
            <option value="archive">archive (storage)</option>
          </select>
        </label>
        <label>
          Compare with
          <select
            value={form.compare_scope}
            onChange={(e) => setForm({ ...form, compare_scope: e.target.value as Folder["compare_scope"] })}
          >
            {SCOPES.map((s) => (
              <option key={s} value={s}>
                {s.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </label>
        <button type="submit">Create Folder</button>
      </form>

      <h2>Extensions</h2>
      <table>
        <thead>
          <tr>
            <th>Extension</th>
            <th>Media type</th>
            <th>Enabled</th>
          </tr>
        </thead>
        <tbody>
          {extensions.map((x) => (
            <tr key={x.id}>
              <td>
                <code>{x.extension}</code>
              </td>
              <td>{x.media_type}</td>
              <td>
                <input
                  type="checkbox"
                  aria-label={`Enable ${x.extension}`}
                  checked={x.enabled}
                  onChange={(e) =>
                    void run(() => patch(`/api/p/${name}/extensions/${x.id}`, { enabled: e.target.checked }))
                  }
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <form
        className="inline"
        onSubmit={(e) => {
          e.preventDefault();
          void run(async () => {
            await put(`/api/p/${name}/extensions`, extForm);
            setExtForm({ extension: "", media_type: "image" });
          });
        }}
      >
        <label>
          Extension
          <input
            value={extForm.extension}
            onChange={(e) => setExtForm({ ...extForm, extension: e.target.value })}
            placeholder="jxl"
            required
          />
        </label>
        <label>
          Media type
          <select
            value={extForm.media_type}
            onChange={(e) => setExtForm({ ...extForm, media_type: e.target.value as Extension["media_type"] })}
          >
            {MEDIA.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
        <button type="submit">Add extension</button>
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
