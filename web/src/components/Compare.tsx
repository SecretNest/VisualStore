import { useCallback, useEffect, useState } from "react";
import type { CompareMode } from "../pages/groupKeys";
import { layers } from "./compareLayout";

function PreviewImage({ name, fileId, frame, opacity, shown, onFallback, onReset }: { name: string; fileId: string; frame: number | null; opacity: number; shown: boolean; onFallback: (id: string) => void; onReset: (id: string) => void }) {
  const [source, setSource] = useState<"preview" | "thumbnail" | "none">("preview");
  useEffect(() => {
    setSource("preview");
    onReset(fileId);
  }, [fileId, frame, onReset]);
  const style = { opacity, visibility: shown ? "visible" : "hidden" } as const;
  if (source === "none")
    return (
      <div className="preview preview-missing" style={style}>
        No image available
      </div>
    );
  const main = frame === null ? "preview" : `frame/${frame}`;
  const path = source === "preview" ? main : "thumbnail";
  return (
    <img
      className={`preview preview-${source}`}
      alt=""
      style={style}
      src={`/api/p/${name}/files/${fileId}/${path}`}
      onError={() => {
        if (source === "preview") {
          onFallback(fileId);
          setSource("thumbnail");
        } else setSource("none");
      }}
    />
  );
}

export default function Compare({
  name,
  leftId,
  rightId,
  mode,
  leftFrame = null,
  rightFrame = null,
}: {
  name: string;
  leftId: string;
  rightId: string | null;
  mode: CompareMode;
  leftFrame?: number | null;
  rightFrame?: number | null;
}) {
  const [opacity, setOpacity] = useState(0.5);
  const [tick, setTick] = useState(0);
  const [fallbacks, setFallbacks] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (mode !== "blink") return;
    const timer = window.setInterval(() => setTick((t) => t + 1), 500);
    return () => window.clearInterval(timer);
  }, [mode]);
  const l = layers(mode, tick, opacity);
  const note = (id: string) => setFallbacks((prev) => (prev.has(id) ? prev : new Set(prev).add(id)));
  const reset = useCallback((id: string) => setFallbacks((prev) => {
    if (!prev.has(id)) return prev;
    const next = new Set(prev);
    next.delete(id);
    return next;
  }), []);
  const sides = [leftId, rightId].filter((id): id is string => id !== null);
  return (
    <div className={`compare compare-${mode}${l.stacked ? " compare-stacked" : ""}`} data-tick={tick}>
      <div className="compare-images">
        <PreviewImage name={name} fileId={leftId} frame={leftFrame} opacity={l.left.opacity} shown={l.left.shown} onFallback={note} onReset={reset} />
        {rightId && (
          <PreviewImage name={name} fileId={rightId} frame={rightFrame} opacity={l.right.opacity} shown={l.right.shown} onFallback={note} onReset={reset} />
        )}
      </div>
      {mode === "overlay" && rightId && (
        <label className="inline-label">
          Overlay opacity
          <input type="range" min={0} max={1} step={0.05} value={opacity} onChange={(e) => setOpacity(Number(e.target.value))} />
        </label>
      )}
      {!rightId && <p className="muted">No other member to compare with.</p>}
      {sides.some((id) => fallbacks.has(id)) && (
        <p className="muted" role="note">
          The original of {sides.filter((id) => fallbacks.has(id)).length === 2 ? "both files" : "one file"} is not available; showing the thumbnail instead.
        </p>
      )}
    </div>
  );
}
