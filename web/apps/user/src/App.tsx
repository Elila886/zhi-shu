import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  FileText,
  LogIn,
  Menu,
  MoreHorizontal,
  Paperclip,
  Plus,
  Search,
  Send,
  Square,
  Trash2,
  UploadCloud,
  UserPlus,
  X,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize from "rehype-sanitize";
import { Navigate, Route, Routes, matchPath, useLocation, useNavigate } from "react-router-dom";
import {
  ApiError,
  BrandMark,
  Button,
  EmptyState,
  Spinner,
  consumeNdjson,
  type ChatMessage,
  type ChatThread,
  type PublicConfig,
  type StreamEvent,
  type ThreadDocument,
} from "@zhishu/shared";
import { useAuth } from "./auth";
import { documentTypeLabel, fileAcceptValue, filterAllowedFiles, normalizeDocumentExtensions } from "./files";

const FALLBACK_MODELS = ["gpt-4o-mini"];

function errorMessage(error: unknown, fallback = "操作失败，请稍后再试。") {
  if (!(error instanceof ApiError)) return fallback;
  const known: Record<string, string> = {
    "Invalid credentials": "邮箱或密码错误。",
    "Your account is not active": "账号已被禁用。",
    "Your account is not verified": "账号尚未验证。",
    "Email already exists.": "该邮箱已被注册。",
    "Username already exists.": "该用户名已被使用。",
    "该邮箱已被注册。": "该邮箱已被注册。",
    "该用户名已被使用。": "该用户名已被使用。",
  };
  return known[error.message] || error.message || fallback;
}

function Protected({ children }: { children: React.ReactNode }) {
  const { status } = useAuth();
  if (status === "loading") return <PageLoader />;
  return status === "authenticated" ? children : <Navigate to="/login" replace />;
}

function PageLoader() {
  return (
    <div className="page-loader">
      <BrandMark />
      <Spinner label="正在恢复登录状态" />
    </div>
  );
}

