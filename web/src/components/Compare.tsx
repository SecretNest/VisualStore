import { useEffect, useState } from "react";
import type { CompareMode } from "../pages/groupKeys";
import { layers } from "./compareLayout";

function PreviewImage({ name, fileId, opacity, onFallback }: { name: string; fileId: string; opacity: number; onFallback: (id: string) => void }) {
  const [source, setSource] = useState<"preview" | "thumbnail" | "none">("preview");
  useEffect(() => setSource("preview"), [fileId]);
  if (source === "none") return <div className="preview preview-missing">No image available</div>;
  const path = source === "preview" ? "preview" : "thumbnail";
  return (
    <img
      className={`preview preview-${source}`}
      alt=""
      style={{ opacity }}
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

export default function Compare({ name, leftId, rightId, mode }: { name: string; leftId: string; rightId: string | null; mode: CompareMode }) {
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
  const sides = [leftId, rightId].filter((id): id is string => id !== null);
  return (
    <div className={`compare compare-${mode}${l.stacked ? " compare-stacked" : ""}`} data-tick={tick}>
      <div className="compare-images">
        {l.left.shown && <PreviewImage name={name} fileId={leftId} opacity={l.left.opacity} onFallback={note} />}
        {rightId && l.right.shown && <PreviewImage name={name} fileId={rightId} opacity={l.right.opacity} onFallback={note} />}
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
