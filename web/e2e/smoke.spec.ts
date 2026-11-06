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

test("scan a folder, resolve the identical group, restore from trash", async ({ page }) => {
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
  await page.goto("/p/demo/folders");
  await page.getByLabel("Name", { exact: true }).fill("Lib");
  await page.getByLabel("Container path").fill(mediaPath);
  await page.getByRole("button", { name: "Create Folder" }).click();
  await expect(page.getByRole("status")).toHaveText("Folder created.");
  await page.getByRole("button", { name: "Scan now" }).click();
  await expect(page.getByRole("status")).toHaveText("Scan queued.");

  await page.goto("/p/demo/groups");
  await expect(page.getByRole("link", { name: "2 files" })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("link", { name: "2 files" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Group of 2 files");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/p\/demo\/groups$/);

  await page.goto("/p/demo/jobs");
  await expect(page.locator("code", { hasText: "b.jpg" })).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "Restore", exact: true }).click();
  await expect(page.getByText("Nothing in the trash.")).toBeVisible({ timeout: 20_000 });
});
