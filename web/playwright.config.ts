import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: { baseURL: "http://127.0.0.1:8099", trace: "retain-on-failure" },
  webServer: {
    command: "bash e2e/start-server.sh",
    url: "http://127.0.0.1:8099/api/auth/me",
    reuseExistingServer: false,
    timeout: 180_000,
  },
});
