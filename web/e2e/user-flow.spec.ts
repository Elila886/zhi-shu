import { expect, test, type APIRequestContext, type Locator, type Page } from "@playwright/test";
import path from "node:path";

const fixture = path.join(__dirname, "fixtures", "knowledge.txt");
const userEmail = process.env.E2E_USER_EMAIL;
const userPassword = process.env.E2E_USER_PASSWORD;
const adminEmail = process.env.E2E_ADMIN_EMAIL;
const adminPassword = process.env.E2E_ADMIN_PASSWORD;
const superAdminEmail = process.env.E2E_SUPER_ADMIN_EMAIL;
const superAdminPassword = process.env.E2E_SUPER_ADMIN_PASSWORD;
const realAiReady = process.env.E2E_REAL_AI === "1"
  && Boolean(process.env.E2E_OPENAI_API_KEY)
  && Boolean(process.env.E2E_MODEL_PROVIDER)
  && Boolean(process.env.E2E_MODEL_NAMES)
  && Boolean(process.env.E2E_EMBEDDINGS_MODEL_NAME)
  && Boolean(userEmail) && Boolean(userPassword)
  && Boolean(adminEmail) && Boolean(adminPassword)
  && Boolean(superAdminEmail) && Boolean(superAdminPassword);

type Account = { email: string; username: string; password: string; registered: boolean };
type LoginPayload = { access_token: string; user: { id: string } };

test.describe.configure({ mode: "serial" });

function newAccount(prefix = "e2e"): Account {
  const suffix = `${Date.now()}${Math.floor(Math.random() * 10000)}`.slice(-10);
  return { email: `${prefix}${suffix}@example.com`, username: `${prefix}${suffix}`.slice(0, 16), password: "E2eReact123!", registered: false };
}

function auth(payload: LoginPayload) { return { Authorization: `Bearer ${payload.access_token}` }; }

async function apiLogin(request: APIRequestContext, email: string, password: string, admin = false): Promise<LoginPayload> {
  const response = await request.post(admin ? "/api/v1/auth/admin/login" : "/api/v1/auth/login", { form: { username: email, password } });
  expect(response.status()).toBe(200);
  return response.json() as Promise<LoginPayload>;
}

async function cleanupAccount(request: APIRequestContext, account: Account) {
  if (!account.registered) return;
  const login = await request.post("/api/v1/auth/login", { form: { username: account.email, password: account.password } });
  if (!login.ok()) return;
  const payload = await login.json() as LoginPayload;
  await request.delete(`/api/v1/users/user-profile/${payload.user.id}`, { headers: auth(payload) });
}

async function registerAndLogin(page: Page, account: Account) {
  await page.goto("/register");
  await page.getByLabel("邮箱 *").fill(account.email);
  await page.getByLabel("用户名 *").fill(account.username);
  await page.getByLabel("密码 *").fill(account.password);
  await page.getByLabel("名字").fill("E2E");
  await page.getByLabel("姓氏").fill("Test");
  await page.locator("main").getByRole("button", { name: "注册", exact: true }).click();
  await page.waitForURL(/\/login$/);
  account.registered = true;
  await page.getByLabel("邮箱 *").fill(account.email);
  await page.getByLabel("密码 *").fill(account.password);
  await page.locator("main").getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
}

async function loginSeededUser(page: Page) {
  test.skip(!userEmail || !userPassword, "requires isolated E2E user credentials");
  await page.goto("/login");
  await page.getByLabel("邮箱 *").fill(userEmail!);
  await page.getByLabel("密码 *").fill(userPassword!);
  await page.locator("main").getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByText("你好，e2e_member")).toBeVisible();
}

async function loginSeededAdmin(page: Page, superAdmin = false) {
  const email = superAdmin ? superAdminEmail : adminEmail;
  const password = superAdmin ? superAdminPassword : adminPassword;
  test.skip(!email || !password, "requires isolated administrator credentials");
  await page.goto(process.env.E2E_ADMIN_BASE_URL || "http://localhost:18512/");
  await page.getByLabel("管理员邮箱").fill(email!);
  await page.getByLabel("密码").fill(password!);
  await page.getByRole("button", { name: "登录管理后台" }).click();
  await expect(page.getByRole("heading", { name: "数据概览" })).toBeVisible();
}

async function expectNoHorizontalOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
}

