import { describe, expect, it } from "vitest";
import { applyKey, type KeyState } from "./groupKeys";

const base: KeyState = {
  members: [
    { file_id: "a", decision: "keep" },
    { file_id: "b", decision: "undecided" },
    { file_id: "c", decision: "delete" },
  ],
  selected: 0,
  anchor: null,
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
