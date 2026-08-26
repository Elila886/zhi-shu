import asyncio
import json

import api_utils
import streamlit as st
from loguru import logger
from state_management import new_chat, update_document_list, update_thread, update_user_threads


def _request_stop_generation() -> None:
    """Callback invoked by Streamlit as soon as the user presses Stop."""
    st.session_state["stop_generation_requested"] = True


def _finish_interrupted_generation() -> None:
    """Persist the partial answer after Streamlit interrupts the running script."""
    if not st.session_state.get("generation_active") or not st.session_state.get("stop_generation_requested"):
        return

    partial_response = st.session_state.get("generation_response", "")
    if partial_response:
        st.session_state["thread"].messages.append({"role": "ai", "content": partial_response})
    st.session_state["generation_active"] = False
    st.session_state["stop_generation_requested"] = False
    st.session_state["generation_response"] = ""
    st.info("已停止生成。已生成的内容已保留。")


def _render_generation_control_styles() -> None:
    """Place the stop control over the native chat input's send-arrow position."""
    st.markdown(
        """
        <style>
        div[data-testid="stAppViewContainer"] [class*="st-key-stop_"] {
            position: fixed;
            right: 4.15rem;
            bottom: 0.88rem;
            z-index: 1000;
        }
        div[data-testid="stAppViewContainer"] [class*="st-key-stop_"] button {
            width: 2.15rem;
            min-width: 2.15rem;
            height: 2.15rem;
            min-height: 2.15rem;
            padding: 0;
            border: 0;
            border-radius: 50%;
            background: #4b5563;
            color: white;
            font-size: 0.76rem;
            line-height: 1;
            box-shadow: 0 1px 3px rgb(15 23 42 / 20%);
        }
        div[data-testid="stAppViewContainer"] [class*="st-key-stop_"] button:hover {
            background: #1f2937;
            color: white;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def authenticated_user_chat_interface_component():
    _render_generation_control_styles()
    _finish_interrupted_generation()
    is_first_message = False
    for message in st.session_state["thread"].messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if prompt := st.chat_input(
        "输入问题，或拖入资料以建立本会话知识库…",
        accept_file="multiple",
        key="prompt",
        file_type=["pdf", "docx", "txt"],
        disabled=st.session_state.get("generation_active", False),
    ):
        if st.session_state["thread"].id is None:
            is_first_message = True
            thread_id = api_utils.create_new_thread().get("id")
            if thread_id is None:
                raise ValueError("Something happend")
            st.session_state["thread"].id = thread_id

        text = prompt.text or ""
        files = prompt.files or []
        if text:
            st.session_state["thread"].messages.append({"role": "human", "content": text})

        if is_first_message:
            update_thread(st.session_state["thread"].id, f"{text[:30]}")
            update_user_threads()

        with st.chat_message("human"):
            st.markdown(text or "*[file upload]*")
            for up in files:
                st.write(f"📂 {up.name}")

        for file in files:
            with st.spinner(f"正在上传 {file.name}…"):
                resp = api_utils.upload_document(st.session_state["thread"].id, file)
                if resp:
                    st.success(f"已上传 {file.name}")
                else:
                    st.error(f"{file.name} 上传失败")

        if files:
            update_document_list(st.session_state["thread"].id)

        # Uploading a file is not a chat request.  In particular, do not send an
        # empty prompt to the agent while embeddings/indexing may still be settling.
        if not text:
            # The sidebar was rendered before this upload. Rerun so the new file
            # is immediately shown in the current session's knowledge base.
            st.rerun()
            return

        with st.chat_message("ai"):
            steps_container = st.container()
            answer_placeholder = st.empty()
            stop_placeholder = st.empty()
            st.session_state["generation_active"] = True
            st.session_state["stop_generation_requested"] = False
            st.session_state["generation_response"] = ""
            stop_placeholder.button(
                "■",
                key="stop_authenticated_generation",
                on_click=_request_stop_generation,
                help="停止生成",
            )

            async def fetch_stream():
                try:
                    chat_data = {"prompt": text, "model_name": st.session_state["model_name"]}
                    async for line in api_utils.chat_stream(chat_data, st.session_state["thread"].id):
                        if st.session_state.get("stop_generation_requested"):
                            return
                        try:
                            event: dict = json.loads(line)
                            event_type = event.get("type")

                            if event_type == "tool_call":
                                with steps_container:
                                    st.markdown(
                                        f"**Tool Call:** Running `{event['name']}` with arguments: `{event['args']}`"
                                    )

                            elif event_type == "tool_result":
                                with steps_container:
                                    with st.expander(f"**Tool Result:** `{event['name']}`", expanded=False):
                                        st.code(event["content"], language="json")

                            elif event_type == "llm_chunk":
                                st.session_state["generation_response"] += event.get("content", "")
                                answer_placeholder.markdown(st.session_state["generation_response"] + "▌")

                            else:
                                logger.warning(f"Unknown event type: {event_type}")
                                st.warning(event)

                        except (json.JSONDecodeError, KeyError) as e:
                            logger.warning(f"Could not parse stream event: {line} - Error: {e}")

                    full_response = st.session_state["generation_response"]
                    answer_placeholder.markdown(full_response)
                    if full_response:
                        st.session_state["thread"].messages.append({"role": "ai", "content": full_response})
                    st.session_state["generation_active"] = False
                    st.session_state["generation_response"] = ""
                    stop_placeholder.empty()

                except Exception as e:
                    st.session_state["generation_active"] = False
                    st.session_state["generation_response"] = ""
                    stop_placeholder.empty()
                    st.error("An error occurred while processing your request.")
                    logger.error(f"Error in fetch_stream: {e}")
                    if is_first_message:
                        api_utils.delete_thread(st.session_state["thread"].id)
                        logger.info(f"Thread {st.session_state['thread'].id} deleted")
                        new_chat()

            with st.spinner("正在检索知识库并生成回答..."):
                asyncio.run(fetch_stream())


def unauthenticated_user_chat_interface_component():
    _render_generation_control_styles()
    _finish_interrupted_generation()
    for message in st.session_state["thread"].messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if prompt := st.chat_input("输入您的问题…", key="prompt", disabled=st.session_state.get("generation_active", False)):
        st.session_state["thread"].messages.append({"role": "human", "content": prompt})

        with st.chat_message("human"):
            st.markdown(prompt)

        with st.chat_message("ai"):
            placeholder = st.empty()
            stop_placeholder = st.empty()
            st.session_state["generation_active"] = True
            st.session_state["stop_generation_requested"] = False
            st.session_state["generation_response"] = ""
            stop_placeholder.button(
                "■",
                key="stop_simple_generation",
                on_click=_request_stop_generation,
                help="停止生成",
            )

            async def fetch_stream():
                try:
                    chat_data = {"prompt": prompt, "model_name": st.session_state["model_name"]}
                    async for line in api_utils.simple_chat_stream(chat_data):
                        if st.session_state.get("stop_generation_requested"):
                            return
                        try:
                            chunk = json.loads(line).get("content")
                            st.session_state["generation_response"] += chunk
                            placeholder.markdown(st.session_state["generation_response"] + "▌")
                        except (json.JSONDecodeError, KeyError) as e:
                            logger.warning(f"Could not parse stream event: {line} - Error: {e}")

                    full_response = st.session_state["generation_response"]
                    placeholder.markdown(full_response)
                    if full_response:
                        st.session_state["thread"].messages.append({"role": "ai", "content": full_response})
                    st.session_state["generation_active"] = False
                    st.session_state["generation_response"] = ""
                    stop_placeholder.empty()
                except Exception:
                    st.session_state["generation_active"] = False
                    st.session_state["generation_response"] = ""
                    stop_placeholder.empty()
                    st.error("An error occurred while processing your request. Please try again.")

            with st.spinner("正在生成回答..."):
                asyncio.run(fetch_stream())
