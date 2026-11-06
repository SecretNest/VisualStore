import { describe, expect, it } from "vitest";
import { applyKey, compareTarget, selectMember, type KeyState } from "./groupKeys";

const base: KeyState = {
  members: [
    { file_id: "a", decision: "keep" },
    { file_id: "b", decision: "undecided" },
    { file_id: "c", decision: "delete" },
  ],
  selected: 0,
  anchor: null,
  mode: "side",
  target: null,
};

describe("applyKey", () => {
  it("selects members with digits and arrows", () => {
    expect((applyKey(base, "2") as KeyState).selected).toBe(1);
    expect((applyKey(base, "9") as KeyState).selected).toBe(0); // out of range: unchanged
    expect((applyKey(base, "ArrowDown") as KeyState).selected).toBe(1);
    expect((applyKey({ ...base, selected: 2 }, "ArrowDown") as KeyState).selected).toBe(2);
    expect((applyKey(base, "ArrowUp") as KeyState).selected).toBe(0);
  });

  it("k and d set the selected decision", () => {
    const s = applyKey({ ...base, selected: 1 }, "k") as KeyState;
    expect(s.members[1].decision).toBe("keep");
    expect((applyKey(s, "d") as KeyState).members[1].decision).toBe("delete");
  });

  it("Enter confirms only when every member is decided", () => {
    expect(applyKey(base, "Enter")).toEqual(base);
    const decided = applyKey({ ...base, selected: 1 }, "d") as KeyState;
    expect(applyKey(decided, "Enter")).toEqual({ action: "confirm" });
  });

  it("Escape skips; a sets the anchor; u asks for unrelated only across two members", () => {
    expect(applyKey(base, "Escape")).toEqual({ action: "skip" });
    expect(applyKey(base, "u")).toEqual(base);
    const anchored = applyKey(base, "a") as KeyState;
    expect(anchored.anchor).toBe(0);
    expect(applyKey(anchored, "u")).toEqual(anchored);
    expect(applyKey({ ...anchored, selected: 2 }, "u")).toEqual({ action: "unrelated" });
  });

  it("ignores other keys", () => {
    expect(applyKey(base, "x")).toEqual(base);
  });
});

describe("comparison keys", () => {
  const withMode = { ...base, mode: "side" as const, target: null };

  it("s, o and b switch the mode", () => {
    expect((applyKey(withMode, "o") as KeyState).mode).toBe("overlay");
    expect((applyKey(withMode, "b") as KeyState).mode).toBe("blink");
    expect((applyKey({ ...withMode, mode: "blink" }, "s") as KeyState).mode).toBe("side");
  });

  it("n and p cycle the comparison target over the other members, starting from the nearest", () => {
    const n1 = applyKey(withMode, "n", 2) as KeyState; // nearest is index 2 → next other is index 1
    expect(n1.target).toBe(1);
    const n2 = applyKey(n1, "n", 2) as KeyState;
    expect(n2.target).toBe(2);
    const p = applyKey(withMode, "p", 1) as KeyState; // previous of 1, skipping selected 0 → 2
    expect(p.target).toBe(2);
    expect((applyKey({ ...withMode, members: [base.members[0]] }, "n", null) as KeyState).target).toBeNull();
  });

  it("selecting a member resets the target; Space swaps selected and target", () => {
    const targeted = { ...withMode, target: 2 };
    expect((applyKey(targeted, "2") as KeyState).target).toBeNull();
    expect((applyKey(targeted, "ArrowDown") as KeyState).target).toBeNull();
    const swapped = applyKey(targeted, " ") as KeyState;
    expect(swapped.selected).toBe(2);
    expect(swapped.target).toBe(0);
    expect(applyKey(withMode, " ")).toEqual(withMode);
  });

  it("compareTarget falls back to the nearest neighbour", () => {
    expect(compareTarget(withMode, 1)).toBe(1);
    expect(compareTarget({ ...withMode, target: 2 }, 1)).toBe(2);
    expect(compareTarget(withMode, null)).toBeNull();
  });
});

describe("selectMember", () => {
  it("selects and resets the comparison target, like the digit keys", () => {
    const state: KeyState = { ...base, mode: "side", target: 2 };
    expect(selectMember(state, 1)).toEqual({ ...state, selected: 1, target: null });
    expect(selectMember(state, 9)).toEqual(state); // out of range: unchanged
  });
});
