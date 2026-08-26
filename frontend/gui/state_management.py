import json
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from urllib.parse import unquote
from uuid import UUID

import api_utils
import extra_streamlit_components as stx
import streamlit as st
from config import settings

MODEL_NAMES = settings.model_names or ["gpt-4o-mini"]
LOGIN_COOKIE_NAME = "zhishu_login"
LOGIN_DURATION = timedelta(hours=1)


class Page(StrEnum):
    HOME = "home"
    LOGIN = "login"
    REGISTER = "register"


class User:
    def __init__(
        self,
        is_authenticated: bool = False,
        username: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        threads: list[dict] | None = None,
    ):
        self.is_authenticated = is_authenticated
        self.username = username
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.threads = threads or []


class Thread:
    def __init__(
        self,
        id: UUID | None = None,
        title: str = "",
        user_id: UUID | None = None,
        messages: list | None = None,
        documents: list | None = None,
    ):
        self.id = id
        self.user_id = user_id
        self.title = title
        self.messages = messages or []
        self.documents = documents or []


def _cookie_manager():
    """Return the single Cookie component created for the current script run."""
    return st.session_state["_zhishu_cookie_manager_instance"]


def _restore_saved_login() -> None:
    """Restore an unexpired browser session after a Streamlit page reload."""
    if st.session_state["user"].is_authenticated:
        return

    # On a hard refresh, Streamlit exposes request cookies synchronously.
    # Prefer that value so the authenticated UI can be restored on the first
    # render; keep CookieManager as a fallback for component-triggered reruns.
    cookie_value = st.context.cookies.get(LOGIN_COOKIE_NAME) or _cookie_manager().get(LOGIN_COOKIE_NAME)
    if not cookie_value:
        return

    try:
        # universal-cookie automatically JSON-decodes object-shaped cookie
        # values.  Depending on component timing/version, CookieManager.get()
        # therefore returns either the original JSON string or a dict.
        if isinstance(cookie_value, str):
            # st.context.cookies exposes the request's URL-encoded cookie
            # value, while CookieManager returns a decoded value.
            saved_login = json.loads(unquote(cookie_value))
        elif isinstance(cookie_value, dict):
            saved_login = cookie_value
        else:
            raise TypeError("Unsupported saved-login cookie value")

        expires_at = datetime.fromisoformat(saved_login["expires_at"])
        if expires_at <= datetime.now(timezone.utc):
            raise ValueError("Saved login has expired")

        restored_user = User(
            is_authenticated=True,
            username=saved_login["username"],
            access_token=saved_login["access_token"],
            refresh_token=saved_login.get("refresh_token"),
        )
        threads = api_utils.get_user_threads(access_token=restored_user.access_token)
        if not isinstance(threads, list):
            raise ValueError("Saved access token is no longer valid")

        restored_user.threads = threads
        st.session_state["user"] = restored_user
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        clear_saved_login()


def save_login(login_response: dict) -> None:
    """Persist the signed-in session in the browser for exactly one hour."""
    expires_at = datetime.now(timezone.utc) + LOGIN_DURATION
    saved_login = {
        "username": login_response.get("user", {}).get("username"),
        "access_token": login_response.get("access_token"),
        "refresh_token": login_response.get("refresh_token"),
        "expires_at": expires_at.isoformat(),
    }
    _cookie_manager().set(LOGIN_COOKIE_NAME, json.dumps(saved_login), expires_at=expires_at)


def clear_saved_login() -> None:
    try:
        _cookie_manager().delete(LOGIN_COOKIE_NAME)
    except KeyError:
        # CookieManager.delete() removes the browser cookie first and then
        # unconditionally deletes its own cache entry.  Request cookies read
        # through st.context are not always present in that component cache.
        pass


def initialize_state() -> None:
    if "page" not in st.session_state:
        st.session_state["page"] = Page.HOME

    if "user" not in st.session_state:
        st.session_state["user"] = User()

    if "thread" not in st.session_state:
        st.session_state["thread"] = Thread()

    if "model_name" not in st.session_state:
        st.session_state["model_name"] = MODEL_NAMES[0]

    # CookieManager's browser response arrives on a follow-up Streamlit rerun.
    # Instantiate it once *per run* so that rerun can receive the cookie, while
    # save/logout reuse this same object and never create a duplicate element.
    st.session_state["_zhishu_cookie_manager_instance"] = stx.CookieManager(key="zhishu_cookie_manager")

    # CookieManager writes and deletes cookies in the browser after Streamlit
    # has rendered the component.  Calling st.rerun() in the same run cancels
    # that browser-side action, so login/logout queue it for this next, full
    # render instead.
    if st.session_state.get("_logout_in_progress", False):
        if st.session_state.pop("_clear_saved_login", False):
            clear_saved_login()
        # st.context.cookies is a snapshot from when the current Streamlit
        # connection was opened.  It can still contain the deleted cookie on
        # component-triggered reruns, so never restore during this logged-out
        # session.  A successful login explicitly clears this guard.
        return

    if pending_login := st.session_state.pop("_pending_saved_login", None):
        save_login(pending_login)
    else:
        _restore_saved_login()


def new_chat():
    st.session_state["thread"] = Thread()


def update_thread(thread_id: UUID, title: str):
    updated_thread_response = api_utils.update_thread(thread_id, title)
    user_id = updated_thread_response.get("user_id")
    st.session_state["thread"].id = thread_id
    st.session_state["thread"].title = title
    st.session_state["thread"].user_id = user_id


def change_thread(thread_id: UUID) -> None:
    get_thread_response = api_utils.get_thread(thread_id)
    title = get_thread_response.get("title")
    user_id = get_thread_response.get("user_id")

    st.session_state["thread"] = Thread(id=thread_id, title=title, user_id=user_id)  # type: ignore
    update_document_list(thread_id)
    update_chat_history(thread_id)


def update_document_list(thread_id: UUID) -> None:
    documents = []
    documents_response = api_utils.list_document(thread_id)
    if documents_response is None:
        st.sidebar.error("Failed to retrieve document list. Please try again.")
    else:
        documents = documents_response

    st.session_state["thread"].documents = documents


def update_user_threads() -> list[dict]:
    threads = api_utils.get_user_threads()
    st.session_state["user"].threads = threads
    return threads


def update_chat_history(thread_id: UUID) -> None:
    chat_messages = []
    chat_history_response = api_utils.get_chat_history(thread_id)
    if isinstance(chat_history_response, list):
        chat_messages = chat_history_response
    else:
        st.sidebar.error(chat_history_response.get("details", "Failed to retrieve chat history. Please try again."))

    st.session_state["thread"].messages = chat_messages


def authenticate_user(login_response: dict) -> None:
    st.session_state.pop("_logout_in_progress", None)
    st.session_state.pop("_clear_saved_login", None)
    st.session_state["user"] = User(
        is_authenticated=True,
        username=login_response.get("user", {}).get("username"),
        access_token=login_response.get("access_token"),
        refresh_token=login_response.get("refresh_token"),
    )
    st.session_state["_pending_saved_login"] = login_response
    update_user_threads()
    st.session_state["thread"] = Thread()


def logout_user() -> None:
    st.session_state["page"] = Page.HOME
    st.session_state["user"] = User()
    st.session_state["thread"] = Thread()
    st.session_state["model_name"] = MODEL_NAMES[0]
    st.session_state.pop("_pending_saved_login", None)
    st.session_state["_logout_in_progress"] = True
    st.session_state["_clear_saved_login"] = True
