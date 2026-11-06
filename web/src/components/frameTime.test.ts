import { describe, expect, it } from "vitest";
import { formatFrameTime } from "./frameTime";

describe("formatFrameTime", () => {
  it("shows tenths of seconds and minutes past sixty seconds", () => {
    expect(formatFrameTime(0)).toBe("0.0 s");
    expect(formatFrameTime(1500)).toBe("1.5 s");
    expect(formatFrameTime(18750)).toBe("18.8 s");
    expect(formatFrameTime(65_040)).toBe("1:05.0");
    expect(formatFrameTime(3_600_000)).toBe("60:00.0");
  });
});
