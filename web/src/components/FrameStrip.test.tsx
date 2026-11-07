// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import FrameStrip from "./FrameStrip";

function respond(body: unknown) {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

describe("FrameStrip", () => {
  it("lists the frames as a list with their times and reports the clicked index", async () => {
    respond({ media_type: "video", frames: [{ index: 0, time_ms: 0 }, { index: 1, time_ms: 1500 }] });
    const picked: number[] = [];
    render(<FrameStrip name="p" fileId="vid" picked={null} onPick={(i) => picked.push(i)} />);
    const items = await waitFor(() => {
      const found = screen.getAllByRole("listitem");
      expect(found).toHaveLength(2);
      return found;
    });
    expect(screen.getByRole("list", { name: "Frames" })).toBeTruthy();
    expect(items[1].textContent).toContain("1.5 s");
    fireEvent.click(items[1].querySelector("button")!);
    expect(picked).toEqual([1]);
    expect(items[1].querySelector("img")?.getAttribute("src")).toBe("/api/p/p/files/vid/frame/1");
  });

  it("renders nothing for a single-frame file", async () => {
    const fetchMock = respond({ media_type: "image", frames: [{ index: 0, time_ms: 0 }] });
    const { container } = render(<FrameStrip name="p" fileId="still" picked={null} onPick={() => undefined} />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 20));
    expect(container.querySelector("ul")).toBeNull();
  });
});
