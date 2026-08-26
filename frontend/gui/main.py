import config  # need to import for config initialization # noqa: F401
import streamlit as st
from pages import home_page, login_page, register_page
from sidebar import display_sidebar
from state_management import Page, initialize_state

st.set_page_config(page_title="知枢 · 企业智能知识助手", layout="wide", page_icon="✦", initial_sidebar_state="expanded")


def render_global_styles():
    """Apply the product visual language in one place."""
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;600;700&display=swap');
        :root { --ink: #102a43; --muted: #6b7d90; --brand: #117c88; --brand-dark: #07545f; --mint: #e8f6f4; }
        .stApp, .stApp * { font-family: "Noto Sans SC", "Microsoft YaHei", "PingFang SC", sans-serif; }
        .stApp { background: #f5f8fa; color: var(--ink); }
        [data-testid="stAppViewContainer"] > .main { background: radial-gradient(circle at 75% -15%, #d9f3f0 0, transparent 28rem), #f5f8fa; }
        [data-testid="stMainBlockContainer"] { max-width: 1120px; padding-top: 3.2rem; }
        [data-testid="stSidebar"] { background: linear-gradient(180deg, #092d3a 0%, #0a3d4a 100%); }
        [data-testid="stSidebar"] * { color: #e8f1f3; }
        [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {
            color: #ffffff !important;
            letter-spacing: -.02em;
        }
        [data-testid="stSidebar"] [data-testid="stCaptionContainer"],
        [data-testid="stSidebar"] [data-testid="stCaptionContainer"] * { color: #a9c9cd !important; }
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p { color: #bdcdd2; }
        [data-testid="stSidebar"] button { border-color: rgba(224, 245, 243, .2); color: #e8f1f3 !important; }
        [data-testid="stSidebar"] button:hover { border-color: #71c8c2; background: rgba(255,255,255,.08); color: #ffffff !important; }
        [data-testid="stSidebar"] [data-testid="stBaseButton-secondary"] { background: rgba(255,255,255,.07); }
        [data-testid="stSidebar"] [data-testid="stBaseButton-secondary"] p { color: #e8f1f3 !important; }
        [data-testid="stSidebar"] [data-testid="stSelectbox"] [data-baseweb="select"] > div {
            min-height: 2.85rem;
            background: rgba(255,255,255,.08) !important;
            border: 1px solid rgba(173, 218, 216, .35) !important;
            border-radius: .7rem !important;
            box-shadow: none !important;
        }
        [data-testid="stSidebar"] [data-testid="stSelectbox"] [data-baseweb="select"] > div:hover {
            border-color: #79cbc5 !important;
            background: rgba(255,255,255,.12) !important;
        }
        [data-testid="stSidebar"] [data-testid="stSelectbox"] [data-baseweb="select"] input,
        [data-testid="stSidebar"] [data-testid="stSelectbox"] [data-baseweb="select"] span,
        [data-testid="stSidebar"] [data-testid="stSelectbox"] [data-baseweb="select"] svg { color: #f3fbfb !important; -webkit-text-fill-color: #f3fbfb !important; fill: #b9dcda !important; }
        h1, h2, h3 { color: var(--ink) !important; letter-spacing: -.03em; }
        .hero { padding: 2rem 2.25rem; border: 1px solid #dbe7e8; border-radius: 1.35rem; background: rgba(255,255,255,.86); box-shadow: 0 15px 35px rgba(23, 57, 74, .07); margin-bottom: 1.75rem; }
        .eyebrow { color: var(--brand); font-size: .78rem; letter-spacing: .12em; font-weight: 700; margin-bottom: .6rem; }
        .hero h1 { margin: 0 0 .55rem; font-size: 2.05rem; }
        .hero p { color: var(--muted); font-size: 1rem; margin: 0; line-height: 1.8; }
        .feature-card { box-sizing: border-box; height: 184px; padding: 1.2rem 1.25rem; border-radius: 1rem; border: 1px solid #dfe9e9; background: rgba(255,255,255,.78); }
        .feature-card b { display: block; color: #123b4b; font-size: 1.02rem; margin: .65rem 0 .35rem; }
        .feature-card span { color: var(--muted); font-size: .88rem; line-height: 1.6; }
        .feature-icon { width: 2.1rem; height: 2.1rem; display: grid; place-items: center; border-radius: .65rem; color: var(--brand); background: var(--mint); font-size: 1.15rem; }
        .login-required { padding: 1.4rem 1.5rem; border: 1px solid #cfe4e2; border-radius: 1rem; background: linear-gradient(135deg, #f9fefd, #edf8f7); color: var(--ink); }
        .login-required strong { display: block; margin-bottom: .35rem; font-size: 1.06rem; }
        .login-required span { color: var(--muted); font-size: .92rem; }
        [data-testid="stChatMessage"] { border: 1px solid #e0eaeb; border-radius: 1rem; background: rgba(255,255,255,.76); padding: .7rem .9rem; }
        [data-testid="stChatInput"] { border-radius: 1rem; border-color: #cadcdc; background: #fff; box-shadow: 0 8px 25px rgba(22, 64, 78, .1); }
        [data-testid="stChatInput"] textarea { color: var(--ink); }
        .stButton > button[kind="primary"], .stFormSubmitButton > button { background: var(--brand); border-color: var(--brand); color: white; border-radius: .65rem; }
        .stButton > button[kind="primary"]:hover, .stFormSubmitButton > button:hover { background: var(--brand-dark); border-color: var(--brand-dark); color: white; }
        [data-testid="stFileUploader"] { border: 1px dashed #8cbeb9; border-radius: .85rem; background: #f7fcfb; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def main():
    initialize_state()
    render_global_styles()
    display_sidebar()
    if st.session_state["page"] == Page.HOME:
        home_page()
    elif st.session_state["page"] == Page.LOGIN:
        login_page()
    elif st.session_state["page"] == Page.REGISTER:
        register_page()


if __name__ == "__main__":
    main()
