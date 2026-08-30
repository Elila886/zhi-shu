import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdminAuthProvider, useAdminAuth } from "./auth";

function Probe() {
  const { status, user } = useAdminAuth();
  return <div>{status}:{user?.username || "anonymous"}</div>;
}

describe("administrator session restoration", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("restores through the administrator profile surface after a refresh", async () => {
    const requests: string[] = [];
    vi.stubGlobal("fetch", vi.fn().mockImplementation(async (input: string | URL) => {
      const url = String(input);
      requests.push(url);
      if (url.endsWith("/auth/admin/refresh-token")) {
        return new Response(JSON.stringify({ access_token: "admin-token" }), { status: 200 });
      }
      if (url.endsWith("/admin/me")) {
        return new Response(JSON.stringify({
          id: "admin-1", username: "manager", email: "manager@example.com", role: "admin",
          first_name: "", last_name: "", is_active: true, is_verified: true,
          last_login_at: null, disabled_reason: null, created_at: "2026-08-29", updated_at: "2026-08-29",
        }), { status: 200 });
      }
      return new Response(JSON.stringify({ detail: "unexpected request" }), { status: 500 });
    }));

    render(<AdminAuthProvider><Probe /></AdminAuthProvider>);

    await waitFor(() => expect(screen.getByText("authenticated:manager")).toBeVisible());
    expect(requests).toContain("/api/v1/auth/admin/refresh-token");
    expect(requests).toContain("/api/v1/admin/me");
    expect(requests).not.toContain("/api/v1/users/me");
  });
});
