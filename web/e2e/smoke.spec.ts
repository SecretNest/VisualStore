import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";

test("login, create a profile, run a backup, log out", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);

  await page.getByLabel("Password").fill("wrong");
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page.getByRole("alert")).toHaveText("Wrong password.");

  await page.getByLabel("Password").fill("e2e-password");
  await page.getByRole("button", { name: "Log in" }).click();
  await expect(page).toHaveURL(/\/profiles$/);
  await expect(page.getByText("No profiles yet.")).toBeVisible();

  await page.getByLabel("Name", { exact: true }).fill("demo");
  await page.getByLabel("Display name").fill("Demo library");
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page).toHaveURL(/\/p\/demo\/overview$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Demo library");
  await expect(page.getByText("Metadata: never")).toBeVisible();

  await page.getByRole("button", { name: "Back up now" }).click();
  await expect(page.locator("tr.job-done td").first()).toHaveText("backup", { timeout: 20_000 });
  await expect(page.getByText("Metadata: never")).toHaveCount(0);

  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/p/demo/overview");
  await expect(page).toHaveURL(/\/login$/);
});

test("switch model, scan a folder, resolve the identical group, restore from trash", async ({ page }) => {
  test.setTimeout(120_000);
  const mediaPath = readFileSync(new URL(".media-path", import.meta.url), "utf8");
  await page.goto("/login");
  await page.getByLabel("Password").fill("e2e-password");
  await page.getByRole("button", { name: "Log in" }).click();
  await page.goto("/profiles");
  if (await page.getByText("No profiles yet.").isVisible().catch(() => false)) {
    await page.getByLabel("Name", { exact: true }).fill("demo");
    await page.getByRole("button", { name: "Create" }).click();
  }
  await page.goto("/p/demo/jobs");
  await page.getByRole("combobox", { name: "Model", exact: true }).selectOption("tiny-test-8-v1");
  await page.getByRole("button", { name: "Switch model" }).click();
  await expect(page.getByRole("status")).toContainText("Model switch queued");
  await expect(page.locator("tr.job-done td", { hasText: "model_switch" })).toBeVisible({ timeout: 20_000 });

  await page.goto("/p/demo/folders");
  await page.getByLabel("Name", { exact: true }).fill("Lib");
  await page.getByLabel("Container path").fill(mediaPath);
  await page.getByRole("button", { name: "Create Folder" }).click();
  await expect(page.getByRole("status")).toHaveText("Folder created.");
  await page.getByRole("button", { name: "Scan now" }).click();
  await expect(page.getByRole("status")).toHaveText("Scan queued.");

  await page.goto("/p/demo/groups");
  await expect(page.getByRole("link", { name: "2 files" })).toHaveCount(2, { timeout: 30_000 });
  await page.getByRole("row", { name: /identical/ }).getByRole("link", { name: "2 files" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Group of 2 files");
  const previews = page.locator("img.preview");
  await expect(previews).toHaveCount(2);
  await expect.poll(() => previews.first().evaluate((img) => (img as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
  await page.keyboard.press("o");
  await expect(page.getByLabel("Overlay opacity")).toBeVisible();
  await page.keyboard.press("b");
  await expect(page.locator(".compare-blink")).toBeVisible();
  await page.keyboard.press("s");
  await expect(page.locator(".compare-side img.preview")).toHaveCount(2);
  const thumbs = page.locator("img.thumb");
  await expect(thumbs).toHaveCount(2);
  await expect.poll(() => thumbs.first().evaluate((img) => (img as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
  const firstGroupUrl = page.url();
  await page.keyboard.press("Enter");
  await expect(page).not.toHaveURL(firstGroupUrl); // Enter confirms and moves to the next open group
  await page.goto("/p/demo/groups");
  await expect(page.getByRole("link", { name: "2 files" })).toHaveCount(1, { timeout: 20_000 });

  await page.getByRole("row", { name: /near duplicate/ }).getByRole("link", { name: "2 files" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Group of 2 files");
  const strip = page.getByRole("list", { name: "Frames" });
  await expect(strip.getByRole("listitem")).toHaveCount(2, { timeout: 20_000 });
  await expect(strip.getByRole("listitem").nth(1)).toContainText("1.0 s");
  await strip.getByRole("listitem").nth(1).click();
  await expect(page.locator("img.preview-preview").first()).toHaveAttribute("src", /\/frame\/1$/);
  await expect.poll(() => page.locator("img.preview-preview").first().evaluate((img) => (img as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
  await page.keyboard.press("Escape");
  await page.goto("/p/demo/groups");
  await page.getByText("Default rules for this filter").click();
  await page.getByRole("button", { name: "Preview" }).click();
  await expect(page.getByRole("status")).toContainText("1 groups match, 1 get the default resolution (0 skipped), 1 files will be moved to the trash");
  await page.getByRole("button", { name: "Execute" }).click();
  await expect(page.getByText(/Bulk resolution queued as job/)).toBeVisible();
  await expect(page.getByRole("link", { name: "2 files" })).toHaveCount(0, { timeout: 20_000 });

  await page.goto("/p/demo/jobs");
  await expect(page.locator("code", { hasText: "b.png" })).toBeVisible({ timeout: 20_000 });
  await expect(page.locator("code", { hasText: "still.png" })).toBeVisible();
  await page.getByRole("button", { name: "Select all" }).click();
  await page.getByRole("button", { name: /Restore selected/ }).click();
  await expect(page.getByText("Nothing in the trash.")).toBeVisible({ timeout: 20_000 });
});
