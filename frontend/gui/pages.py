import api_utils
import streamlit as st
from chat_components import authenticated_user_chat_interface_component
from state_management import Page, authenticate_user


def home_page():
    st.markdown(
        """
        <section class="hero">
          <div class="eyebrow">KNOWLEDGE, MADE ACTIONABLE</div>
          <h1>企业智能知识助手</h1>
          <p>连接制度文档、项目资料与团队经验，让每一次提问都得到有依据、可追溯的答案。</p>
        </section>
        """,
        unsafe_allow_html=True,
    )
    if not st.session_state["user"].is_authenticated:
        c1, c2, c3 = st.columns(3)
        cards = [("⌕", "知识精准检索", "从业务材料中提取与问题最相关的信息。"), ("▣", "多格式资料接入", "支持 PDF、Word 和 TXT，快速沉淀团队知识。"), ("◌", "连续上下文对话", "保留会话脉络，让复杂问题得到连贯回答。")]
        for column, (icon, title, text) in zip((c1, c2, c3), cards):
            column.markdown(f'<div class="feature-card"><div class="feature-icon">{icon}</div><b>{title}</b><span>{text}</span></div>', unsafe_allow_html=True)
    else:
        st.subheader("今天想查找什么？", divider="gray")
        authenticated_user_chat_interface_component()


def login_page():
    st.subheader("登录知识工作台", divider="gray")
    with st.form("login_form"):
        email = st.text_input("邮箱 *")
        password = st.text_input("密码 *", type="password")
        submitted = st.form_submit_button("登录")

    back_to_home_component()

    if submitted:
        with st.spinner("正在登录..."):
            login_response = api_utils.login_user(email, password)
            if message := login_response.get("message"):
                st.success(message)
                st.session_state["page"] = Page.HOME
                authenticate_user(login_response)
                st.rerun()
            else:
                st.error(login_response.get("detail", "Registration failed. Please try again."))


def register_page():
    st.subheader("创建企业知识空间", divider="gray")
    with st.form("register_form"):
        col1, col2 = st.columns(2)
        email = col1.text_input("邮箱 *")
        username = col1.text_input("用户名 *", max_chars=16)
        password = col1.text_input("密码 *", type="password", max_chars=32)
        first_name = col2.text_input("名字", max_chars=50)
        last_name = col2.text_input("姓氏", max_chars=50)

        submitted = st.form_submit_button("注册")

    back_to_home_component()

    if submitted:
        register_data = {
            "email": email,
            "username": username,
            "password": password,
            "first_name": first_name,
            "last_name": last_name,
        }
        with st.spinner("正在创建账号..."):
            register_response = api_utils.register_user(register_data)
            if message := register_response.get("message"):
                st.success(message)
                st.session_state["page"] = Page.LOGIN
                st.rerun()
            else:
                st.error(register_response.get("detail", "Registration failed. Please try again."))


def back_to_home_component():
    if st.button("← 返回首页", type="tertiary"):
        st.session_state["page"] = Page.HOME
        st.rerun()
