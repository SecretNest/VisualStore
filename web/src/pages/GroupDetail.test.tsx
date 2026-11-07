// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { GroupDetail as Detail, GroupMember } from "../types";
import GroupDetail from "./GroupDetail";

function member(id: string, name: string): GroupMember {
  return {
    file_id: id,
    decision: "undecided",
    directory: "",
    name,
    size: 10,
    sha1: "aa",
    state: "fingerprinted",
    folder_id: "f",
    folder_name: "Lib",
    folder_path: "/lib",
    role: "inbox",
    folder_priority: 0,
    relative_path: name,
    absolute_path: `/lib/${name}`,
    media_type: "image",
  };
}

function detail(groupId: string, ids: string[]): Detail {
  return {
    group: {
      id: groupId,
      member_count: ids.length,
      min_distance: 0,
      has_identical: true,
      band: "identical",
      status: "open",
      updated_at: "2026-11-07T00:00:00Z",
      members: ids.map((id) => member(id, `${id}.jpg`)),
    },
    pairs: [{ file_a: ids[0], file_b: ids[1], distance: 0, source: "sha1", judgment: "none", judged_at: null, judgment_origin: null, model_id: null }],
    neighbours: {},
  };
}

/** A fetch stub: GET of a group answers from `groups`; everything else is recorded. */
function stubFetch(groups: Record<string, Detail>, options: { delayMs?: Record<string, number> } = {}) {
  const calls: { method: string; url: string }[] = [];
  const resolvers: Record<string, () => void> = {};
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      calls.push({ method, url });
      const groupMatch = url.match(/\/groups\/([^/?]+)$/);
      if (method === "GET" && groupMatch) {
        const id = groupMatch[1];
        if (options.delayMs?.[id]) await new Promise<void>((r) => (resolvers[id] = r));
        return new Response(JSON.stringify(groups[id]), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url.endsWith("/next")) return new Response(JSON.stringify({ id: null }), { status: 200 });
      if (url.includes("/frames")) return new Response(JSON.stringify({ media_type: "image", frames: [] }), { status: 200 });
      return new Response(JSON.stringify({}), { status: 200 });
    }),
  );
  return { calls, resolvers };
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/p/:name/groups/:groupId" element={<GroupDetail />} />
        <Route path="/p/:name/groups" element={<p>list</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("GroupDetail keyboard handling", () => {
  it("does not resolve the group when Enter is pressed on a focused button", async () => {
    const { calls } = stubFetch({ g1: detail("g1", ["a", "b"]) });
    renderAt("/p/p/groups/g1");
    await screen.findByText("Group of 2 files");
    // decide every member so Enter would otherwise confirm
    fireEvent.keyDown(window, { key: "k" });
    fireEvent.keyDown(window, { key: "ArrowDown" });
    fireEvent.keyDown(window, { key: "d" });
    const skip = screen.getByRole("button", { name: "Skip (Esc)" });
    skip.focus();
    fireEvent.keyDown(skip, { key: "Enter" });
    await act(async () => undefined);
    expect(calls.filter((c) => c.url.endsWith("/resolve"))).toHaveLength(0);
  });

  it("ignores browser chords such as Ctrl+D", async () => {
    stubFetch({ g1: detail("g1", ["a", "b"]) });
    renderAt("/p/p/groups/g1");
    await screen.findByText("Group of 2 files");
    fireEvent.keyDown(window, { key: "d", ctrlKey: true });
    const deleteButtons = screen.getAllByRole("button", { name: "Delete" });
    expect(deleteButtons.some((b) => b.className.includes("active"))).toBe(false);
    fireEvent.keyDown(window, { key: "d" });
    expect(screen.getAllByRole("button", { name: "Delete" })[0].className).toContain("active");
  });

  it("confirms with Enter once every member is decided", async () => {
    const { calls } = stubFetch({ g1: detail("g1", ["a", "b"]) });
    renderAt("/p/p/groups/g1");
    await screen.findByText("Group of 2 files");
    fireEvent.keyDown(window, { key: "k" });
    fireEvent.keyDown(window, { key: "ArrowDown" });
    fireEvent.keyDown(window, { key: "d" });
    fireEvent.keyDown(window, { key: "Enter" });
    await waitFor(() => expect(calls.filter((c) => c.method === "POST" && c.url.endsWith("/groups/g1/resolve"))).toHaveLength(1));
  });
});

describe("GroupDetail state between groups", () => {
  it("clears the previous group while the next one loads and drops a late response", async () => {
    const groups = { g1: detail("g1", ["a", "b"]), g2: detail("g2", ["c", "d"]) };
    const { resolvers } = stubFetch(groups, { delayMs: { g2: 1 } });
    render(
      <MemoryRouter initialEntries={["/p/p/groups/g1"]}>
        <Routes>
          <Route
            path="/p/:name/groups/:groupId"
            element={
              <>
                <GroupDetail />
                <Link to="/p/p/groups/g2">go to g2</Link>
              </>
            }
          />
        </Routes>
      </MemoryRouter>,
    );
    await screen.findAllByText("a.jpg");
    fireEvent.click(screen.getByRole("link", { name: "go to g2" }));
    // g2 is loading: the old members are gone, nothing is actionable
    await waitFor(() => expect(screen.queryAllByText("a.jpg")).toHaveLength(0));
    expect(screen.getByText("Loading…")).toBeTruthy();
    await act(async () => resolvers.g2?.());
    await screen.findAllByText("c.jpg");
  });

  it("drops the answer of an earlier group that arrives after a newer one", async () => {
    const groups = { g1: detail("g1", ["a", "b"]), g2: detail("g2", ["c", "d"]) };
    const { resolvers } = stubFetch(groups, { delayMs: { g1: 1 } });
    render(
      <MemoryRouter initialEntries={["/p/p/groups/g1"]}>
        <Routes>
          <Route
            path="/p/:name/groups/:groupId"
            element={
              <>
                <GroupDetail />
                <Link to="/p/p/groups/g2">go to g2</Link>
              </>
            }
          />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole("link", { name: "go to g2" }));
    await screen.findAllByText("c.jpg");
    await act(async () => resolvers.g1?.()); // the slow g1 answer lands after g2 was shown
    expect(screen.queryAllByText("a.jpg")).toHaveLength(0);
    expect(screen.getAllByText("c.jpg").length).toBeGreaterThan(0);
  });
});
