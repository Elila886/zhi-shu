import { createContext, useContext, useEffect, useMemo, useRef, useState, type PropsWithChildren } from "react";
import { createApiClient, type AuthUser, type LoginResponse, type UserProfile } from "@zhishu/shared";

type AuthStatus = "loading" | "authenticated" | "anonymous";

interface RegistrationData {
  email: string;
  username: string;
  password: string;
  first_name: string;
  last_name: string;
}

interface AuthContextValue {
  status: AuthStatus;
  user: AuthUser | null;
  api: ReturnType<typeof createApiClient>;
  login: (email: string, password: string) => Promise<LoginResponse>;
  register: (data: RegistrationData) => Promise<{ message: string }>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: PropsWithChildren) {
  const tokenRef = useRef<string | null>(null);
  const bootstrapStarted = useRef(false);
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUser] = useState<AuthUser | null>(null);

  const api = useMemo(
    () =>
      createApiClient({
        surface: "user",
        getToken: () => tokenRef.current,
        setToken: (token) => {
          tokenRef.current = token;
        },
        onSessionExpired: () => {
          setUser(null);
          setStatus("anonymous");
        },
      }),
    [],
  );

  useEffect(() => {
    if (bootstrapStarted.current) return;
    bootstrapStarted.current = true;
    void api
      .bootstrap()
      .then(() => api.request<UserProfile>("/users/me"))
      .then((profile) => {
        setUser({ id: profile.id, email: profile.email, username: profile.username, role: profile.role });
        setStatus("authenticated");
      })
      .catch(() => {
        setUser(null);
        setStatus("anonymous");
      });
  }, [api]);

  async function login(email: string, password: string) {
    const response = await api.login(email, password);
    setUser(response.user);
    setStatus("authenticated");
    return response;
  }

  async function register(data: RegistrationData) {
    return api.request<{ message: string }>("/auth/signup", { method: "POST", body: JSON.stringify(data) });
  }

  async function logout() {
    await api.logout();
  }

  return <AuthContext.Provider value={{ status, user, api, login, register, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
