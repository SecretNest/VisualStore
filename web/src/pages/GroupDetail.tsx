import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, get, post, put } from "../api";
import Compare from "../components/Compare";
import FrameStrip from "../components/FrameStrip";
import Thumb from "../components/Thumb";
import { formatBytes } from "../format";
import type { Decision, Group, GroupDetail as Detail } from "../types";
import { applyKey, compareTarget, selectMember, type KeyState } from "./groupKeys";

export default function GroupDetail() {
  const { name = "", groupId = "" } = useParams();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [keys, setKeys] = useState<KeyState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [picked, setPicked] = useState<Record<string, number>>({});

  const load = useCallback(async () => {
    try {
      const d = await get<Detail>(`/api/p/${name}/groups/${groupId}`);
      setDetail(d);
      setKeys({
        members: d.group.members.map((m) => ({
          file_id: m.file_id,
          decision: m.decision,
        })),
        selected: 0,
        anchor: null,
        mode: "side",
        target: null,
      });
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404)
        navigate(`/p/${name}/groups`, { replace: true });
      else
        setError(
          err instanceof ApiError ? err.message : "Could not load the group.",
        );
    }
  }, [name, groupId, navigate]);

  useEffect(() => {
    void load();
  }, [load]);

  const goNext = useCallback(async () => {
    // Queue order, wrapping around, never a group whose decisions are being executed.
    try {
      const next = await get<{ id: string | null }>(`/api/p/${name}/groups/${groupId}/next`);
      navigate(next.id ? `/p/${name}/groups/${next.id}` : `/p/${name}/groups`);
    } catch {
      const open = await get<Group[]>(`/api/p/${name}/groups?status=open&limit=1`).catch(() => []);
      navigate(open[0] && open[0].id !== groupId ? `/p/${name}/groups/${open[0].id}` : `/p/${name}/groups`);
    }
  }, [name, groupId, navigate]);

  const confirm = useCallback(async () => {
    if (!keys || busy || detail?.group.status !== "open") return;
    setBusy(true);
    try {
      const decisions = Object.fromEntries(
        keys.members.map((m) => [m.file_id, m.decision]),
      );
      await post(`/api/p/${name}/groups/${groupId}/resolve`, { decisions });
      await goNext();
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Could not resolve the group.",
      );
    } finally {
      setBusy(false);
    }
  }, [keys, busy, name, groupId, goNext, detail]);

  const markUnrelated = useCallback(async () => {
    if (!keys || keys.anchor === null) return;
    const a = keys.members[keys.anchor].file_id;
    const b = keys.members[keys.selected].file_id;
    setBusy(true);
    try {
      await put(`/api/p/${name}/pairs/${a}/${b}/judgment`, { judgment: "unrelated" });
      // The judgment queues a regroup that replaces this group; wait for it rather than
      // showing the old members, then move on.
      const before = keys.members.map((m) => m.file_id).join(",");
      for (let i = 0; i < 40; i++) {
        await new Promise((r) => window.setTimeout(r, 500));
        try {
          const d = await get<Detail>(`/api/p/${name}/groups/${groupId}`);
          if (d.group.members.map((m) => m.file_id).join(",") !== before) break;
        } catch (err) {
          if (err instanceof ApiError && err.status === 404) break;
          throw err;
        }
      }
      await goNext();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not mark the pair.");
    } finally {
      setBusy(false);
    }
  }, [keys, name, groupId, goNext]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement)?.tagName;
      if (
        !keys ||
        !detail ||
        tag === "INPUT" ||
        tag === "SELECT" ||
        tag === "TEXTAREA"
      )
        return;
      const id = detail.group.members[keys.selected].file_id;
      const near = detail.neighbours[id]?.file_id;
      const nearIndex = near
        ? detail.group.members.findIndex((m) => m.file_id === near)
        : -1;
      const result = applyKey(keys, e.key, nearIndex === -1 ? null : nearIndex);
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
  }, [keys, detail, confirm, goNext, markUnrelated]);

  if (error && (!detail || !keys))
    return (
      <p className="error" role="alert">
        {error}
      </p>
    );
  if (!detail || !keys) return <p className="muted">Loading…</p>;

  const { group } = detail;
  const decided = keys.members.every((m) => m.decision !== "undecided");
  const selectedId = group.members[keys.selected].file_id;
  const nearestId = detail.neighbours[selectedId]?.file_id ?? null;
  const nearestFound = nearestId
    ? group.members.findIndex((m) => m.file_id === nearestId)
    : -1;
  const nearestIndex = nearestFound === -1 ? null : nearestFound;
  const targetIndex = compareTarget(keys, nearestIndex);
  const targetId =
    targetIndex === null ? null : group.members[targetIndex].file_id;
  const setDecision = (index: number, decision: Decision) =>
    setKeys({
      ...keys,
      selected: index,
      members: keys.members.map((m, i) =>
        i === index ? { ...m, decision } : m,
      ),
    });

  return (
    <section>
      <h1>
        Group of {group.member_count} files{" "}
        {group.has_identical && <span className="tag">byte-identical</span>}{" "}
        <span className="tag">{group.band.replace("_", " ")}</span>{" "}
        <span className="tag">{group.status.replace("_", " ")}</span>
      </h1>
      <p className="muted">
        Keys: 1–9 select · ↑/↓ move · k keep · d delete · a anchor · u mark
        anchor+selected unrelated · s/o/b side-by-side/overlay/blink · n/p other
        member · Space swap · Enter confirm · Esc skip
      </p>
      <div className="detail-columns">
        <ol className="members">
          {group.members.map((m, i) => {
            const decision = keys.members[i].decision;
            const classes = ["member", `member-${decision}`];
            if (i === keys.selected) classes.push("selected");
            if (i === keys.anchor) classes.push("anchor");
            return (
              <li
                key={m.file_id}
                className={classes.join(" ")}
                onClick={() => setKeys(selectMember(keys, i))}
              >
                <div>
                  <Thumb name={name} fileId={m.file_id} size={64} />
                  <span className="tag">{m.role}</span>{" "}
                  <strong>{m.folder_name}</strong>{" "}
                  <code>{m.relative_path}</code>
                  <span className="muted"> · {formatBytes(m.size)}</span>
                  {detail.neighbours[m.file_id] && (
                    <span className="muted">
                      {" "}
                      · nearest{" "}
                      {detail.neighbours[m.file_id]!.distance.toFixed(3)}
                    </span>
                  )}{" "}
                  <a
                    href={`/api/p/${name}/files/${m.file_id}/content`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    open original
                  </a>{" "}
                  <Link to={`/p/${name}/files/${m.file_id}`}>details</Link>
                </div>
                <div className="actions-row">
                  <button
                    type="button"
                    className={decision === "keep" ? "active" : ""}
                    onClick={() => setDecision(i, "keep")}
                  >
                    Keep
                  </button>
                  <button
                    type="button"
                    className={
                      decision === "delete" ? "active danger" : "danger"
                    }
                    onClick={() => setDecision(i, "delete")}
                  >
                    Delete
                  </button>
                  <button
                    type="button"
                    onClick={() => setKeys({ ...keys, anchor: i })}
                  >
                    Anchor
                  </button>
                </div>
              </li>
            );
          })}
        </ol>
        <div className="pane">
          <div className="actions-row">
            {(["side", "overlay", "blink"] as const).map((m) => (
              <button
                key={m}
                type="button"
                className={keys.mode === m ? "active" : ""}
                onClick={() => setKeys({ ...keys, mode: m })}
              >
                {m === "side"
                  ? "Side by side (s)"
                  : m === "overlay"
                    ? "Overlay (o)"
                    : "Blink (b)"}
              </button>
            ))}
            <button
              type="button"
              onClick={() =>
                setKeys(applyKey(keys, "n", nearestIndex) as KeyState)
              }
              disabled={group.members.length < 2}
            >
              Next (n)
            </button>
            <button
              type="button"
              onClick={() =>
                setKeys(applyKey(keys, "p", nearestIndex) as KeyState)
              }
              disabled={group.members.length < 2}
            >
              Previous (p)
            </button>
            <button
              type="button"
              onClick={() => setKeys(applyKey(keys, " ") as KeyState)}
              disabled={keys.target === null}
            >
              Swap (Space)
            </button>
          </div>
          <p className="muted">
            Comparing <code>{group.members[keys.selected].relative_path}</code>
            {targetId && targetIndex !== null && (
              <>
                {" "}
                with <code>{group.members[targetIndex].relative_path}</code>
                {keys.target === null ? " (nearest neighbour)" : ""}
              </>
            )}
          </p>
          <Compare
            name={name}
            leftId={selectedId}
            rightId={targetId}
            mode={keys.mode}
            leftFrame={picked[selectedId] ?? null}
            rightFrame={targetId ? (picked[targetId] ?? null) : null}
          />
          {[selectedId, targetId]
            .filter((id): id is string => id !== null)
            .map((id) => {
              const member = group.members.find((m) => m.file_id === id);
              if (!member || (member.media_type !== "animated" && member.media_type !== "video")) return null;
              return (
                <div key={id} className="strip-block">
                  <span className="muted">Frames of {member.relative_path}</span>
                  <FrameStrip name={name} fileId={id} picked={picked[id] ?? null} onPick={(index) => setPicked({ ...picked, [id]: index })} />
                </div>
              );
            })}
        </div>
      </div>
      <div className="actions-row">
        <button
          type="button"
          onClick={() => void confirm()}
          disabled={!decided || busy || group.status !== "open"}
        >
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
      <p className="muted">{detail.pairs.length} pairs in this group.</p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
