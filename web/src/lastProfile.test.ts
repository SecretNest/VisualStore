import { describe, expect, it } from "vitest";
import { getLastProfile, setLastProfile } from "./lastProfile";

function memoryStorage(): Storage {
  const map = new Map<string, string>();
  return {
    get length() {
      return map.size;
    },
    clear: () => map.clear(),
    getItem: (k) => map.get(k) ?? null,
    key: (i) => [...map.keys()][i] ?? null,
    removeItem: (k) => void map.delete(k),
    setItem: (k, v) => void map.set(k, v),
  };
}

describe("lastProfile", () => {
  it("is null until set, then round-trips, then clears", () => {
    const storage = memoryStorage();
    expect(getLastProfile(storage)).toBeNull();
    setLastProfile("cg", storage);
    expect(getLastProfile(storage)).toBe("cg");
    setLastProfile(null, storage);
    expect(getLastProfile(storage)).toBeNull();
  });
});
