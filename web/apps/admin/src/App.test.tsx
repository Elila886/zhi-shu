import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const request = vi.fn();
const logout = vi.fn();
let authState: Record<string, unknown>;

vi.mock("./auth", () => ({ useAdminAuth: () => authState }));

function renderApp(path = "/overview") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}><App /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  request.mockReset();
  logout.mockReset();
  authState = {
    status: "authenticated",
    user: { id: "admin-1", email: "admin@example.com", username: "admin", role: "admin" },
    api: { request },
    login: vi.fn(),
    logout,
  };
});

describe("admin application", () => {
  it("shows the independent administrator login when anonymous", () => {
    authState = { ...authState, status: "anonymous", user: null };
    renderApp("/");
    expect(screen.getByRole("heading", { name: "管理后台" })).toBeVisible();
    expect(screen.getByRole("button", { name: "登录管理后台" })).toBeVisible();
  });

  it("shows audit navigation only to a super administrator", async () => {
    request.mockImplementation(async (path: string) => path.includes("health")
      ? { backend: "healthy", database: "healthy", pgvector: "healthy" }
      : { users: 0, active_users_30d: 0, threads: 0, documents: 0, today_threads: 0, failed_documents: 0, generated_at: "2026-08-29" });
    const first = renderApp();
    expect(screen.queryByText("操作审计")).not.toBeInTheDocument();
    first.unmount();
    authState = { ...authState, user: { ...(authState.user as object), role: "super_admin" } };
    renderApp();
    expect(await screen.findByText("操作审计")).toBeVisible();
  });

  it("submits password reset from user management", async () => {
    request.mockImplementation(async (path: string) => {
      if (path.startsWith("/admin/users?")) return { items: [{ id: "user-1", username: "member", email: "member@example.com", role: "user", is_active: true, is_verified: true, disabled_reason: null, last_login_at: null, created_at: "2026-08-29", updated_at: "2026-08-29", first_name: "", last_name: "", thread_count: 0 }], total: 1, page: 1, page_size: 100 };
      return { message: "ok" };
    });
    const view = renderApp("/users");
    await screen.findByText("member");
    fireEvent.click(screen.getByRole("button", { name: "管理" }));
    const passwordFields = view.container.querySelectorAll<HTMLInputElement>('.dialog input[type="password"]');
    fireEvent.change(passwordFields[0], { target: { value: "Replacement123!" } });
    fireEvent.change(passwordFields[1], { target: { value: "Replacement123!" } });
    fireEvent.click(screen.getByRole("button", { name: "确认重置" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith("/admin/users/user-1/reset-password", expect.objectContaining({ method: "POST" })));
  });

  it("submits role and disabled-status changes from the administrator dialog", async () => {
    authState = { ...authState, user: { id: "root-1", email: "root@example.com", username: "root", role: "super_admin" } };
    request.mockImplementation(async (path: string) => {
      if (path.startsWith("/admin/users?")) return { items: [{ id: "user-1", username: "member", email: "member@example.com", role: "user", is_active: true, is_verified: true, disabled_reason: null, last_login_at: null, created_at: "2026-08-29", updated_at: "2026-08-29", first_name: "", last_name: "", thread_count: 0 }], total: 1, page: 1, page_size: 100 };
      return { message: "ok" };
    });
    const view = renderApp("/users");
    await screen.findByText("member");
    fireEvent.click(screen.getByRole("button", { name: "管理" }));
    fireEvent.change(view.container.querySelector<HTMLSelectElement>('.dialog select')!, { target: { value: "admin" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "启用账号" }));
    fireEvent.change(screen.getByPlaceholderText("必填"), { target: { value: "policy" } });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith(
      "/admin/users/user-1",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ role: "admin", is_active: false, disabled_reason: "policy" }) }),
    ));
  });

  it("lets a super administrator maintain a personnel profile and query permission", async () => {
    authState = { ...authState, user: { id: "root-1", email: "root@example.com", username: "root", role: "super_admin" } };
    request.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path.startsWith("/admin/users?")) return { items: [{ id: "user-1", username: "manager", email: "manager@example.com", role: "admin", is_active: true, is_verified: true, disabled_reason: null, last_login_at: null, created_at: "2026-08-29", updated_at: "2026-08-29", first_name: "", last_name: "", thread_count: 0 }], total: 1, page: 1, page_size: 100 };
      if (path === "/admin/users/user-1/personnel-profile" && !init?.method) return { profile: null, can_query_personnel: false };
      return { message: "ok" };
    });
    renderApp("/users");
    await screen.findByText("manager");
    fireEvent.click(screen.getByRole("button", { name: "管理" }));
    await screen.findByLabelText("姓名");
    fireEvent.change(screen.getByLabelText("姓名"), { target: { value: "张三" } });
    fireEvent.change(screen.getByLabelText("工号"), { target: { value: "E-1001" } });
    fireEvent.change(screen.getByLabelText("部门"), { target: { value: "工程部" } });
    fireEvent.change(screen.getByLabelText("职位"), { target: { value: "工程师" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "允许该管理员在聊天中查询人员基本信息" }));
    fireEvent.click(screen.getByRole("button", { name: "保存员工档案与查询权限" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith(
      "/admin/users/user-1/personnel-profile",
      expect.objectContaining({ method: "PUT", body: JSON.stringify({ full_name: "张三", employee_no: "E-1001", department: "工程部", job_title: "工程师", work_email: null, work_phone: null, employment_status: "active" }) }),
    ));
    await waitFor(() => expect(request).toHaveBeenCalledWith(
      "/admin/users/user-1/personnel-query-permission",
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ enabled: true }) }),
    ));
  });

  it("requires explicit confirmation before deleting a document", async () => {
    request.mockImplementation(async (path: string) => {
      if (path.startsWith("/admin/documents?")) return { items: [{ id: "doc-1", file_name: "guide.pdf", status: "completed", chunk_count: 3, error_message: null, uploaded_at: "2026-08-29", thread_id: "thread-1", user_id: "user-1", username: "member", email: "member@example.com" }], total: 1, page: 1, page_size: 100 };
      return { deleted_chunks: 3 };
    });
    renderApp("/documents");
    await screen.findByText("guide.pdf");
    fireEvent.click(screen.getByRole("button", { name: "删除 guide.pdf" }));
    const confirm = screen.getByRole("button", { name: "确认删除" });
    expect(confirm).toBeDisabled();
    fireEvent.click(screen.getByText("我确认删除该文档及向量数据"));
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    await waitFor(() => expect(request).toHaveBeenCalledWith("/admin/documents/doc-1", expect.objectContaining({ method: "DELETE" })));
  });
});
