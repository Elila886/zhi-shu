import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  BookOpen,
  Database,
  FileWarning,
  LayoutDashboard,
  LogOut,
  Menu,
  Search,
  ShieldCheck,
  Trash2,
  UserRoundCog,
  Users,
  X,
} from "lucide-react";
import { Navigate, NavLink, Route, Routes, useNavigate } from "react-router-dom";
import {
  ApiError,
  BrandMark,
  Button,
  EmptyState,
  Spinner,
  type AdminDocument,
  type AdminHealth,
  type AdminOverview,
  type AdminUser,
  type AuditLog,
  type Paginated,
  type Role,
} from "@zhishu/shared";
import { useAdminAuth } from "./auth";

function message(error: unknown, fallback = "操作失败，请稍后重试。") {
  if (!(error instanceof ApiError)) return fallback;
  const mapping: Record<string, string> = {
    "Invalid credentials": "邮箱或密码错误。",
    "Administrator permission required": "当前账号没有后台管理权限。",
    "Your account is not active": "账号已被禁用。",
    "Your account is not verified": "账号尚未验证。",
  };
  return mapping[error.message] || error.message || fallback;
}

export default function App() {
  const { status } = useAdminAuth();
  if (status === "loading") return <Loading />;
  if (status === "anonymous") return <Login />;
  return <AdminShell />;
}

function Loading() {
  return <div className="admin-loading"><BrandMark admin /><Spinner label="正在恢复管理会话" /></div>;
}

function Login() {
  const { login } = useAdminAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!email || !password) return setError("请填写管理员邮箱和密码。");
    setPending(true); setError("");
    try { await login(email, password); }
    catch (error) { setError(message(error, "无法登录管理后台。")); }
    finally { setPending(false); }
  }
  return (
    <main className="login-page">
      <section className="login-card">
        <div className="login-emblem"><ShieldCheck size={30} /></div>
        <h1>管理后台</h1><p>企业智能知识助手 · 仅限授权管理员</p>
        <form onSubmit={(event) => void submit(event)}>
          <label htmlFor="admin-email">管理员邮箱</label><input id="admin-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
          <label htmlFor="admin-password">密码</label><input id="admin-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
          {error && <div className="alert error" role="alert">{error}</div>}
          <Button className="primary" disabled={pending}>{pending ? <><Spinner />正在验证...</> : "登录管理后台"}</Button>
        </form>
      </section>
    </main>
  );
}

function AdminShell() {
  const { user, logout } = useAdminAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  async function signOut() { await logout(); queryClient.clear(); navigate("/"); }
  const nav = [
    ["/overview", <LayoutDashboard />, "数据概览"],
    ["/users", <Users />, "用户管理"],
    ["/documents", <BookOpen />, "知识库管理"],
    ...(user?.role === "super_admin" ? [["/audit", <Activity />, "操作审计"]] : []),
  ] as [string, React.ReactNode, string][];
  return (
    <div className="admin-shell">
      <button className="admin-menu" onClick={() => setOpen(true)} aria-label="打开导航"><Menu /></button>
      {open && <button className="nav-scrim" onClick={() => setOpen(false)} aria-label="关闭导航" />}
      <aside className={`admin-sidebar ${open ? "open" : ""}`}>
        <div className="nav-head"><BrandMark admin /><button onClick={() => setOpen(false)}><X /></button></div>
        <nav>{nav.map(([to, icon, label]) => <NavLink to={to} key={to} onClick={() => setOpen(false)}>{icon}{label}</NavLink>)}</nav>
        <div className="admin-account">
          <div><span>{user?.username}</span><small>{user?.email}</small><em>{user?.role}</em></div>
          <button onClick={() => void signOut()} aria-label="退出登录"><LogOut size={18} /></button>
        </div>
      </aside>
      <main className="admin-main">
        <Routes>
          <Route path="/overview" element={<Overview />} />
          <Route path="/users" element={<UsersPage />} />
          <Route path="/documents" element={<DocumentsPage />} />
          <Route path="/audit" element={user?.role === "super_admin" ? <AuditPage /> : <Navigate to="/overview" replace />} />
          <Route path="*" element={<Navigate to="/overview" replace />} />
        </Routes>
      </main>
    </div>
  );
}

