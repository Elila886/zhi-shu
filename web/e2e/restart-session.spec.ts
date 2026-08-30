import { execFileSync } from "node:child_process";
import path from "node:path";
import { expect, test } from "@playwright/test";

test("container restart preserves the refresh-cookie session @non-ai", async ({ page, request }) => {
  test.skip(
    process.env.E2E_RESTART_SERVICES !== "1" || !process.env.E2E_COMPOSE_FILE,
    "opt-in and point E2E_COMPOSE_FILE at the isolated E2E Compose stack",
  );
  const suffix = `${Date.now()}`.slice(-9);
  const account = { email: `restart${suffix}@example.com`, username: `restart${suffix}`.slice(0, 16), password: "Restart123!" };
  let userId = "";
  try {
    const signup = await request.post("/api/v1/auth/signup", { data: { ...account, first_name: "Restart", last_name: "Test" } });
    expect(signup.status()).toBe(201);
    const login = await request.post("/api/v1/auth/login", { form: { username: account.email, password: account.password } });
    userId = ((await login.json()) as { user: { id: string } }).user.id;

    await page.goto("/login");
    await page.getByLabel("邮箱 *").fill(account.email);
    await page.getByLabel("密码 *").fill(account.password);
    await page.locator("main").getByRole("button", { name: "登录", exact: true }).click();
    await expect(page.getByText(`你好，${account.username}`)).toBeVisible();

    execFileSync("docker", [
      "compose",
      "-p",
      process.env.E2E_COMPOSE_PROJECT || "zhishu-e2e",
      "-f",
      process.env.E2E_COMPOSE_FILE!,
      "restart",
      "backend",
      "frontend",
      "admin",
    ], {
      cwd: path.resolve(__dirname, "../.."),
      stdio: "pipe",
      timeout: 120_000,
    });
    await expect.poll(async () => (await request.get("/healthz")).status(), { timeout: 120_000 }).toBe(200);
    await expect.poll(
      async () => (await request.get("/api/v1/config/public")).status(),
      { timeout: 120_000 },
    ).toBe(200);
    await page.reload();
    await expect(page.getByText(`你好，${account.username}`)).toBeVisible({ timeout: 30_000 });
  } finally {
    if (userId) {
      const login = await request.post("/api/v1/auth/login", { form: { username: account.email, password: account.password } });
      if (login.ok()) {
        const token = ((await login.json()) as { access_token: string }).access_token;
        await request.delete(`/api/v1/users/user-profile/${userId}`, { headers: { Authorization: `Bearer ${token}` } });
      }
    }
  }
});
