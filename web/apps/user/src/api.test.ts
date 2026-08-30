import { afterEach, describe, expect, it, vi } from "vitest";
import { consumeNdjson, createApiClient, type StreamEvent } from "@zhishu/shared";

afterEach(() => vi.restoreAllMocks());

describe("consumeNdjson", () => {
  it("parses fragmented events and reports malformed lines without dropping valid data", async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('{"type":"llm_chunk","content":"你'));
        controller.enqueue(encoder.encode('好"}\nnot-json\n{"type":"tool_call","name":"search","args":{}}\n{"type":"tool_result","name":"search","content":"done"}\n'));
        controller.close();
      },
    });
    const events: StreamEvent[] = [];
    const malformed: string[] = [];
    await consumeNdjson(new Response(stream, { status: 200 }), (event) => events.push(event), (line) => malformed.push(line));
    expect(events).toHaveLength(3);
    expect(events[0]).toMatchObject({ type: "llm_chunk", content: "你好" });
    expect(malformed).toEqual(["not-json"]);
    expect(events[2]).toMatchObject({ type: "tool_result", name: "search", content: "done" });
  });

  it("keeps a final event without a trailing newline and forwards server error events", async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('{"type":"llm_chunk","content":"partial"}\n{"type":"error","content":"provider failed"}'));
        controller.close();
      },
    });
    const events: StreamEvent[] = [];
    await consumeNdjson(new Response(stream, { status: 200 }), (event) => events.push(event));
    expect(events).toEqual([
      { type: "llm_chunk", content: "partial" },
      { type: "error", content: "provider failed" },
    ]);
  });
});

describe("createApiClient", () => {
  it("refreshes once after a 401 and retries with the new in-memory token", async () => {
    let token: string | null = null;
    const expired = vi.fn();
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "expired" }), { status: 401 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ access_token: "new-token" }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify([{ id: "thread-1" }]), { status: 200 }));
    const client = createApiClient({
      surface: "user",
      getToken: () => token,
      setToken: (value) => { token = value; },
      onSessionExpired: expired,
    });
    await expect(client.request("/threads/")).resolves.toEqual([{ id: "thread-1" }]);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    const retryHeaders = new Headers(fetchMock.mock.calls[2][1]?.headers);
    expect(retryHeaders.get("Authorization")).toBe("Bearer new-token");
    expect(expired).not.toHaveBeenCalled();
  });

  it("shares one refresh request across concurrent 401 responses", async () => {
    let token: string | null = "expired";
    let refreshCalls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/refresh-token")) {
        refreshCalls += 1;
        await Promise.resolve();
        return new Response(JSON.stringify({ access_token: "new-token" }), { status: 200 });
      }
      const headers = new Headers(init?.headers);
      return headers.get("Authorization") === "Bearer new-token"
        ? new Response(JSON.stringify({ ok: true }), { status: 200 })
        : new Response(JSON.stringify({ detail: "expired" }), { status: 401 });
    });
    const client = createApiClient({ surface: "user", getToken: () => token, setToken: (value) => { token = value; }, onSessionExpired: vi.fn() });
    await Promise.all([client.request("/threads/"), client.request("/users/me")]);
    expect(refreshCalls).toBe(1);
  });
});
