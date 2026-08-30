import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 180_000,
  fullyParallel: false,
  // The opt-in restart recovery test restarts the isolated backend and both
  // Nginx containers.  Keep every project single-worker so it cannot race a
  // registration, admin, or mobile flow against that restart.
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  expect: { toHaveScreenshot: { maxDiffPixelRatio: 0.005, threshold: 0.1, animations: "disabled" } },
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://localhost:8501",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "non-ai", grep: /@non-ai/, use: { ...devices["Desktop Chrome"], channel: process.env.PLAYWRIGHT_CHANNEL || "chrome" } },
    { name: "admin", grep: /@admin/, use: { ...devices["Desktop Chrome"], channel: process.env.PLAYWRIGHT_CHANNEL || "chrome" } },
    { name: "real-ai", grep: /@real-ai/, use: { ...devices["Desktop Chrome"], channel: process.env.PLAYWRIGHT_CHANNEL || "chrome" } },
    { name: "mobile", grep: /@mobile/, use: { ...devices["Desktop Chrome"], channel: process.env.PLAYWRIGHT_CHANNEL || "chrome" } },
  ],
});
