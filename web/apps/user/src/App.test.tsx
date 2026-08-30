import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@zhishu/shared";
import App from "./App";

const request = vi.fn();
const authorizedFetch = vi.fn();
const login = vi.fn();
const register = vi.fn();
const logout = vi.fn();
let authState: Record<string, unknown>;

vi.mock("./auth", () => ({ useAuth: () => authState }));

function renderApp(path = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}><App /></MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  request.mockReset();
  authorizedFetch.mockReset();
  login.mockReset();
  register.mockReset();
  logout.mockReset();
  authState = {
    status: "anonymous",
    user: null,
    api: { request, authorizedFetch },
    login,
    register,
    logout,
  };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ model_names: ["test-model"], document_extensions: [".txt"] }), { status: 200 })));
});

describe("user application", () => {
  it("keeps the anonymous homepage read-only", async () => {
    renderApp();
    expect(await screen.findByText("知识精准检索")).toBeVisible();
    expect(screen.queryByPlaceholderText("输入问题，或拖入资料以建立本会话知识库…")).not.toBeInTheDocument();
  });

  it("shows Chinese validation and login failures", async () => {
    const registration = renderApp("/register");
    fireEvent.change(screen.getByLabelText("邮箱 *"), { target: { value: "valid@example.com" } });
    fireEvent.change(screen.getByLabelText("用户名 *"), { target: { value: "x" } });
    fireEvent.change(screen.getByLabelText("密码 *"), { target: { value: "Password123!" } });
    fireEvent.click(screen.getAllByRole("button", { name: "注册" }).at(-1)!);
    expect(await screen.findByRole("alert")).toHaveTextContent("用户名长度需为 3 至 16 个字符");
    registration.unmount();

    login.mockRejectedValue(new ApiError("Invalid credentials", 401));
    renderApp("/login");
    fireEvent.change(screen.getByLabelText("邮箱 *"), { target: { value: "valid@example.com" } });
    fireEvent.change(screen.getByLabelText("密码 *"), { target: { value: "WrongPassword" } });
    fireEvent.click(screen.getAllByRole("button", { name: "登录" }).at(-1)!);
    expect(await screen.findByRole("alert")).toHaveTextContent("邮箱或密码错误");
  });

  it("uses runtime file types and uploads without sending an empty prompt", async () => {
    authState = { ...authState, status: "authenticated", user: { id: "user-1", email: "user@example.com", username: "member", role: "user" } };
    request.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/threads/" && init?.method === "POST") return { id: "thread-1", title: "New Chat" };
      if (path === "/threads/") return [];
      if (path.startsWith("/chat/")) return [];
      if (path.startsWith("/documents/")) return [];
      return {};
    });
    const view = renderApp();
    const input = await waitFor(() => view.container.querySelector<HTMLInputElement>('input[type="file"]'));
    expect(input).not.toBeNull();
    await waitFor(() => expect(input).toHaveAttribute("accept", ".txt"));
    fireEvent.change(input!, { target: { files: [new File(["knowledge"], "knowledge.txt", { type: "text/plain" })] } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith("/documents/upload/thread-1", expect.objectContaining({ method: "POST" })));
    expect(authorizedFetch).not.toHaveBeenCalled();
  });

  it("aborts generation and retains the partial answer", async () => {
    authState = { ...authState, status: "authenticated", user: { id: "user-1", email: "user@example.com", username: "member", role: "user" } };
    request.mockImplementation(async (path: string) => {
      if (path === "/threads/") return [{ id: "thread-1", title: "测试会话" }];
      if (path.startsWith("/chat/")) return [];
      if (path.startsWith("/documents/")) return [];
      return {};
    });
    authorizedFetch.mockImplementation(async (_path: string, init: RequestInit) => {
      const signal = init.signal!;
      const encoder = new TextEncoder();
      return new Response(new ReadableStream({
        start(controller) {
          controller.enqueue(encoder.encode('{"type":"llm_chunk","content":"部分回答"}\n'));
          signal.addEventListener("abort", () => controller.error(new DOMException("aborted", "AbortError")));
        },
      }), { status: 200 });
    });
    renderApp("/chat/thread-1");
    const composer = await screen.findByPlaceholderText("输入问题，或拖入资料以建立本会话知识库…");
    fireEvent.change(composer, { target: { value: "问题" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByText("部分回答")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "停止生成" }));
    await waitFor(() => expect(screen.getByText("部分回答")).toBeVisible());
  });

  it("does not append a late stream event after stop while retaining the partial answer", async () => {
    authState = { ...authState, status: "authenticated", user: { id: "user-1", email: "user@example.com", username: "member", role: "user" } };
    request.mockImplementation(async (path: string) => {
      if (path === "/threads/") return [{ id: "thread-1", title: "测试会话" }];
      if (path.startsWith("/chat/") || path.startsWith("/documents/")) return [];
      return {};
    });
    authorizedFetch.mockImplementation(async (_path: string, init: RequestInit) => {
      const encoder = new TextEncoder();
      return new Response(new ReadableStream({
        start(controller) {
          controller.enqueue(encoder.encode('{"type":"llm_chunk","content":"已保留"}\n'));
          init.signal!.addEventListener("abort", () => {
            controller.enqueue(encoder.encode('{"type":"llm_chunk","content":"迟到内容"}\n'));
            controller.close();
          });
        },
      }), { status: 200 });
    });
    renderApp("/chat/thread-1");
    fireEvent.change(await screen.findByPlaceholderText("输入问题，或拖入资料以建立本会话知识库…"), { target: { value: "问题" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByText("已保留")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "停止生成" }));
    await waitFor(() => expect(screen.getByText("已保留")).toBeVisible());
    expect(screen.queryByText("迟到内容")).not.toBeInTheDocument();
  });

  it("switches history and requires confirmation before deleting a document or thread", async () => {
    authState = { ...authState, status: "authenticated", user: { id: "user-1", email: "user@example.com", username: "member", role: "user" } };
    request.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/threads/" && !init?.method) return [
        { id: "thread-1", title: "第一会话" },
        { id: "thread-2", title: "第二会话" },
      ];
      if (path === "/chat/thread-1") return [{ role: "ai", content: "第一答案" }];
      if (path === "/chat/thread-2") return [{ role: "ai", content: "第二答案" }];
      if (path === "/documents/thread-1") return [{ id: "doc-1", file_name: "guide.txt", status: "completed", chunk_count: 1 }];
      if (path === "/documents/thread-2") return [];
      return {};
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderApp("/chat/thread-1");
    expect(await screen.findByText("第一答案")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "删除 guide.txt" }));
    expect(confirm).toHaveBeenCalled();
    expect(request).not.toHaveBeenCalledWith("/documents/doc-1", expect.objectContaining({ method: "DELETE" }));

    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "删除 guide.txt" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith("/documents/doc-1", expect.objectContaining({ method: "DELETE" })));

    fireEvent.click(screen.getByRole("button", { name: "第二会话" }));
    expect(await screen.findByText("第二答案")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "管理会话 第二会话" }));
    fireEvent.click(screen.getByRole("button", { name: /^删除$/ }));
    await waitFor(() => expect(request).toHaveBeenCalledWith("/threads/thread-2", expect.objectContaining({ method: "DELETE" })));
  });

  it("shows upload progress and clears it after an upload-only send", async () => {
    authState = { ...authState, status: "authenticated", user: { id: "user-1", email: "user@example.com", username: "member", role: "user" } };
    let finishUpload!: (value: unknown) => void;
    request.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/threads/" && init?.method === "POST") return { id: "thread-upload", title: "New Chat" };
      if (path.startsWith("/documents/upload/")) return new Promise((resolve) => { finishUpload = resolve; });
      if (path === "/threads/") return [];
      if (path.startsWith("/chat/")) return [];
      if (path.startsWith("/documents/")) return [];
      return {};
    });
    const view = renderApp();
    const input = await waitFor(() => view.container.querySelector<HTMLInputElement>('input[type="file"]'));
    await waitFor(() => expect(input).toHaveAttribute("accept", ".txt"));
    fireEvent.change(input!, { target: { files: [new File(["knowledge"], "guide.txt", { type: "text/plain" })] } });
    await waitFor(() => expect(screen.getByText("guide.txt")).toBeVisible());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith("/documents/upload/thread-upload", expect.objectContaining({ method: "POST" })));
    await waitFor(() => expect(document.querySelector(".upload-progress")).not.toBeNull());
    finishUpload({ document_id: "doc-1" });
    await waitFor(() => expect(screen.queryByText("正在上传 guide.txt…")).not.toBeInTheDocument());
  });

  it("reports an upload failure and clears the pending progress state", async () => {
    authState = { ...authState, status: "authenticated", user: { id: "user-1", email: "user@example.com", username: "member", role: "user" } };
    request.mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/threads/" && init?.method === "POST") return { id: "thread-upload-error", title: "New Chat" };
      if (path.startsWith("/documents/upload/")) throw new ApiError("上传失败", 500);
      if (path === "/threads/" || path.startsWith("/chat/") || path.startsWith("/documents/")) return [];
      return {};
    });
    const view = renderApp();
    const input = await waitFor(() => view.container.querySelector<HTMLInputElement>('input[type="file"]'));
    await waitFor(() => expect(input).toHaveAttribute("accept", ".txt"));
    fireEvent.change(input!, { target: { files: [new File(["knowledge"], "broken.txt", { type: "text/plain" })] } });
    await waitFor(() => expect(screen.getByText("broken.txt")).toBeVisible());
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("上传失败");
    await waitFor(() => expect(screen.queryByText("正在上传 broken.txt…")).not.toBeInTheDocument());
  });

  it("renders tool events, malformed-line feedback, and the completed answer", async () => {
    authState = { ...authState, status: "authenticated", user: { id: "user-1", email: "user@example.com", username: "member", role: "user" } };
    request.mockImplementation(async (path: string) => {
      if (path === "/threads/") return [{ id: "thread-1", title: "测试会话" }];
      if (path.startsWith("/chat/") || path.startsWith("/documents/")) return [];
      return {};
    });
    authorizedFetch.mockResolvedValue(new Response(new ReadableStream({
      start(controller) {
        const encoder = new TextEncoder();
        controller.enqueue(encoder.encode('{"type":"tool_call","name":"search","args":{"q":"policy"}}\nnot-json\n{"type":"tool_result","name":"search","content":"done"}\n{"type":"llm_chunk","content":"最终回答"}\n'));
        controller.close();
      },
    }), { status: 200 }));
    renderApp("/chat/thread-1");
    fireEvent.change(await screen.findByPlaceholderText("输入问题，或拖入资料以建立本会话知识库…"), { target: { value: "问题" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(await screen.findByText("工具调用：")).toBeVisible();
    expect(await screen.findByText("工具结果：search")).toBeVisible();
    expect(await screen.findByText("收到一段无法解析的流式数据，其他内容已保留。")).toBeVisible();
    expect(await screen.findByText("最终回答")).toBeVisible();
  });
});
