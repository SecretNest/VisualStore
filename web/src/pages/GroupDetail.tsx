import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ApiError, get, post, put } from "../api";
import Thumb from "../components/Thumb";
import { formatBytes } from "../format";
import type { Decision, Group, GroupDetail as Detail } from "../types";
import { applyKey, type KeyState } from "./groupKeys";

export default function GroupDetail() {
  const { name = "", groupId = "" } = useParams();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [keys, setKeys] = useState<KeyState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await get<Detail>(`/api/p/${name}/groups/${groupId}`);
      setDetail(d);
      setKeys({
        members: d.group.members.map((m) => ({ file_id: m.file_id, decision: m.decision })),
        selected: 0,
        anchor: null,
        mode: "side",
        target: null,
      });
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) navigate(`/p/${name}/groups`, { replace: true });
      else setError(err instanceof ApiError ? err.message : "Could not load the group.");
    }
  }, [name, groupId, navigate]);

  useEffect(() => {
    void load();
  }, [load]);

  const goNext = useCallback(async () => {
    const next = await get<Group[]>(`/api/p/${name}/groups?status=open&limit=2`);
    const target = next.find((g) => g.id !== groupId);
    navigate(target ? `/p/${name}/groups/${target.id}` : `/p/${name}/groups`);
  }, [name, groupId, navigate]);

  const confirm = useCallback(async () => {
    if (!keys || busy) return;
    setBusy(true);
    try {
      const decisions = Object.fromEntries(keys.members.map((m) => [m.file_id, m.decision]));
      await post(`/api/p/${name}/groups/${groupId}/resolve`, { decisions });
      await goNext();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not resolve the group.");
    } finally {
      setBusy(false);
    }
  }, [keys, busy, name, groupId, goNext]);

  const markUnrelated = useCallback(async () => {
    if (!keys || keys.anchor === null) return;
    const a = keys.members[keys.anchor].file_id;
    const b = keys.members[keys.selected].file_id;
    try {
      await put(`/api/p/${name}/pairs/${a}/${b}/judgment`, { judgment: "unrelated" });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not mark the pair.");
    }
  }, [keys, name, load]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (!keys || (e.target as HTMLElement)?.tagName === "INPUT") return;
      const result = applyKey(keys, e.key);
      if ("action" in result) {
        e.preventDefault();
        if (result.action === "confirm") void confirm();
        else if (result.action === "skip") void goNext();
        else void markUnrelated();
      } else if (result !== keys) {
        e.preventDefault();
        setKeys(result);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [keys, confirm, goNext, markUnrelated]);

  if (error)
    return (
      <p className="error" role="alert">
        {error}
      </p>
    );
  if (!detail || !keys) return <p className="muted">Loading…</p>;

  const { group } = detail;
  const decided = keys.members.every((m) => m.decision !== "undecided");
  const setDecision = (index: number, decision: Decision) =>
    setKeys({
      ...keys,
      selected: index,
      members: keys.members.map((m, i) => (i === index ? { ...m, decision } : m)),
    });

  return (
    <section>
      <h1>
        Group of {group.member_count} files {group.has_identical && <span className="tag">byte-identical</span>}{" "}
        <span className="tag">{group.band.replace("_", " ")}</span>{" "}
        <span className="tag">{group.status.replace("_", " ")}</span>
      </h1>
      <p className="muted">
        Keys: 1–9 select · ↑/↓ move · k keep · d delete · a anchor · u mark anchor+selected unrelated · Enter
        confirm · Esc skip
      </p>
      <ol className="members">
        {group.members.map((m, i) => {
          const decision = keys.members[i].decision;
          const classes = ["member", `member-${decision}`];
          if (i === keys.selected) classes.push("selected");
          if (i === keys.anchor) classes.push("anchor");
          return (
            <li key={m.file_id} className={classes.join(" ")} onClick={() => setKeys({ ...keys, selected: i })}>
              <div>
                <Thumb name={name} fileId={m.file_id} size={64} />
                <span className="tag">{m.role}</span> <strong>{m.folder_name}</strong> <code>{m.relative_path}</code>
                <span className="muted"> · {formatBytes(m.size)}</span>{" "}
                <a href={`/api/p/${name}/files/${m.file_id}/content`} target="_blank" rel="noreferrer">
                  open original
                </a>
              </div>
              <div className="actions-row">
                <button type="button" className={decision === "keep" ? "active" : ""} onClick={() => setDecision(i, "keep")}>
                  Keep
                </button>
                <button
                  type="button"
                  className={decision === "delete" ? "active danger" : "danger"}
                  onClick={() => setDecision(i, "delete")}
                >
                  Delete
                </button>
                <button type="button" onClick={() => setKeys({ ...keys, anchor: i })}>
                  Anchor
                </button>
              </div>
            </li>
          );
        })}
      </ol>
      <div className="actions-row">
        <button type="button" onClick={() => void confirm()} disabled={!decided || busy || group.status !== "open"}>
          Confirm (Enter)
        </button>
        <button type="button" onClick={() => void goNext()}>
          Skip (Esc)
        </button>
        <button
          type="button"
          onClick={() => void markUnrelated()}
          disabled={keys.anchor === null || keys.anchor === keys.selected}
        >
          Mark unrelated (u)
        </button>
        <button
          type="button"
          onClick={() =>
            void (async () => {
              await post(`/api/p/${name}/groups/${groupId}/dismiss`);
              await goNext();
            })()
          }
          disabled={group.status !== "open"}
        >
          Dismiss group
        </button>
      </div>
      <p className="muted">{detail.pairs.length} pairs in this group. Image comparison arrives in milestone 4.</p>
    </section>
  );
}
