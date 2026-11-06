import { describe, expect, it } from "vitest";
import { ApiError, Unauthorized, api } from "./api";

function fakeFetch(status: number, body: unknown): typeof fetch {
  return (async () =>
    new Response(body === undefined ? null : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    })) as unknown as typeof fetch;
}

describe("api", () => {
  it("returns parsed JSON on success", async () => {
    const result = await api<{ ok: boolean }>("/api/x", {}, fakeFetch(200, { ok: true }));
    expect(result).toEqual({ ok: true });
  });

  it("throws Unauthorized on 401", async () => {
    await expect(
      api("/api/x", {}, fakeFetch(401, { detail: "Not logged in." })),
    ).rejects.toBeInstanceOf(Unauthorized);
  });

  it("throws ApiError carrying the server detail", async () => {
    const promise = api("/api/x", {}, fakeFetch(400, { detail: "Bad name." }));
    await expect(promise).rejects.toBeInstanceOf(ApiError);
    await expect(promise).rejects.toMatchObject({ status: 400, message: "Bad name." });
  });

  it("sends JSON bodies with credentials", async () => {
    let seen: RequestInit | undefined;
    const spy = (async (_: RequestInfo | URL, init?: RequestInit) => {
      seen = init;
      return new Response("{}", { status: 200 });
    }) as unknown as typeof fetch;
    await api("/api/x", { method: "POST", body: JSON.stringify({ a: 1 }) }, spy);
    expect(seen?.credentials).toBe("same-origin");
    expect((seen?.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });
});
