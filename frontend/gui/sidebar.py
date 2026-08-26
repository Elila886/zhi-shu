import api_utils
import streamlit as st
from config import settings
from state_management import Page, change_thread, logout_user, new_chat, update_document_list, update_user_threads


def display_sidebar():
    _render_sidebar_styles()
    st.sidebar.markdown("## ✦ 知枢")
    st.sidebar.caption("企业智能知识助手")
    if st.session_state["user"].is_authenticated:
        greeting_component()
        model_selection_component()
        chat_history_component()
        document_list_component()
        logout_component()
    else:
        authentication_component()


def authentication_component():
    st.sidebar.subheader("账号与权限")
    col1, col2 = st.sidebar.columns(2)
    if col1.button("登录"):
        st.session_state["page"] = Page.LOGIN
    if col2.button("注册"):
        st.session_state["page"] = Page.REGISTER
    st.sidebar.markdown("登录后可创建专属知识空间，并管理文件与会话记录。")


def greeting_component():
    st.sidebar.subheader(f"你好，{st.session_state['user'].username}")
    if st.sidebar.button("＋ 新建知识问答", use_container_width=True):
        new_chat()


def model_selection_component():
    st.sidebar.subheader("模型选择")
    st.session_state["model_name"] = st.sidebar.selectbox(
        "选择模型",
        options=settings.model_names,
        key="model",
        label_visibility="collapsed",
    )


def chat_history_component():
    st.sidebar.markdown('<p class="history-heading">最近</p>', unsafe_allow_html=True)
    threads = st.session_state["user"].threads
    menu_thread_id = st.session_state.get("history_menu_thread_id")

    if threads:
        for thread in threads:
            thread_id = str(thread["id"])
            is_active = str(st.session_state["thread"].id) == thread_id
            title = thread["title"] or "New Chat"
            title_col, action_col = st.sidebar.columns([0.86, 0.14], vertical_alignment="center")

            with title_col:
                if st.button(
                    title,
                    key=f"history_open_{thread_id}",
                    type="primary" if is_active else "secondary",
                    use_container_width=True,
                ):
                    st.session_state.pop("history_menu_thread_id", None)
                    change_thread(thread["id"])
                    st.rerun()

            with action_col:
                if menu_thread_id == thread_id:
                    if st.button("删除", key=f"history_delete_{thread_id}", use_container_width=True):
                        with st.spinner(""):
                            delete_response = api_utils.delete_thread(thread["id"])
                            if delete_response.get("status") == "ok":
                                st.session_state.pop("history_menu_thread_id", None)
                                update_user_threads()
                                new_chat()
                                st.rerun()
                            else:
                                st.sidebar.error("Failed to delete the thread")
                elif st.button("⋯", key=f"history_more_{thread_id}", use_container_width=True):
                    st.session_state["history_menu_thread_id"] = thread_id
                    st.rerun()


def _render_sidebar_styles():
    """Keep history rows visually compact without changing other sidebar widgets."""
    st.sidebar.markdown(
        """
        <style>
        div[data-testid="stSidebar"] [class*="st-key-history_open_"] button {
            min-height: 2.4rem;
            border: 0;
            border-radius: 0.7rem;
            background: transparent;
            color: #d9e7e9;
            justify-content: flex-start;
            padding: 0.35rem 0.65rem;
            font-size: 1rem;
            font-weight: 400;
            text-align: left;
            overflow: hidden;
            box-shadow: none;
            transition: background 0.15s ease;
        }
        div[data-testid="stSidebar"] [class*="st-key-history_open_"] button:hover {
            background: rgba(255,255,255,.1);
            color: #ffffff;
            border: 0;
        }
        div[data-testid="stSidebar"] [class*="st-key-history_open_"] [data-testid="stBaseButton-primary"] {
            background: rgba(113,200,194,.2);
            color: #ffffff;
            border: 0;
        }
        div[data-testid="stSidebar"] [class*="st-key-history_open_"] button p {
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        div[data-testid="stSidebar"] [class*="st-key-history_more_"] button {
            min-height: 2.4rem;
            border: 0;
            background: transparent;
            color: transparent;
            font-size: 1.2rem;
            padding: 0;
            transition: color 0.15s ease, background 0.15s ease;
        }
        div[data-testid="stSidebar"] [class*="st-key-history_more_"]:hover button,
        div[data-testid="stSidebar"] [class*="st-key-history_more_"] button:focus {
            color: #b9dcda;
            background: rgba(255,255,255,.1);
        }
        div[data-testid="stSidebar"] [class*="st-key-history_delete_"] button {
            min-height: 2.35rem;
            border-radius: 0.5rem;
            font-size: 0.82rem;
            padding: 0.2rem;
        }
        div[data-testid="stSidebar"] .history-heading {
            color: #9fc5c8;
            font-size: 0.9rem;
            font-weight: 600;
            margin: 1.15rem 0 0.35rem 0.15rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def document_list_component():
    st.sidebar.subheader("本会话知识库")
    documents = st.session_state["thread"].documents
    if documents:
        for number, doc in enumerate(documents, start=1):
            col1, col2 = st.sidebar.columns([0.85, 0.15])
            with col1:
                st.markdown(f"{number}. {doc['file_name']}")
            with col2:
                if st.button("❌", key=f"delete_{doc['id']}"):
                    with st.spinner(""):
                        delete_response = api_utils.delete_document(doc["id"])
                        if delete_response:
                            success_message = f"Document with ID {doc['id']} deleted successfully."
                            st.sidebar.success(success_message)
                            update_document_list(st.session_state["thread"].id)
                            st.rerun()
                        else:
                            st.sidebar.error("Failed to delete the document")
    else:
        st.sidebar.caption("还没有上传文件。可在对话框中添加 PDF、Word 或 TXT 文档。")


def logout_component():
    st.sidebar.divider()
    if st.sidebar.button("退出登录", use_container_width=True):
        logout_user()
        st.rerun()
