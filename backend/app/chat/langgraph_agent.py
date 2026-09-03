from fastapi import HTTPException

from app.config import settings
from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
import json
from typing import Any, Sequence

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from .prompts import SYSTEM_PROMPT
from .tools import query_personnel, tools


def create_model(model_name: str, streaming: bool = False) -> BaseChatModel:
    """Create a retrieval chain based on the provided model name."""

    if model_name == "deepseek":
        if settings.deepseek_api_key is None:
            raise HTTPException(
                status_code=500,
                detail="DEEPSEEK_API_KEY is not configured. Add it to the .env file and restart the containers.",
            )
        return init_chat_model(
            model="deepseek-chat",
            model_provider="openai",
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            streaming=streaming,
        )

    model = init_chat_model(
        model=model_name,
        model_provider=settings.model_provider,
        api_key=settings.api_key,
        base_url=settings.model_base_url or None,
        streaming=streaming,
    )

    return model


def build_retrival_graph(
    checkpointer: BaseCheckpointSaver,
    model_name: str,
    model_override: BaseChatModel | None = None,
    tool_override: Sequence[Any] | None = None,
) -> CompiledStateGraph:
    """Build the explicit Agent/Tool graph used by chat and HITL leave requests."""
    model = model_override or create_model(model_name=model_name)
    graph_tools = list(tool_override or tools)
    bound_model = model.bind_tools(graph_tools)

    async def agent_node(state: MessagesState) -> dict[str, list[BaseMessage]]:
        response = await bound_model.ainvoke([SystemMessage(content=SYSTEM_PROMPT), *state["messages"]])
        return {"messages": [response]}

    def route_agent(state: MessagesState) -> str:
        last = state["messages"][-1]
        tool_calls = getattr(last, "tool_calls", None) or []
        # Personnel lookups are terminal and deliberately isolated from the
        # general-purpose tool node.  This prevents a model from pairing a
        # directory lookup with RAG or web search in the same turn.
        if any(isinstance(call, dict) and call.get("name") == "query_personnel" for call in tool_calls):
            return "personnel_tools"
        return "tools" if tool_calls else END

    async def personnel_tools_node(state: MessagesState, config: RunnableConfig) -> dict[str, list[ToolMessage]]:
        last = state["messages"][-1]
        messages: list[ToolMessage] = []
        for call in getattr(last, "tool_calls", None) or []:
            if not isinstance(call, dict) or call.get("name") != "query_personnel":
                continue
            result = await query_personnel.ainvoke(call.get("args") or {}, config=config)
            messages.append(
                ToolMessage(
                    content=str(result),
                    name="query_personnel",
                    tool_call_id=str(call.get("id") or "personnel-query"),
                )
            )
        return {"messages": messages}

    def route_tools(state: MessagesState) -> str:
        for message in reversed(state["messages"]):
            if isinstance(message, ToolMessage) and message.name == "query_personnel":
                return "personnel_final"
            if isinstance(message, ToolMessage) and message.name == "start_leave_request":
                try:
                    payload = json.loads(str(message.content))
                except json.JSONDecodeError:
                    break
                if payload.get("event") == "leave_final":
                    return "leave_final"
                break
        return "agent"

    def leave_final(state: MessagesState) -> dict[str, list[AIMessage]]:
        payload: dict[str, Any] = {}
        for message in reversed(state["messages"]):
            if isinstance(message, ToolMessage) and message.name == "start_leave_request":
                try:
                    payload = json.loads(str(message.content))
                except json.JSONDecodeError:
                    pass
                break
        labels = {"approved": "已批准", "rejected": "已拒绝", "cancelled": "已取消"}
        label = labels.get(str(payload.get("status")), "已结束")
        return {"messages": [AIMessage(content=f"你的请假流程{label}。请在通知中心查看详细审批结果。")]}

    def personnel_final(state: MessagesState) -> dict[str, list[AIMessage]]:
        payload: dict[str, Any] = {}
        for message in reversed(state["messages"]):
            if isinstance(message, ToolMessage) and message.name == "query_personnel":
                try:
                    payload = json.loads(str(message.content))
                except json.JSONDecodeError:
                    pass
                break

        status = payload.get("status")
        if status == "forbidden":
            content = "你没有查询人员基本信息的权限。"
        elif status == "not_found":
            content = "未找到匹配的人员基本信息，请确认姓名后再试。"
        elif status == "invalid_request":
            content = "请提供需要查询的人员完整姓名。"
        elif status == "ambiguous":
            candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
            rows = [
                f"- 工号：{' '.join(str(item.get('employee_no', '')).split())}｜部门：{' '.join(str(item.get('department', '')).split())}"
                for item in candidates
                if isinstance(item, dict)
            ]
            content = "存在重名人员，请补充工号或部门。"
            if rows:
                content += "\n候选：\n" + "\n".join(rows)
        elif status == "found" and isinstance(payload.get("profile"), dict):
            profile = payload["profile"]
            safe = lambda key, fallback="未录入": " ".join(str(profile.get(key) or fallback).split())
            employment_status = "在职" if profile.get("employment_status") == "active" else "离职"
            content = "\n".join((
                "查询结果：",
                f"- 姓名：{safe('full_name')}",
                f"- 工号：{safe('employee_no')}",
                f"- 部门：{safe('department')}",
                f"- 职位：{safe('job_title')}",
                f"- 工作邮箱：{safe('work_email')}",
                f"- 工作电话：{safe('work_phone')}",
                f"- 在职状态：{employment_status}",
            ))
        else:
            content = "人员查询未能完成，请稍后重试。"
        return {"messages": [AIMessage(content=content)]}

    graph = StateGraph(MessagesState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", ToolNode(graph_tools))
    graph.add_node("personnel_tools", personnel_tools_node)
    graph.add_node("leave_final", leave_final)
    graph.add_node("personnel_final", personnel_final)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route_agent, {"tools": "tools", "personnel_tools": "personnel_tools", END: END})
    graph.add_conditional_edges("tools", route_tools, {"agent": "agent", "leave_final": "leave_final", "personnel_final": "personnel_final"})
    graph.add_edge("personnel_tools", "personnel_final")
    graph.add_edge("leave_final", END)
    graph.add_edge("personnel_final", END)
    return graph.compile(checkpointer=checkpointer)
