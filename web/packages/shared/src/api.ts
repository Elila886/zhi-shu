import type { LoginResponse, StreamEvent } from "./types";

export type Surface = "user" | "admin";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly detail?: unknown,
    public readonly code?: string,
    public readonly retryAfter?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface ClientOptions {
  surface: Surface;
  getToken: () => string | null;
  setToken: (token: string | null) => void;
  onSessionExpired: () => void;
}

const authPrefix = (surface: Surface) => (surface === "admin" ? "/auth/admin" : "/auth");

async function readError(response: Response): Promise<ApiError> {
  let detail: unknown;
  let code: string | undefined;
  let bodyRetryAfter: number | undefined;
  try {
    const body = (await response.json()) as { detail?: unknown; code?: unknown; retry_after?: unknown };
    detail = body.detail;
    code = typeof body.code === "string" ? body.code : undefined;
    bodyRetryAfter = typeof body.retry_after === "number" ? body.retry_after : undefined;
  } catch {
    detail = await response.text().catch(() => undefined);
  }
  let message = response.statusText || "请求失败";
  if (typeof detail === "string") message = detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { msg?: string };
    message = first.msg || message;
  }
  const headerRetryAfter = Number(response.headers.get("Retry-After"));
  const retryAfter = Number.isFinite(headerRetryAfter) && headerRetryAfter > 0 ? headerRetryAfter : bodyRetryAfter;
  if (response.status === 429 && retryAfter) message = `请求过于频繁，请在 ${retryAfter} 秒后重试。`;
  return new ApiError(message, response.status, detail, code, retryAfter);
}

export function createApiClient(options: ClientOptions) {
  let refreshPromise: Promise<string> | null = null;

  async function refresh(): Promise<string> {
    if (!refreshPromise) {
      refreshPromise = fetch(`/api/v1${authPrefix(options.surface)}/refresh-token`, {
        method: "POST",
        credentials: "include",
        headers: { Accept: "application/json" },
      })
        .then(async (response) => {
          if (!response.ok) throw await readError(response);
          const body = (await response.json()) as { access_token: string };
          options.setToken(body.access_token);
          return body.access_token;
        })
        .catch((error) => {
          options.setToken(null);
          options.onSessionExpired();
          throw error;
        })
        .finally(() => {
          refreshPromise = null;
        });
    }
    return refreshPromise;
  }

  async function authorizedFetch(path: string, init: RequestInit = {}, retry = true): Promise<Response> {
    const headers = new Headers(init.headers);
    headers.set("Accept", headers.get("Accept") || "application/json");
    const token = options.getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    if (init.body && !(init.body instanceof FormData) && !(init.body instanceof URLSearchParams)) {
      headers.set("Content-Type", headers.get("Content-Type") || "application/json");
    }
    let response = await fetch(`/api/v1${path}`, { ...init, headers, credentials: "include" });
    if (response.status === 401 && retry && !path.startsWith(authPrefix(options.surface))) {
      await refresh();
      response = await authorizedFetch(path, init, false);
    }
    return response;
  }

  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await authorizedFetch(path, init);
    if (!response.ok) throw await readError(response);
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  async function login(email: string, password: string): Promise<LoginResponse> {
    const form = new URLSearchParams({ username: email, password });
    const response = await fetch(`/api/v1${authPrefix(options.surface)}/login`, {
      method: "POST",
      body: form,
      credentials: "include",
      headers: { Accept: "application/json", "Content-Type": "application/x-www-form-urlencoded" },
    });
    if (!response.ok) throw await readError(response);
    const body = (await response.json()) as LoginResponse;
    options.setToken(body.access_token);
    return body;
  }

  async function bootstrap(): Promise<string> {
    return refresh();
  }

  async function logout(): Promise<void> {
    try {
      await fetch(`/api/v1${authPrefix(options.surface)}/logout`, {
        method: "POST",
        credentials: "include",
        headers: { Accept: "application/json" },
      });
    } finally {
      options.setToken(null);
      options.onSessionExpired();
    }
  }

  return { request, authorizedFetch, login, bootstrap, logout };
}

export async function consumeNdjson(
  response: Response,
  onEvent: (event: StreamEvent) => void,
  onMalformed?: (line: string) => void,
): Promise<void> {
  if (!response.ok) throw await readError(response);
  if (!response.body) throw new ApiError("浏览器未提供流式响应", 500);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const raw of lines) {
      const line = raw.trim();
      if (!line) continue;
      try {
        onEvent(JSON.parse(line) as StreamEvent);
      } catch {
        onMalformed?.(line);
      }
    }
    if (done) break;
  }
  if (buffer.trim()) {
    try {
      onEvent(JSON.parse(buffer) as StreamEvent);
    } catch {
      onMalformed?.(buffer);
    }
  }
}
