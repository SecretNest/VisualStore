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
