import type { CompareMode } from "../pages/groupKeys";

export interface Layer {
  shown: boolean;
  opacity: number;
}

export interface Layers {
  stacked: boolean;
  left: Layer;
  right: Layer;
}

export function layers(mode: CompareMode, tick: number, opacity: number): Layers {
  if (mode === "side") return { stacked: false, left: { shown: true, opacity: 1 }, right: { shown: true, opacity: 1 } };
  if (mode === "overlay") return { stacked: true, left: { shown: true, opacity: 1 }, right: { shown: true, opacity } };
  const even = tick % 2 === 0;
  return { stacked: true, left: { shown: even, opacity: 1 }, right: { shown: !even, opacity: 1 } };
}