function PageHeader({ title, copy }: { title: string; copy: string }) {
  return <header className="page-header"><div><h1>{title}</h1><p>{copy}</p></div><span className="online-dot">● 系统在线</span></header>;
}

function Overview() {
  const { api } = useAdminAuth();
  const overview = useQuery({ queryKey: ["admin-overview"], queryFn: () => api.request<AdminOverview>("/admin/overview") });
  const health = useQuery({ queryKey: ["admin-health"], queryFn: () => api.request<AdminHealth>("/admin/health"), refetchInterval: 60_000 });
  const cards = overview.data ? [
    [<Users />, "用户总数", overview.data.users], [<UserRoundCog />, "30 天活跃", overview.data.active_users_30d],
    [<Activity />, "会话总数", overview.data.threads], [<Database />, "文档总数", overview.data.documents],
    [<LayoutDashboard />, "今日新会话", overview.data.today_threads], [<FileWarning />, "失败文档", overview.data.failed_documents],
  ] : [];
  return <div className="admin-page"><PageHeader title="数据概览" copy="查看用户、会话与知识库运行状态" />
    {overview.isLoading ? <PanelLoader /> : overview.error ? <ErrorPanel error={overview.error} /> : <div className="metrics">{cards.map(([icon,label,value]) => <article key={String(label)}><div>{icon}</div><span>{label}</span><b>{value}</b></article>)}</div>}
    <section className="panel"><div className="panel-title"><div><h2>系统服务状态</h2><p>核心服务实时健康检查</p></div></div>
      <div className="health-grid">{Object.entries({ backend: "后端 API", database: "PostgreSQL", pgvector: "PGVector" }).map(([key,label]) => {
        const value = health.data?.[key as keyof AdminHealth]; return <div key={key}><span className={value === "healthy" ? "healthy" : "unhealthy"}>●</span><div><b>{label}</b><small>{value === "healthy" ? "运行正常" : health.isLoading ? "检查中" : "不可用"}</small></div></div>;
      })}</div>
    </section>
  </div>;
}

