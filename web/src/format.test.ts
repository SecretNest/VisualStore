import { describe, expect, it } from "vitest";
import { formatBytes, formatWhen } from "./format";

describe("format", () => {
  it("formats byte counts", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 MB");
  });
  it("formats missing times as never", () => {
    expect(formatWhen(null)).toBe("never");
    expect(formatWhen("2026-11-06T10:00:00+00:00")).not.toBe("never");
  });
});