async function scrollSidebarControlIntoView(control: Locator) {
  await control.evaluate((element) => {
    const sidebar = element.closest<HTMLElement>(".sidebar");
    if (sidebar) sidebar.scrollTop = element.getBoundingClientRect().top + sidebar.scrollTop - 72;
  });
  await expect(control).toBeInViewport();
}

function watchForForbiddenNonAiRequests(page: Page) {
  const forbidden: string[] = [];
  page.on("request", (request) => {
    const url = request.url();
    if (request.method() === "POST" && (/\/api\/v1\/chat\//.test(url) || /\/api\/v1\/documents\/upload\//.test(url))) {
      forbidden.push(`${request.method()} ${url}`);
    }
  });
  return () => expect(forbidden, `non-AI flow attempted an AI mutation: ${forbidden.join(", ")}`).toEqual([]);
}

test("non-AI registration, login, hard refresh, and logout keep the user surface usable @non-ai", async ({ page, request }) => {
  const assertNoAiMutation = watchForForbiddenNonAiRequests(page);
  const account = newAccount();
  try {
    await registerAndLogin(page, account);
    await page.reload();
    await expect(page.getByText(`你好，${account.username}`)).toBeVisible();
    await page.getByRole("button", { name: "退出登录" }).click();
    await expect(page.getByRole("button", { name: "登录" })).toBeVisible();
  } finally {
    await cleanupAccount(request, account);
  }
  assertNoAiMutation();
});

test("non-AI seeded sessions synchronize through the UI and require both deletion confirmations @non-ai", async ({ page, request }) => {
  const assertNoAiMutation = watchForForbiddenNonAiRequests(page);
  test.skip(!userEmail || !userPassword, "requires isolated E2E user credentials");
  const seeded = await apiLogin(request, userEmail!, userPassword!);
  const created = await request.post("/api/v1/threads/", { headers: auth(seeded) });
  expect(created.status()).toBe(201);
  const createdThread = await created.json() as { id: string };
  const titled = await request.patch(`/api/v1/threads/${createdThread.id}`, { headers: auth(seeded), data: { title: "E2E UI 同步会话" } });
  expect(titled.status()).toBe(200);

  await loginSeededUser(page);
  await expect(page.getByRole("button", { name: "E2E 稳定会话 A", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "E2E 稳定会话 A", exact: true }).click();
  await expect(page).toHaveURL(/\/chat\//);
  await expect(page.getByText("E2E 待删除文档.txt")).toBeVisible();

  await page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "删除 E2E 待删除文档.txt" }).click();
  await expect(page.getByText("E2E 待删除文档.txt")).not.toBeVisible();

  await page.reload();
  await expect(page.getByRole("button", { name: "E2E UI 同步会话", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "E2E UI 同步会话", exact: true }).click();
  await page.getByRole("button", { name: "管理会话 E2E UI 同步会话" }).click();
  await page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "删除", exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("button", { name: "E2E UI 同步会话", exact: true })).not.toBeVisible();
  assertNoAiMutation();
});

test("non-AI file selection and removal never starts an upload or model request @non-ai", async ({ page }) => {
  const assertNoAiMutation = watchForForbiddenNonAiRequests(page);
  await loginSeededUser(page);
  await page.locator('input[type="file"]').setInputFiles(fixture);
  await expect(page.getByText("knowledge.txt")).toBeVisible();
  await page.locator(".pending-files button").click();
  await expect(page.getByText("knowledge.txt")).not.toBeVisible();
  assertNoAiMutation();
});

test("mobile user login, register, sidebar, composer, and upload list remain operable @mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/login");
  await expect(page.locator("main").getByRole("button", { name: "登录", exact: true })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await page.getByRole("button", { name: "打开侧栏" }).click();
  await page.getByRole("complementary").getByRole("button", { name: "注册", exact: true }).click();
  await expect(page.locator("main").getByRole("button", { name: "注册", exact: true })).toBeVisible();
  await expectNoHorizontalOverflow(page);

  await loginSeededUser(page);
  await expect(page.getByRole("button", { name: "打开侧栏" })).toBeVisible();
  await page.getByRole("button", { name: "打开侧栏" }).click();
  await expect(page.getByText("本会话知识库", { exact: true })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await page.getByRole("complementary").getByRole("button", { name: "关闭侧栏" }).click();
  await expect(page.getByPlaceholder("输入问题，或拖入资料以建立本会话知识库…")).toBeVisible();
  await page.locator('input[type="file"]').setInputFiles(fixture);
  await expect(page.getByText("knowledge.txt")).toBeVisible();
  await expectNoHorizontalOverflow(page);

  await page.getByRole("button", { name: "打开侧栏" }).click();
  await page.getByRole("button", { name: "E2E 移动会话", exact: true }).click();
  await expect(page.getByText("E2E 移动待删除文档.txt")).toBeVisible();
  await page.getByRole("button", { name: "打开侧栏" }).click();
  const deleteDocumentButton = page.getByRole("button", { name: "删除 E2E 移动待删除文档.txt" });
  await scrollSidebarControlIntoView(deleteDocumentButton);
  page.once("dialog", (dialog) => void dialog.accept());
  await deleteDocumentButton.click();
  await expect(page.getByText("E2E 移动待删除文档.txt")).not.toBeVisible();
  const manageThreadButton = page.getByRole("button", { name: "管理会话 E2E 移动会话" });
  await scrollSidebarControlIntoView(manageThreadButton);
  await manageThreadButton.click();
  const deleteThreadButton = page.getByRole("button", { name: "删除", exact: true });
  await scrollSidebarControlIntoView(deleteThreadButton);
  page.once("dialog", (dialog) => void dialog.accept());
  await deleteThreadButton.click();
  await expect(page).toHaveURL(/\/$/);
  await expectNoHorizontalOverflow(page);
});

test("administrator and super-administrator API surfaces enforce the role matrix @admin", async ({ request }) => {
  test.skip(!userEmail || !userPassword || !adminEmail || !adminPassword || !superAdminEmail || !superAdminPassword, "requires isolated role credentials");
  const member = await apiLogin(request, userEmail!, userPassword!);
  expect((await request.get("/api/v1/admin/overview", { headers: auth(member) })).status()).toBe(403);
  expect((await request.get("/api/v1/admin/overview")).status()).toBe(401);

  const admin = await apiLogin(request, adminEmail!, adminPassword!, true);
  expect((await request.get("/api/v1/admin/overview", { headers: auth(admin) })).status()).toBe(200);
  expect((await request.get("/api/v1/admin/audit-logs", { headers: auth(admin) })).status()).toBe(403);
  expect((await request.get("/api/v1/users/me", { headers: auth(admin) })).status()).toBe(401);

  const superAdmin = await apiLogin(request, superAdminEmail!, superAdminPassword!, true);
  expect((await request.get("/api/v1/admin/audit-logs", { headers: auth(superAdmin) })).status()).toBe(200);
});

test("super administrator changes persist after refresh and password reset invalidates the old credential @admin", async ({ page, request }) => {
  test.skip(!superAdminEmail || !superAdminPassword, "requires isolated super-administrator credentials");
  const target = newAccount("managed");
  const signup = await request.post("/api/v1/auth/signup", { data: { ...target, first_name: "Managed", last_name: "E2E" } });
  expect(signup.status()).toBe(201);
  target.registered = true;
  const targetId = ((await signup.json()) as { user: { id: string } }).user.id;
  const replacement = "Replacement123!";
  try {
    const oldSession = await apiLogin(request, target.email, target.password);
    await loginSeededAdmin(page, true);
    await page.getByRole("link", { name: "用户管理" }).click();
    const row = page.getByRole("row").filter({ hasText: target.username });
    await expect(row).toBeVisible();
    await row.getByRole("button", { name: "管理" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.locator("select").selectOption("admin");
    await dialog.getByRole("checkbox", { name: "启用账号" }).uncheck();
    await dialog.getByPlaceholder("必填").fill("e2e policy");
    await dialog.getByRole("button", { name: "保存修改" }).click();
    await expect(page.getByText("用户设置已更新")).toBeVisible();
    await page.reload();
    await expect(page.getByRole("row").filter({ hasText: target.username })).toContainText("admin");
    await expect(page.getByRole("row").filter({ hasText: target.username })).toContainText("禁用");
    expect((await request.get("/api/v1/users/me", { headers: auth(oldSession) })).status()).toBe(401);

    const admin = await apiLogin(request, superAdminEmail!, superAdminPassword!, true);
    expect((await request.get(`/api/v1/admin/users?q=${target.username}`, { headers: auth(admin) })).status()).toBe(200);
    const disabledRow = page.getByRole("row").filter({ hasText: target.username });
    await disabledRow.getByRole("button", { name: "管理" }).click();
    const enabledDialog = page.getByRole("dialog");
    await enabledDialog.getByRole("checkbox", { name: "启用账号" }).check();
    await enabledDialog.getByRole("button", { name: "保存修改" }).click();
    await expect(page.getByText("用户设置已更新")).toBeVisible();
    await page.reload();
    const refreshedRow = page.getByRole("row").filter({ hasText: target.username });
    await expect(refreshedRow).not.toContainText("禁用");
    await refreshedRow.getByRole("button", { name: "管理" }).click();
    const passwordInputs = page.getByRole("dialog").locator('input[type="password"]');
    await passwordInputs.nth(0).fill(replacement);
    await passwordInputs.nth(1).fill(replacement);
    await page.getByRole("dialog").getByRole("button", { name: "确认重置" }).click();
    await expect(page.getByText("密码已重置，该用户的现有会话已撤销。")).toBeVisible();
    expect((await request.get("/api/v1/users/me", { headers: auth(oldSession) })).status()).toBe(401);
    expect((await request.post("/api/v1/auth/refresh-token")).status()).toBe(401);
    expect((await request.post("/api/v1/auth/login", { form: { username: target.email, password: target.password } })).status()).toBe(401);
    expect((await request.post("/api/v1/auth/login", { form: { username: target.email, password: replacement } })).status()).toBe(200);
  } finally {
    const targetSession = await apiLogin(request, target.email, replacement);
    await request.delete(`/api/v1/users/user-profile/${targetId}`, { headers: auth(targetSession) });
  }
});

test("mobile administrator navigation, user dialog, password reset, and document confirmation remain operable @mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await loginSeededAdmin(page, true);
  await expectNoHorizontalOverflow(page);
  await page.getByRole("button", { name: "打开导航" }).click();
  await page.getByRole("link", { name: "用户管理" }).click();
  await expect(page.getByRole("heading", { name: "用户管理" })).toBeVisible();
  await page.getByRole("row").filter({ hasText: "e2e_member" }).getByRole("button", { name: "管理" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("dialog").getByRole("button", { name: "确认重置" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await page.getByRole("dialog").locator("header button").click();
  await page.getByRole("button", { name: "打开导航" }).click();
  await page.getByRole("link", { name: "知识库管理" }).click();
  await page.getByRole("button", { name: "删除 E2E 管理待删除文档.txt" }).click();
  const confirm = page.getByRole("button", { name: "确认删除" });
  await expect(confirm).toBeDisabled();
  await page.getByText("我确认删除该文档及向量数据").click();
  await expect(confirm).toBeEnabled();
  await confirm.click();
  await expect(page.getByRole("button", { name: "删除 E2E 管理待删除文档.txt" })).not.toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("real AI indexing and stream contract exercise provider events without substitutes @real-ai", async ({ page }) => {
  if (!realAiReady) {
    throw new Error("environment blocked: E2E_REAL_AI=1 plus real provider, model, embedding, and all role-account variables are required");
  }
  const account = newAccount("realai");
  try {
    await registerAndLogin(page, account);
    await page.setInputFiles('input[type="file"]', fixture);
    const upload = page.waitForResponse((response) => response.url().includes("/documents/upload/") && response.request().method() === "POST");
    await page.getByRole("button", { name: "发送" }).click();
    expect((await upload).status()).toBe(200);
    await expect(page.getByText("knowledge.txt")).toBeVisible();

    await page.getByPlaceholder("输入问题，或拖入资料以建立本会话知识库…").fill("资料的验收关键词是什么？请调用检索工具后用一句话回答。");
    const stream = page.waitForResponse((response) => response.url().includes("/chat/") && response.request().method() === "POST");
    await page.getByRole("button", { name: "发送" }).click();
    const streamResponse = await stream;
    expect(streamResponse.headers()["content-type"]).toContain("application/x-ndjson");
    expect(streamResponse.headers()["cache-control"]).toContain("no-cache");
    expect(streamResponse.headers()["x-accel-buffering"]).toContain("no");
    const streamBody = await streamResponse.body();
    const events = new TextDecoder().decode(streamBody).trim().split("\n").filter(Boolean).map((line) => JSON.parse(line));
    expect(events.filter((event) => event.type === "llm_chunk").length).toBeGreaterThan(1);
    expect(events.some((event) => event.type === "tool_call")).toBeTruthy();
    expect(events.some((event) => event.type === "tool_result")).toBeTruthy();
    await expect(page.locator(".tool-steps")).toContainText(/工具调用|工具结果/, { timeout: 90_000 });
    await expect(page.locator("article.ai-message").last()).toContainText(/.+/, { timeout: 90_000 });
  } finally {
    await cleanupAccount(page.request, account);
  }
});

test("real AI stop retains a streamed partial answer @real-ai", async ({ page }) => {
  if (!realAiReady) {
    throw new Error("environment blocked: E2E_REAL_AI=1 plus real provider, model, embedding, and all role-account variables are required");
  }
  const account = newAccount("realaistop");
  try {
    await registerAndLogin(page, account);
    const composer = page.getByPlaceholder("输入问题，或拖入资料以建立本会话知识库…");
    await composer.fill("请从 1 持续列到 50，每项只写序号和一个短词，不要解释。");
    await page.getByRole("button", { name: "发送" }).click();
    const answer = page.locator("article.ai-message").last();
    await expect(answer).toContainText(/.+/, { timeout: 90_000 });
    const partial = (await answer.textContent())?.replace("▌", "").trim() || "";
    expect(partial.length).toBeGreaterThan(0);
    await expect(page.getByRole("button", { name: "停止生成" })).toBeVisible({ timeout: 30_000 });
    await page.getByRole("button", { name: "停止生成" }).click();
    await expect(page.getByRole("button", { name: "停止生成" })).not.toBeVisible();
    await expect(answer).toContainText(partial);
  } finally {
    await cleanupAccount(page.request, account);
  }
});

test("authorized administrator can query a personnel profile from the chat page @real-ai", async ({ page, request }) => {
  if (!realAiReady) {
    throw new Error("environment blocked: real personnel E2E requires the same isolated real-AI configuration");
  }
  const target = newAccount("personnel");
  const employeeNo = `E2E-${Date.now()}`;
  const fullName = "E2E 人员验证";
  const superAdmin = await apiLogin(request, superAdminEmail!, superAdminPassword!, true);
  const manager = await apiLogin(request, adminEmail!, adminPassword!);
  const signup = await request.post("/api/v1/auth/signup", { data: { ...target, first_name: "Personnel", last_name: "E2E" } });
  expect(signup.status()).toBe(201);
  const targetId = ((await signup.json()) as { user: { id: string } }).user.id;
  try {
    expect((await request.put(`/api/v1/admin/users/${targetId}/personnel-profile`, {
      headers: auth(superAdmin),
      data: { full_name: fullName, employee_no: employeeNo, department: "E2E 工程部", job_title: "验证工程师", work_email: null, work_phone: null, employment_status: "active" },
    })).status()).toBe(200);
    expect((await request.patch(`/api/v1/admin/users/${manager.user.id}/personnel-query-permission`, {
      headers: auth(superAdmin), data: { enabled: true },
    })).status()).toBe(200);
    const thread = await request.post("/api/v1/threads/", { headers: auth(manager) });
    expect(thread.status()).toBe(201);
    const threadId = ((await thread.json()) as { id: string }).id;

    await page.goto("/login");
    await page.getByLabel("邮箱 *").fill(adminEmail!);
    await page.getByLabel("密码 *").fill(adminPassword!);
    await page.locator("main").getByRole("button", { name: "登录", exact: true }).click();
    await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
    await page.goto(`/chat/${threadId}`);
    const composer = page.getByPlaceholder("输入问题，或拖入资料以建立本会话知识库…");
    await composer.fill(`查询${fullName}的基本信息`);
    await page.getByRole("button", { name: "发送" }).click();
    await expect(page.locator(".tool-steps")).toContainText("query_personnel", { timeout: 90_000 });
    const answer = page.locator("article.ai-message").last();
    await expect(answer).toContainText("查询结果：", { timeout: 90_000 });
    await expect(answer).toContainText(`工号：${employeeNo}`);
    await expect(answer).toContainText("工作邮箱：未录入");
  } finally {
    await request.patch(`/api/v1/admin/users/${manager.user.id}/personnel-query-permission`, {
      headers: auth(superAdmin), data: { enabled: false },
    }).catch(() => undefined);
  }
});
