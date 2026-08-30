import { createContext, useContext, useEffect, useMemo, useRef, useState, type PropsWithChildren } from "react";
import { createApiClient, type AuthUser, type LoginResponse, type UserProfile } from "@zhishu/shared";

type Status = "loading" | "authenticated" | "anonymous";
interface Value {
  status: Status;
  user: AuthUser | null;
  api: ReturnType<typeof createApiClient>;
  login: (email: string, password: string) => Promise<LoginResponse>;
  logout: () => Promise<void>;
}
const Context = createContext<Value | null>(null);

export function AdminAuthProvider({ children }: PropsWithChildren) {
  const token = useRef<string | null>(null);
  const started = useRef(false);
  const [status, setStatus] = useState<Status>("loading");
  const [user, setUser] = useState<AuthUser | null>(null);
  const api = useMemo(() => createApiClient({
    surface: "admin",
    getToken: () => token.current,
    setToken: (value) => { token.current = value; },
    onSessionExpired: () => { setUser(null); setStatus("anonymous"); },
  }), []);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void api.bootstrap()
      .then(() => api.request<UserProfile>("/admin/me"))
      .then((profile) => {
        if (!(["admin", "super_admin"] as string[]).includes(profile.role)) throw new Error("forbidden");
        setUser({ id: profile.id, email: profile.email, username: profile.username, role: profile.role });
        setStatus("authenticated");
      })
      .catch(() => { setUser(null); setStatus("anonymous"); });
  }, [api]);

  async function login(email: string, password: string) {
    const response = await api.login(email, password);
    setUser(response.user); setStatus("authenticated");
    return response;
  }
  async function logout() { await api.logout(); }
  return <Context.Provider value={{ status, user, api, login, logout }}>{children}</Context.Provider>;
}

export function useAdminAuth() {
  const value = useContext(Context);
  if (!value) throw new Error("useAdminAuth must be used inside AdminAuthProvider");
  return value;
}
