// @vitest-environment jsdom
import { act, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Compare from "./Compare";

function images(container: HTMLElement): HTMLImageElement[] {
  return Array.from(container.querySelectorAll("img.preview"));
}

describe("Compare", () => {
  it("falls back to the thumbnail and says so when a preview cannot load", async () => {
    const { container } = render(<Compare name="p" leftId="aaa" rightId="bbb" mode="side" />);
    const [left, right] = images(container);
    expect(left.src).toContain("/api/p/p/files/aaa/preview");
    expect(right.src).toContain("/api/p/p/files/bbb/preview");
    await act(async () => {
      left.dispatchEvent(new Event("error"));
    });
    expect(images(container)[0].src).toContain("/api/p/p/files/aaa/thumbnail");
    expect(screen.getByRole("note").textContent).toContain("one file");
    await act(async () => {
      images(container)[1].dispatchEvent(new Event("error"));
    });
    expect(screen.getByRole("note").textContent).toContain("both files");
  });

  it("shows a placeholder once the thumbnail is missing as well", async () => {
    const { container } = render(<Compare name="p" leftId="aaa" rightId={null} mode="side" />);
    await act(async () => {
      images(container)[0].dispatchEvent(new Event("error"));
    });
    await act(async () => {
      images(container)[0].dispatchEvent(new Event("error"));
    });
    expect(images(container)).toHaveLength(0);
    expect(screen.getByText("No image available")).toBeTruthy();
  });

  it("uses the frame image when a frame is picked", () => {
    const { container } = render(<Compare name="p" leftId="aaa" rightId="bbb" mode="side" leftFrame={3} />);
    expect(images(container)[0].src).toContain("/api/p/p/files/aaa/frame/3");
    expect(images(container)[1].src).toContain("/api/p/p/files/bbb/preview");
  });
});