export default function App() {
  const { status } = useAuth();
  const location = useLocation();
  const [mobileSidebar, setMobileSidebar] = useState(false);
  const [modelName, setModelName] = useState("");
  const threadMatch = matchPath("/chat/:threadId", location.pathname);
  const activeThreadId = threadMatch?.params.threadId || null;

  const configQuery = useQuery({
    queryKey: ["public-config"],
    queryFn: async () => {
      const response = await fetch("/api/v1/config/public", { credentials: "include" });
      if (!response.ok) throw new Error("config unavailable");
      return (await response.json()) as PublicConfig;
    },
    staleTime: 300_000,
  });
  const models = configQuery.data?.model_names?.length ? configQuery.data.model_names : FALLBACK_MODELS;
  const documentExtensions = normalizeDocumentExtensions(configQuery.data?.document_extensions || []);
  useEffect(() => {
    if (models.length && (!modelName || !models.includes(modelName))) setModelName(models[0]);
  }, [modelName, models]);
  useEffect(() => setMobileSidebar(false), [location.pathname]);

  if (status === "loading") return <PageLoader />;

  return (
    <div className="app-shell">
      <button className="mobile-menu" onClick={() => setMobileSidebar(true)} aria-label="打开侧栏">
        <Menu size={21} />
      </button>
      {mobileSidebar && <button className="sidebar-scrim" onClick={() => setMobileSidebar(false)} aria-label="关闭侧栏" />}
      <Sidebar
        open={mobileSidebar}
        activeThreadId={activeThreadId}
        models={models}
        documentExtensions={documentExtensions}
        modelName={modelName || models[0]}
        onModelChange={setModelName}
        onClose={() => setMobileSidebar(false)}
      />
      <main className="main-view">
        <Routes>
          <Route path="/" element={<Home modelName={modelName || models[0]} documentExtensions={documentExtensions} />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />
          <Route
            path="/chat/:threadId"
            element={
              <Protected>
                <ChatWorkspace modelName={modelName || models[0]} documentExtensions={documentExtensions} />
              </Protected>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

interface SidebarProps {
  open: boolean;
  activeThreadId: string | null;
  models: string[];
  documentExtensions: string[];
  modelName: string;
  onModelChange: (value: string) => void;
  onClose: () => void;
}

function Sidebar({ open, activeThreadId, models, documentExtensions, modelName, onModelChange, onClose }: SidebarProps) {
  const { status, user, api, logout } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [menuThreadId, setMenuThreadId] = useState<string | null>(null);
  const threadsQuery = useQuery({
    queryKey: ["threads"],
    queryFn: () => api.request<ChatThread[]>("/threads/"),
    enabled: status === "authenticated",
  });
  const documentsQuery = useQuery({
    queryKey: ["documents", activeThreadId],
    queryFn: () => api.request<ThreadDocument[]>(`/documents/${activeThreadId}`),
    enabled: status === "authenticated" && Boolean(activeThreadId),
  });
  const deleteThread = useMutation({
    mutationFn: (threadId: string) => api.request<void>(`/threads/${threadId}`, { method: "DELETE" }),
    onSuccess: (_, threadId) => {
      void queryClient.invalidateQueries({ queryKey: ["threads"] });
      if (activeThreadId === threadId) navigate("/");
      setMenuThreadId(null);
    },
  });
  const deleteDocument = useMutation({
    mutationFn: (documentId: string) => api.request(`/documents/${documentId}`, { method: "DELETE" }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["documents", activeThreadId] }),
  });

  async function signOut() {
    navigate("/", { replace: true });
    queryClient.clear();
    await logout();
  }

  return (
    <aside className={`sidebar ${open ? "sidebar-open" : ""}`}>
      <div className="sidebar-head">
        <BrandMark />
        <button className="mobile-close" onClick={onClose} aria-label="关闭侧栏"><X size={19} /></button>
      </div>
      {status === "authenticated" && user ? (
        <>
          <section className="sidebar-section welcome-section">
            <p className="section-label">你好，{user.username}</p>
            <Button className="new-chat" onClick={() => navigate("/")}><Plus size={17} />新建知识问答</Button>
          </section>
          <section className="sidebar-section">
            <label className="section-label" htmlFor="model-select">模型选择</label>
            <select id="model-select" value={modelName} onChange={(event) => onModelChange(event.target.value)}>
              {models.map((model) => <option value={model} key={model}>{model}</option>)}
            </select>
          </section>
          <section className="sidebar-section history-section">
            <p className="history-heading">最近</p>
            <div className="history-list">
              {threadsQuery.isLoading && <div className="sidebar-loading"><Spinner /></div>}
              {threadsQuery.data?.map((thread) => (
                <div className={`history-row ${activeThreadId === thread.id ? "active" : ""}`} key={thread.id}>
                  <button className="history-title" title={thread.title} onClick={() => navigate(`/chat/${thread.id}`)}>
                    {thread.title || "New Chat"}
                  </button>
                  {menuThreadId === thread.id ? (
                    <button
                      className="history-delete"
                      onClick={() => window.confirm(`确认删除会话“${thread.title || "New Chat"}”及其全部聊天记录吗？`) && deleteThread.mutate(thread.id)}
                      disabled={deleteThread.isPending}
                    >
                      删除
                    </button>
                  ) : (
                    <button className="history-more" onClick={() => setMenuThreadId(thread.id)} aria-label={`管理会话 ${thread.title}`}>
                      <MoreHorizontal size={17} />
                    </button>
                  )}
                </div>
              ))}
              {!threadsQuery.isLoading && !threadsQuery.data?.length && <p className="sidebar-caption">还没有会话记录。</p>}
            </div>
          </section>
          <section className="sidebar-section documents-section">
            <p className="section-label">本会话知识库</p>
            {!activeThreadId && <p className="sidebar-caption">开始新对话后即可添加资料。</p>}
            {activeThreadId && documentsQuery.isLoading && <div className="sidebar-loading"><Spinner /></div>}
            {documentsQuery.data?.map((document, index) => (
              <div className="document-row" key={document.id}>
                <span title={document.file_name}>{index + 1}. {document.file_name}</span>
                <button
                  aria-label={`删除 ${document.file_name}`}
                  disabled={deleteDocument.isPending}
                  onClick={() => window.confirm(`确认删除“${document.file_name}”及其向量切片吗？`) && deleteDocument.mutate(document.id)}
                ><Trash2 size={14} /></button>
              </div>
            ))}
            {activeThreadId && !documentsQuery.isLoading && !documentsQuery.data?.length && (
              <p className="sidebar-caption">
                还没有上传文件。{documentExtensions.length
                  ? `可添加 ${documentTypeLabel(documentExtensions)} 文档。`
                  : "正在读取允许上传的文件类型。"}
              </p>
            )}
          </section>
          <div className="sidebar-footer">
            <Button className="logout-button" onClick={() => void signOut()}>退出登录</Button>
          </div>
        </>
      ) : (
        <section className="sidebar-section auth-panel">
          <p className="section-label">账号与权限</p>
          <div className="auth-actions">
            <Button onClick={() => navigate("/login")}><LogIn size={16} />登录</Button>
            <Button onClick={() => navigate("/register")}><UserPlus size={16} />注册</Button>
          </div>
          <p className="sidebar-caption">登录后可创建专属知识空间，并管理文件与会话记录。</p>
        </section>
      )}
    </aside>
  );
}

function Hero() {
  return (
    <section className="hero">
      <div className="eyebrow">KNOWLEDGE, MADE ACTIONABLE</div>
      <h1>企业智能知识助手</h1>
      <p>连接制度文档、项目资料与团队经验，让每一次提问都得到有依据、可追溯的答案。</p>
    </section>
  );
}

function Home({ modelName, documentExtensions }: { modelName: string; documentExtensions: string[] }) {
  const { status } = useAuth();
  return (
    <div className="content-shell">
      <Hero />
      {status === "authenticated" ? (
        <>
          <div className="chat-heading"><h2>今天想查找什么？</h2><span /></div>
          <ChatWorkspace modelName={modelName} documentExtensions={documentExtensions} />
        </>
      ) : (
        <div className="feature-grid">
          {[
            [<Search />, "知识精准检索", "从业务材料中提取与问题最相关的信息。"],
            [<UploadCloud />, "多格式资料接入", "支持 PDF、Word 和 TXT，快速沉淀团队知识。"],
            [<FileText />, "连续上下文对话", "保留会话脉络，让复杂问题得到连贯回答。"],
          ].map(([icon, title, copy], index) => (
            <article className="feature-card" key={index}>
              <div className="feature-icon">{icon}</div><b>{title}</b><span>{copy}</span>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}

function AuthCard({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  const navigate = useNavigate();
  return (
    <div className="content-shell auth-content">
      <div className="auth-card">
        <button className="back-link" onClick={() => navigate("/")}><ArrowLeft size={16} />返回首页</button>
        <div className="auth-card-head"><span className="auth-logo">✦</span><div><h1>{title}</h1><p>{subtitle}</p></div></div>
        {children}
      </div>
    </div>
  );
}

function LoginPage() {
  const { status, login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  if (status === "authenticated") return <Navigate to="/" replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!email || !password) return setError("请填写邮箱和密码。");
    setPending(true); setError("");
    try {
      await login(email, password);
      navigate("/");
    } catch (error) {
      setError(errorMessage(error, "登录失败，请稍后再试。"));
    } finally { setPending(false); }
  }

  return (
    <AuthCard title="登录知识工作台" subtitle="继续访问您的企业知识空间">
      <form onSubmit={(event) => void submit(event)} className="auth-form">
        <label htmlFor="login-email">邮箱 *</label><input id="login-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
        <label htmlFor="login-password">密码 *</label><input id="login-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        {error && <div className="form-error" role="alert">{error}</div>}
        <Button className="primary-button" disabled={pending}>{pending ? <><Spinner />正在登录...</> : "登录"}</Button>
      </form>
    </AuthCard>
  );
}

function RegisterPage() {
  const { status, register } = useAuth();
  const navigate = useNavigate();
  const [data, setData] = useState({ email: "", username: "", password: "", first_name: "", last_name: "" });
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  if (status === "authenticated") return <Navigate to="/" replace />;
  const field = (name: keyof typeof data) => ({
    value: data[name],
    onChange: (event: React.ChangeEvent<HTMLInputElement>) => setData((current) => ({ ...current, [name]: event.target.value })),
  });

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!data.email) return setError("请填写邮箱。");
    if (data.username.length < 3 || data.username.length > 16) return setError("用户名长度需为 3 至 16 个字符。");
    if (data.password.length < 8 || data.password.length > 32) return setError("密码长度需为 8 至 32 个字符。");
    setPending(true); setError("");
    try {
      await register(data);
      navigate("/login", { replace: true, state: { registered: true } });
    } catch (error) {
      setError(errorMessage(error, "注册失败，请检查填写的信息。"));
    } finally { setPending(false); }
  }

  return (
    <AuthCard title="创建企业知识空间" subtitle="注册后即可上传资料并开始知识问答">
      <form onSubmit={(event) => void submit(event)} className="auth-form register-form">
        <div><label htmlFor="register-email">邮箱 *</label><input id="register-email" type="email" {...field("email")} autoComplete="email" /></div>
        <div><label htmlFor="register-username">用户名 *</label><input id="register-username" maxLength={16} {...field("username")} autoComplete="username" /></div>
        <div><label htmlFor="register-password">密码 *</label><input id="register-password" type="password" maxLength={32} {...field("password")} autoComplete="new-password" /></div>
        <div><label htmlFor="register-first-name">名字</label><input id="register-first-name" maxLength={25} {...field("first_name")} /></div>
        <div><label htmlFor="register-last-name">姓氏</label><input id="register-last-name" maxLength={25} {...field("last_name")} /></div>
        {error && <div className="form-error full-row" role="alert">{error}</div>}
        <Button className="primary-button full-row" disabled={pending}>{pending ? <><Spinner />正在创建账号...</> : "注册"}</Button>
      </form>
    </AuthCard>
  );
}

function Markdown({ children }: { children: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>{children}</ReactMarkdown>;
}

function ChatWorkspace({ modelName, documentExtensions }: { modelName: string; documentExtensions: string[] }) {
  const { api } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const match = matchPath("/chat/:threadId", location.pathname);
  const routeThreadId = match?.params.threadId || null;
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [prompt, setPrompt] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [streamingText, setStreamingText] = useState("");
  const [steps, setSteps] = useState<StreamEvent[]>([]);
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState<string[]>([]);
  const [generating, setGenerating] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const historyQuery = useQuery({
    queryKey: ["chat-history", routeThreadId],
    queryFn: () => api.request<ChatMessage[]>(`/chat/${routeThreadId}`),
    enabled: Boolean(routeThreadId),
  });
  useEffect(() => {
    setMessages(routeThreadId ? historyQuery.data || [] : []);
  }, [routeThreadId, historyQuery.data]);
  useEffect(() => {
    setStreamingText(""); setSteps([]); setError("");
  }, [routeThreadId]);

  async function ensureThread(text: string, pendingFiles: File[]) {
    if (routeThreadId) return routeThreadId;
    const thread = await api.request<ChatThread>("/threads/", { method: "POST" });
    const title = (text.trim() || pendingFiles[0]?.name || "New Chat").slice(0, 30);
    await api.request(`/threads/${thread.id}`, { method: "PATCH", body: JSON.stringify({ title }) });
    await queryClient.invalidateQueries({ queryKey: ["threads"] });
    return thread.id;
  }

  async function uploadFiles(threadId: string, pendingFiles: File[]) {
    for (const file of pendingFiles) {
      setUploading((current) => [...current, file.name]);
      try {
        const body = new FormData(); body.append("file", file);
        await api.request(`/documents/upload/${threadId}`, { method: "POST", body });
      } finally {
        setUploading((current) => current.filter((name) => name !== file.name));
      }
    }
    if (pendingFiles.length) await queryClient.invalidateQueries({ queryKey: ["documents", threadId] });
  }

  async function send() {
    const text = prompt.trim();
    const pendingFiles = files;
    if ((!text && !pendingFiles.length) || generating) return;
    setError(""); setPrompt(""); setFiles([]);
    let threadId: string | null = null;
    let partialResponse = "";
    const isNewThread = !routeThreadId;
    try {
      threadId = await ensureThread(text, pendingFiles);
      await uploadFiles(threadId, pendingFiles);
      if (!text) {
        if (isNewThread) navigate(`/chat/${threadId}`);
        return;
      }
      setMessages((current) => [...current, { role: "human", content: text }]);
      setGenerating(true); setStreamingText(""); setSteps([]);
      const controller = new AbortController(); controllerRef.current = controller;
      const response = await api.authorizedFetch(`/chat/${threadId}`, {
        method: "POST",
        body: JSON.stringify({ prompt: text, model_name: modelName }),
        signal: controller.signal,
      });
      await consumeNdjson(
        response,
        (event) => {
          // A fetch implementation can yield a buffered NDJSON chunk while an
          // abort is racing with the reader.  Preserve chunks already shown,
          // but never apply a late event after the user pressed stop.
          if (controller.signal.aborted) return;
          if (event.type === "llm_chunk") {
            partialResponse += String(event.content || "");
            setStreamingText(partialResponse);
          } else if (event.type === "tool_call" || event.type === "tool_result") {
            setSteps((current) => [...current, event]);
          } else if (event.type === "error") {
            setError(String("content" in event ? event.content : "生成回答失败，请重试。"));
          }
        },
        () => setError("收到一段无法解析的流式数据，其他内容已保留。"),
      );
      if (partialResponse) setMessages((current) => [...current, { role: "ai", content: partialResponse }]);
      setStreamingText("");
      await queryClient.invalidateQueries({ queryKey: ["chat-history", threadId] });
      if (isNewThread) navigate(`/chat/${threadId}`);
    } catch (caught) {
      if ((caught as Error).name === "AbortError") {
        if (partialResponse) setMessages((current) => [...current, { role: "ai", content: partialResponse }]);
      } else {
        setError(errorMessage(caught, "处理请求时发生错误，请重试。"));
      }
    } finally {
      controllerRef.current = null;
      setGenerating(false);
      setStreamingText("");
    }
  }

  function stop() {
    controllerRef.current?.abort();
  }

  function addFiles(selected: FileList | null) {
    if (!selected) return;
    const next = filterAllowedFiles(selected, documentExtensions);
    setFiles((current) => [...current, ...next]);
  }

  const visibleMessages = useMemo(
    () => streamingText ? [...messages, { role: "ai", content: streamingText } satisfies ChatMessage] : messages,
    [messages, streamingText],
  );

  return (
    <section className="chat-workspace">
      <div className="message-list" aria-live="polite">
        {historyQuery.isLoading && routeThreadId && <div className="center-loader"><Spinner label="加载会话" /></div>}
        {!visibleMessages.length && !historyQuery.isLoading && (
          <EmptyState icon={<Search size={26} />}>输入问题，或添加资料来建立本会话知识库。</EmptyState>
        )}
        {visibleMessages.map((message, index) => (
          <article className={`message ${message.role === "human" ? "human-message" : "ai-message"}`} key={`${index}-${message.content.slice(0, 12)}`}>
            <div className="avatar">{message.role === "human" ? "你" : "✦"}</div>
            <div className="message-content"><Markdown>{message.content}</Markdown>{generating && index === visibleMessages.length - 1 && <span className="typing-caret">▌</span>}</div>
          </article>
        ))}
        {steps.length > 0 && (
          <div className="tool-steps">
            {steps.map((step, index) => step.type === "tool_call" ? (
              <div key={index}><b>工具调用：</b>{String("name" in step ? step.name : "")}<code>{String(JSON.stringify("args" in step ? step.args : null) ?? "")}</code></div>
            ) : (
              <details key={index}><summary>工具结果：{String("name" in step ? step.name : "")}</summary><pre>{String("content" in step ? step.content : "")}</pre></details>
            ))}
          </div>
        )}
        {uploading.map((name) => <div className="upload-progress" key={name}><Spinner />正在上传 {name}…</div>)}
        {error && <div className="chat-error" role="alert">{error}</div>}
      </div>
      <div className="composer-wrap">
        {files.length > 0 && (
          <div className="pending-files">
            {files.map((file, index) => <span key={`${file.name}-${index}`}><FileText size={14} />{file.name}<button onClick={() => setFiles((current) => current.filter((_, i) => i !== index))}><X size={13} /></button></span>)}
          </div>
        )}
        <div className="composer">
          <input ref={fileInputRef} type="file" multiple accept={fileAcceptValue(documentExtensions)} hidden onChange={(event) => addFiles(event.target.files)} />
          <button className="attach-button" onClick={() => fileInputRef.current?.click()} disabled={generating || !documentExtensions.length} aria-label="添加资料"><Paperclip size={19} /></button>
          <textarea
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void send(); }
            }}
            placeholder="输入问题，或拖入资料以建立本会话知识库…"
            disabled={generating}
            rows={1}
          />
          {generating ? (
            <button className="send-button stop-button" onClick={stop} aria-label="停止生成"><Square size={15} fill="currentColor" /></button>
          ) : (
            <button className="send-button" onClick={() => void send()} disabled={!prompt.trim() && !files.length} aria-label="发送"><Send size={18} /></button>
          )}
        </div>
        <p className="composer-note">知识回答可能存在误差，请结合原始资料核验。</p>
      </div>
    </section>
  );
}
