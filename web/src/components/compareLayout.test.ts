import { describe, expect, it } from "vitest";
import { layers } from "./compareLayout";

describe("layers", () => {
  it("side by side shows both, not stacked", () => {
    expect(layers("side", 0, 0.5)).toEqual({ stacked: false, left: { shown: true, opacity: 1 }, right: { shown: true, opacity: 1 } });
  });
  it("overlay stacks the right image with the slider opacity", () => {
    expect(layers("overlay", 7, 0.3)).toEqual({ stacked: true, left: { shown: true, opacity: 1 }, right: { shown: true, opacity: 0.3 } });
  });
  it("blink alternates with the tick", () => {
    expect(layers("blink", 0, 0.5).left.shown).toBe(true);
    expect(layers("blink", 0, 0.5).right.shown).toBe(false);
    expect(layers("blink", 1, 0.5).left.shown).toBe(false);
    expect(layers("blink", 1, 0.5).right.shown).toBe(true);
    expect(layers("blink", 1, 0.5).stacked).toBe(true);
  });
});
