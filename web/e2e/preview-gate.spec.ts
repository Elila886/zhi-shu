import { expect, test } from "@playwright/test";

const userBaseUrl = process.env.PREVIEW_USER_BASE_URL || "http://localhost:8511";
const adminBaseUrl = process.env.PREVIEW_ADMIN_BASE_URL || "http://localhost:8512";
const userEmail = process.env.PREVIEW_USER_EMAIL;
const userPassword = process.env.PREVIEW_USER_PASSWORD;
const adminEmail = process.env.PREVIEW_ADMIN_EMAIL;
const adminPassword = process.env.PREVIEW_ADMIN_PASSWORD;

test("@preview @non-ai hard reload restores user and administrator sessions", async ({ browser }) => {
  test.skip(
    process.env.PREVIEW_MODE !== "1" || !userEmail || !userPassword || !adminEmail || !adminPassword,
    "environment blocked: preview account variables are required",
  );

  const userContext = await browser.newContext();
  const userPage = await userContext.newPage();
  await userPage.goto(`${userBaseUrl}/login`);
  await userPage.getByLabel("邮箱 *").fill(userEmail!);
  await userPage.getByLabel("密码 *").fill(userPassword!);
  await userPage.locator("main").getByRole("button", { name: "登录", exact: true }).click();
  await expect(userPage.getByRole("button", { name: "退出登录" })).toBeVisible();
  await userPage.reload();
  await expect(userPage.getByRole("button", { name: "退出登录" })).toBeVisible();
  await userContext.close();

  const adminContext = await browser.newContext();
  const adminPage = await adminContext.newPage();
  await adminPage.goto(`${adminBaseUrl}/`);
  await adminPage.getByLabel("管理员邮箱").fill(adminEmail!);
  await adminPage.getByLabel("密码").fill(adminPassword!);
  await adminPage.getByRole("button", { name: "登录管理后台" }).click();
  await expect(adminPage.getByRole("heading", { name: "数据概览" })).toBeVisible();
  await adminPage.reload();
  await expect(adminPage.getByRole("heading", { name: "数据概览" })).toBeVisible();
  await adminContext.close();
});
