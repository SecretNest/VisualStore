import { useEffect, useState } from "react";
import { get } from "../api";
import type { FramesResponse } from "../types";
import { formatFrameTime } from "./frameTime";

export default function FrameStrip({ name, fileId, picked, onPick }: { name: string; fileId: string; picked: number | null; onPick: (index: number) => void }) {
  const [frames, setFrames] = useState<FramesResponse | null>(null);
  useEffect(() => {
    let cancelled = false;
    setFrames(null);
    void get<FramesResponse>(`/api/p/${name}/files/${fileId}/frames`)
      .then((f) => {
        if (!cancelled) setFrames(f);
      })
      .catch(() => {
        if (!cancelled) setFrames({ media_type: "other", frames: [] });
      });
    return () => {
      cancelled = true;
    };
  }, [name, fileId]);
  if (!frames || frames.frames.length < 2) return null;
  return (
    <div className="frame-strip" role="list" aria-label="Frames">
      {frames.frames.map((f) => (
        <button
          key={f.index}
          type="button"
          role="listitem"
          className={picked === f.index ? "active" : ""}
          onClick={() => onPick(f.index)}
          title={`Frame ${f.index} at ${formatFrameTime(f.time_ms)}`}
        >
          <img src={`/api/p/${name}/files/${fileId}/frame/${f.index}`} alt="" width={64} height={64} />
          <span>{formatFrameTime(f.time_ms)}</span>
        </button>
      ))}
    </div>
  );
}