function UsersPage() {
  const { api, user: actor } = useAdminAuth();
  const queryClient = useQueryClient();
  const [query, setQuery] = useState(""); const [role, setRole] = useState(""); const [active, setActive] = useState("");
  const [selected, setSelected] = useState<AdminUser | null>(null); const [notice, setNotice] = useState("");
  const params = new URLSearchParams({ page_size: "100" }); if (query) params.set("q", query); if (role) params.set("role", role); if (active) params.set("active", active);
  const users = useQuery({ queryKey: ["admin-users", query, role, active], queryFn: () => api.request<Paginated<AdminUser>>(`/admin/users?${params}`) });
  const update = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: object }) => api.request(`/admin/users/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
    onSuccess: () => { setNotice("用户设置已更新"); setSelected(null); void queryClient.invalidateQueries({ queryKey: ["admin-users"] }); },
  });
  const resetPassword = useMutation({
    mutationFn: ({ id, password }: { id: string; password: string }) => api.request(`/admin/users/${id}/reset-password`, { method: "POST", body: JSON.stringify({ new_password: password }) }),
    onSuccess: () => setNotice("密码已重置，该用户的现有会话已撤销。"),
  });
  return <div className="admin-page"><PageHeader title="用户管理" copy="管理账号状态、角色与登录凭据" />
    {notice && <div className="alert success">{notice}</div>}
    <section className="panel"><div className="filters"><label className="search-field"><Search size={17}/><input placeholder="用户名或邮箱" value={query} onChange={(e) => setQuery(e.target.value)} /></label>
      <select value={role} onChange={(e) => setRole(e.target.value)}><option value="">全部角色</option><option>user</option><option>admin</option><option>super_admin</option></select>
      <select value={active} onChange={(e) => setActive(e.target.value)}><option value="">全部状态</option><option value="true">启用</option><option value="false">禁用</option></select></div>
      {users.isLoading ? <PanelLoader /> : users.error ? <ErrorPanel error={users.error} /> : <><div className="table-meta">共 {users.data?.total || 0} 位用户</div><div className="table-wrap"><table><thead><tr><th>用户</th><th>角色</th><th>状态</th><th>会话数</th><th>最近登录</th><th></th></tr></thead><tbody>{users.data?.items.map((item) => <tr key={item.id}><td><b>{item.username}</b><small>{item.email}</small></td><td><span className={`role-badge ${item.role}`}>{item.role}</span></td><td><span className={item.is_active ? "status-active" : "status-disabled"}>● {item.is_active ? "启用" : "禁用"}</span></td><td>{item.thread_count}</td><td>{formatDate(item.last_login_at ?? null)}</td><td><button className="table-action" onClick={() => { setSelected(item); setNotice(""); }}>管理</button></td></tr>)}</tbody></table></div></>}
    </section>
    {selected && <UserDialog user={selected} actorRole={actor!.role} pending={update.isPending || resetPassword.isPending} error={update.error || resetPassword.error} onClose={() => setSelected(null)} onSave={(payload) => update.mutate({ id: selected.id, payload })} onReset={(password) => resetPassword.mutate({ id: selected.id, password })} />}
  </div>;
}

function UserDialog({ user, actorRole, pending, error, onClose, onSave, onReset }: { user: AdminUser; actorRole: Role; pending: boolean; error: unknown; onClose: () => void; onSave: (payload: object) => void; onReset: (password: string) => void }) {
  const [role, setRole] = useState<Role>(user.role); const [active, setActive] = useState(user.is_active); const [reason, setReason] = useState(user.disabled_reason || "");
  const [password, setPassword] = useState(""); const [confirm, setConfirm] = useState(""); const [localError, setLocalError] = useState("");
  const displayedError = localError || (error ? message(error) : "");
  return <div className="dialog-backdrop" role="presentation"><section className="dialog" role="dialog" aria-modal="true" aria-label={`管理 ${user.email}`}><header><div><h2>{user.username}</h2><p>{user.email}</p></div><button onClick={onClose}><X /></button></header>
    <div className="dialog-section"><h3>账号与角色</h3><label>角色</label><select value={role} onChange={(e) => setRole(e.target.value as Role)} disabled={actorRole !== "super_admin"}><option>user</option><option>admin</option><option>super_admin</option></select>
      <label className="toggle-row"><input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />启用账号</label>
      {!active && <><label>禁用原因</label><input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="必填" /></>}
      <Button className="primary" disabled={pending || (!active && !reason.trim())} onClick={() => onSave({ role, is_active: active, disabled_reason: active ? null : reason })}>保存修改</Button></div>
    <div className="dialog-section"><h3>重置密码</h3><label>新密码</label><input type="password" value={password} onChange={(e) => setPassword(e.target.value)} /><label>确认新密码</label><input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
      <Button disabled={pending} onClick={() => { if (password.length < 8 || password.length > 32) return setLocalError("密码长度需为 8 至 32 个字符。"); if (password !== confirm) return setLocalError("两次密码输入不一致。"); setLocalError(""); onReset(password); }}>确认重置</Button></div>
    {displayedError && <div className="alert error">{displayedError}</div>}
  </section></div>;
}

function DocumentsPage() {
  const { api } = useAdminAuth(); const queryClient = useQueryClient(); const [query,setQuery]=useState(""); const [status,setStatus]=useState(""); const [selected,setSelected]=useState<AdminDocument|null>(null); const [confirmed,setConfirmed]=useState(false); const [notice,setNotice]=useState("");
  const params=new URLSearchParams({page_size:"100"}); if(query)params.set("q",query); if(status)params.set("status",status);
  const documents=useQuery({queryKey:["admin-documents",query,status],queryFn:()=>api.request<Paginated<AdminDocument>>(`/admin/documents?${params}`)});
  const remove=useMutation({mutationFn:(id:string)=>api.request<{deleted_chunks:number}>(`/admin/documents/${id}`,{method:"DELETE"}),onSuccess:(result)=>{setNotice(`文档已删除，共清理 ${result.deleted_chunks} 个向量切片。`);setSelected(null);setConfirmed(false);void queryClient.invalidateQueries({queryKey:["admin-documents"]});}});
  return <div className="admin-page"><PageHeader title="知识库管理" copy="查看索引状态并清理文档与向量切片" />{notice&&<div className="alert success">{notice}</div>}<section className="panel"><div className="filters"><label className="search-field"><Search size={17}/><input placeholder="搜索文件名" value={query} onChange={(e)=>setQuery(e.target.value)}/></label><select value={status} onChange={(e)=>setStatus(e.target.value)}><option value="">全部状态</option><option>processing</option><option>completed</option><option>failed</option></select></div>
    {documents.isLoading?<PanelLoader/>:documents.error?<ErrorPanel error={documents.error}/>:!documents.data?.items.length?<EmptyState icon={<BookOpen/>}>没有符合条件的文档</EmptyState>:<><div className="table-meta">共 {documents.data.total} 个文档</div><div className="table-wrap"><table><thead><tr><th>文件名</th><th>用户</th><th>状态</th><th>切片数</th><th>上传时间</th><th></th></tr></thead><tbody>{documents.data.items.map((item)=><tr key={item.id}><td><b>{item.file_name}</b>{item.error_message&&<small className="error-copy">{item.error_message}</small>}</td><td><b>{item.username}</b><small>{item.email}</small></td><td><span className={`doc-status ${item.status}`}>{item.status}</span></td><td>{item.chunk_count}</td><td>{formatDate(item.uploaded_at)}</td><td><button className="icon-action" onClick={()=>{setSelected(item);setConfirmed(false);}} aria-label={`删除 ${item.file_name}`}><Trash2 size={16}/></button></td></tr>)}</tbody></table></div></>}
  </section>{selected&&<div className="dialog-backdrop"><section className="dialog compact" role="dialog" aria-modal="true"><header><div><h2>删除文档</h2><p>{selected.file_name}</p></div><button onClick={()=>setSelected(null)}><X/></button></header><p>该操作会同时删除数据库记录与所有向量切片，无法撤销。</p><label className="confirm-row"><input type="checkbox" checked={confirmed} onChange={(e)=>setConfirmed(e.target.checked)}/>我确认删除该文档及向量数据</label>{remove.error&&<div className="alert error">{message(remove.error)}</div>}<Button className="danger" disabled={!confirmed||remove.isPending} onClick={()=>remove.mutate(selected.id)}>{remove.isPending?"正在删除...":"确认删除"}</Button></section></div>}</div>;
}

function AuditPage() {
  const { api }=useAdminAuth(); const [query,setQuery]=useState(""); const params=new URLSearchParams({page_size:"100"});if(query)params.set("q",query);
  const logs=useQuery({queryKey:["admin-audit",query],queryFn:()=>api.request<Paginated<AuditLog>>(`/admin/audit-logs?${params}`)});
  return <div className="admin-page"><PageHeader title="操作审计" copy="追踪管理员对账号和知识库的关键操作"/><section className="panel"><div className="filters"><label className="search-field"><Search size={17}/><input placeholder="搜索操作或对象 ID" value={query} onChange={(e)=>setQuery(e.target.value)}/></label></div>{logs.isLoading?<PanelLoader/>:logs.error?<ErrorPanel error={logs.error}/>:<><div className="table-meta">共 {logs.data?.total||0} 条记录</div><div className="table-wrap"><table><thead><tr><th>时间</th><th>操作人</th><th>动作</th><th>对象</th><th>IP</th><th>变更</th></tr></thead><tbody>{logs.data?.items.map((log)=><tr key={log.id}><td>{formatDate(log.created_at)}</td><td>{log.actor}</td><td><code>{log.action}</code></td><td>{log.target_type} / {log.target_id||"—"}</td><td>{log.ip_address||"—"}</td><td><details><summary>查看</summary><pre>{JSON.stringify({before:log.before_data,after:log.after_data},null,2)}</pre></details></td></tr>)}</tbody></table></div></>}</section></div>;
}

function PanelLoader(){return <div className="panel-loader"><Spinner label="加载数据"/></div>}
function ErrorPanel({error}:{error:unknown}){return <div className="alert error">{message(error,"加载数据失败。")}</div>}
function formatDate(value:string|null){if(!value)return "—";const date=new Date(value);return Number.isNaN(date.getTime())?value:date.toLocaleString("zh-CN",{hour12:false});}
