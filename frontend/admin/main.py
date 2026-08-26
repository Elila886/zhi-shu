import os

import requests
import streamlit as st


BASE_URL = os.getenv("BACKEND_BASE_URL", "http://backend:8000/api/v1")
TIMEOUT = 30

st.set_page_config(page_title="企业智能知识助手 · 管理后台", page_icon="🛡️", layout="wide")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;600;700&display=swap');
html, body, [class*="css"] { font-family: 'Noto Sans SC','Microsoft YaHei',sans-serif; }
[data-testid="stSidebar"] { background: #0f172a; }
[data-testid="stSidebar"] * { color: #e2e8f0; }
[data-testid="stMetric"] { border: 1px solid #e2e8f0; border-radius: 12px; padding: 16px; background: white; }
.admin-title { font-size: 1.45rem; font-weight: 700; color: #f8fafc; margin: 4px 0 2px; }
.admin-subtitle { color: #94a3b8; font-size: .82rem; margin-bottom: 22px; }
.status-ok { color: #15803d; font-weight: 600; }
</style>
""", unsafe_allow_html=True)


def api(method: str, path: str, **kwargs):
    headers = kwargs.pop("headers", {})
    if token := st.session_state.get("admin_token"):
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = requests.request(method, f"{BASE_URL}{path}", headers=headers, timeout=TIMEOUT, **kwargs)
        if response.status_code == 401:
            st.session_state.clear()
            st.rerun()
        if not response.ok:
            detail = response.json().get("detail", response.text)
            raise RuntimeError(detail)
        return response.json() if response.content else {}
    except requests.RequestException as exc:
        raise RuntimeError(f"无法连接后台服务：{exc}") from exc


def login_page():
    left, center, right = st.columns([1, 1.1, 1])
    with center:
        st.write("")
        st.write("")
        st.title("🛡️ 管理后台")
        st.caption("企业智能知识助手 · 仅限授权管理员")
        with st.form("admin-login"):
            email = st.text_input("管理员邮箱")
            password = st.text_input("密码", type="password")
            submitted = st.form_submit_button("登录管理后台", type="primary", use_container_width=True)
        if submitted:
            try:
                response = requests.post(f"{BASE_URL}/auth/login", data={"username": email, "password": password}, timeout=TIMEOUT)
                response.raise_for_status()
                login = response.json()
                token = login["access_token"]
                me = requests.get(f"{BASE_URL}/users/me", headers={"Authorization": f"Bearer {token}"}, timeout=TIMEOUT)
                me.raise_for_status()
                profile = me.json()
                if profile.get("role") not in {"admin", "super_admin"}:
                    st.error("当前账号没有后台管理权限")
                    return
                st.session_state["admin_token"] = token
                st.session_state["admin_profile"] = profile
                st.rerun()
            except requests.HTTPError as exc:
                message = "邮箱或密码错误"
                if exc.response is not None and exc.response.status_code == 403:
                    message = "账号已被禁用或未验证"
                st.error(message)
            except requests.RequestException as exc:
                st.error(f"无法连接后台服务：{exc}")


def overview_page():
    st.title("数据概览")
    data = api("GET", "/admin/overview")
    cols = st.columns(6)
    metrics = [
        ("用户总数", data["users"]), ("30 天活跃", data["active_users_30d"]),
        ("会话总数", data["threads"]), ("文档总数", data["documents"]),
        ("今日新会话", data["today_threads"]), ("失败文档", data["failed_documents"]),
    ]
    for col, (label, value) in zip(cols, metrics):
        col.metric(label, value)
    st.subheader("系统服务状态")
    health = api("GET", "/admin/health")
    health_cols = st.columns(3)
    labels = {"backend": "后端 API", "database": "PostgreSQL", "pgvector": "PGVector"}
    for col, key in zip(health_cols, labels):
        value = health[key]
        col.markdown(f"**{labels[key]}**  \n<span class='status-ok'>{'● 正常' if value == 'healthy' else '● 不可用'}</span>", unsafe_allow_html=True)


def users_page():
    st.title("用户管理")
    c1, c2, c3 = st.columns([2, 1, 1])
    query = c1.text_input("搜索", placeholder="用户名或邮箱")
    role_label = c2.selectbox("角色", ["全部", "user", "admin", "super_admin"])
    active_label = c3.selectbox("状态", ["全部", "启用", "禁用"])
    params = {"q": query or None, "role": None if role_label == "全部" else role_label,
              "active": None if active_label == "全部" else active_label == "启用", "page_size": 100}
    data = api("GET", "/admin/users", params={key: value for key, value in params.items() if value is not None})
    items = data["items"]
    st.caption(f"共 {data['total']} 位用户")
    if not items:
        st.info("没有符合条件的用户")
        return
    st.dataframe([{
        "用户名": item["username"], "邮箱": item["email"], "角色": item["role"],
        "状态": "启用" if item["is_active"] else "禁用", "会话数": item["thread_count"],
        "最近登录": item["last_login_at"] or "—", "注册时间": item["created_at"],
    } for item in items], use_container_width=True, hide_index=True)
    selected_email = st.selectbox("选择要管理的用户", [item["email"] for item in items])
    selected = next(item for item in items if item["email"] == selected_email)
    tab1, tab2 = st.tabs(["账号与角色", "重置密码"])
    with tab1, st.form("edit-user"):
        role = st.selectbox("角色", ["user", "admin", "super_admin"], index=["user", "admin", "super_admin"].index(selected["role"]))
        active = st.toggle("启用账号", value=selected["is_active"])
        reason = st.text_input("禁用原因", value=selected.get("disabled_reason") or "", disabled=active)
        if st.form_submit_button("保存修改", type="primary"):
            try:
                api("PATCH", f"/admin/users/{selected['id']}", json={"role": role, "is_active": active, "disabled_reason": None if active else reason})
                st.success("用户设置已更新")
                st.rerun()
            except RuntimeError as exc:
                st.error(str(exc))
    with tab2, st.form("reset-password"):
        new_password = st.text_input("新密码", type="password", help="8–32 个字符")
        confirm = st.text_input("确认新密码", type="password")
        if st.form_submit_button("确认重置"):
            if new_password != confirm:
                st.error("两次密码输入不一致")
            else:
                try:
                    api("POST", f"/admin/users/{selected['id']}/reset-password", json={"new_password": new_password})
                    st.success("密码已重置")
                except RuntimeError as exc:
                    st.error(str(exc))


def documents_page():
    st.title("知识库管理")
    c1, c2 = st.columns([2, 1])
    query = c1.text_input("搜索文档", placeholder="文件名")
    status_value = c2.selectbox("处理状态", ["全部", "processing", "completed", "failed"])
    params = {"q": query or None, "status": None if status_value == "全部" else status_value, "page_size": 100}
    data = api("GET", "/admin/documents", params={key: value for key, value in params.items() if value is not None})
    items = data["items"]
    st.caption(f"共 {data['total']} 个文档")
    if not items:
        st.info("没有符合条件的文档")
        return
    st.dataframe([{
        "文件名": item["file_name"], "用户": item["email"], "状态": item["status"],
        "切片数": item["chunk_count"], "上传时间": item["uploaded_at"], "失败原因": item["error_message"] or "—",
    } for item in items], use_container_width=True, hide_index=True)
    selected_id = st.selectbox("选择要删除的文档", [item["id"] for item in items], format_func=lambda value: next(item["file_name"] for item in items if item["id"] == value))
    confirm = st.checkbox("我确认同时删除数据库记录与向量切片")
    if st.button("删除文档", type="primary", disabled=not confirm):
        try:
            result = api("DELETE", f"/admin/documents/{selected_id}")
            st.success(f"文档已删除，共清理 {result['deleted_chunks']} 个向量切片")
            st.rerun()
        except RuntimeError as exc:
            st.error(str(exc))


def audit_page():
    st.title("操作审计")
    query = st.text_input("搜索操作或对象 ID")
    data = api("GET", "/admin/audit-logs", params={"q": query, "page_size": 100})
    st.caption(f"共 {data['total']} 条记录")
    st.dataframe([{
        "时间": item["created_at"], "操作人": item["actor"], "动作": item["action"],
        "对象": f"{item['target_type']} / {item['target_id'] or '—'}", "IP": item["ip_address"] or "—",
        "修改前": item["before_data"], "修改后": item["after_data"],
    } for item in data["items"]], use_container_width=True, hide_index=True)


if "admin_token" not in st.session_state:
    login_page()
    st.stop()

profile = st.session_state["admin_profile"]
with st.sidebar:
    st.markdown('<div class="admin-title">企业智能知识助手</div>', unsafe_allow_html=True)
    st.markdown('<div class="admin-subtitle">ADMIN CONSOLE</div>', unsafe_allow_html=True)
    pages = ["数据概览", "用户管理", "知识库管理"]
    if profile["role"] == "super_admin":
        pages.append("操作审计")
    page = st.radio("导航", pages, label_visibility="collapsed")
    st.divider()
    st.caption(profile["email"])
    st.caption("超级管理员" if profile["role"] == "super_admin" else "管理员")
    if st.button("退出登录", use_container_width=True):
        st.session_state.clear()
        st.rerun()

try:
    {"数据概览": overview_page, "用户管理": users_page, "知识库管理": documents_page, "操作审计": audit_page}[page]()
except RuntimeError as exc:
    st.error(str(exc))
