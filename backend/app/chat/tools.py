import json
from hashlib import sha256
from datetime import date

from app.config import settings
from app.chat.hybrid_retrieval import retrieve_hybrid_documents
from app.db.main import async_session
from app.leave import service as leave_service
from app.personnel import service as personnel_service
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.types import interrupt
from loguru import logger

tavily = TavilySearchResults(
    tavily_api_key=settings.tavily_api_key,
    max_results=3,
    include_answer=False,
    include_raw_content=False,
    include_images=False,
)


@tool
async def retrieve_user_documents(query: str, config: RunnableConfig) -> str:
    """
    Use this tool to answer questions about the user's uploaded documents.
    It will automatically retrieve documents relevant to the current user and thread.
    """
    # 用户信息，文档信息
    user_id = config["configurable"].get("user_id")  # type: ignore
    thread_id = config["configurable"].get("thread_id")  # type: ignore
    logger.info(f"Retrieving documents for user_id: {user_id} and thread_id: {thread_id}")

    # Both Dense and BM25 retrieval are constrained to this exact scope.
    result_docs = await retrieve_hybrid_documents(query, user_id, thread_id)

    if not result_docs:
        return "No relevant documents"

    return "\n\n".join([doc.page_content for doc in result_docs])


def _configured_uuid(config: RunnableConfig, key: str) -> str:
    value = config.get("configurable", {}).get(key)
    if not value:
        raise ValueError(f"Missing required workflow context: {key}")
    return str(value)


@tool
async def get_leave_balance(leave_type_code: str, config: RunnableConfig) -> str:
    """Query the current employee's authoritative annual leave balance by leave type code or name.

    Always call this before creating a leave request. Never ask the employee for a balance.
    """
    from uuid import UUID

    user_id = UUID(_configured_uuid(config, "user_id"))
    async with async_session() as session:
        types = await leave_service.list_types(session)
        leave_type = next((item for item in types if item.code == leave_type_code or item.name == leave_type_code), None)
        if leave_type is None:
            return json.dumps({"event": "leave_balance", "found": False, "message": "假期类型不存在或未启用。"}, ensure_ascii=False)
        balances = await leave_service.list_balances(session, user_id, leave_service.business_year())
        balance = next((item for item in balances if item.leave_type_id == leave_type.id), None)
        return json.dumps({"event": "leave_balance", "found": balance is not None, "leave_type": leave_type.model_dump(mode="json"),
            "balance": balance.model_dump(mode="json") if balance else None}, ensure_ascii=False)


@tool
async def start_leave_request(
    leave_type_code: str,
    start_date: str,
    end_date: str,
    reason: str,
    config: RunnableConfig,
    start_period: str = "am",
    end_period: str = "pm",
) -> str:
    """Create a leave draft then pause for employee confirmation and administrator approval.

    Dates must be ISO YYYY-MM-DD. The leave type may be its configured code or name. Call get_leave_balance first; this tool revalidates data on the server.
    """
    from uuid import UUID

    try:
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    except ValueError as exc:
        raise ValueError("日期必须使用 YYYY-MM-DD 格式") from exc
    user_id = UUID(_configured_uuid(config, "user_id"))
    thread_id = UUID(_configured_uuid(config, "thread_id"))
    model_name = str(config.get("configurable", {}).get("model_name") or "")
    leave_run_id = _configured_uuid(config, "leave_run_id")
    workflow_fingerprint = f"{leave_type_code}|{start_date}|{end_date}|{start_period}|{end_period}|{reason.strip()}"
    workflow_key = f"{thread_id}:{leave_run_id}:{sha256(workflow_fingerprint.encode()).hexdigest()}"
    async with async_session() as session:
        request = await leave_service.create_draft(
            session, requester_id=user_id, chat_thread_id=thread_id, workflow_key=workflow_key,
            leave_type_code=leave_type_code, start_date=start, end_date=end, start_period=start_period,
            end_period=end_period, reason=reason, model_name=model_name,
        )
    response = interrupt({"type": "leave_confirmation_required", "request": request.model_dump(mode="json")})
    async with async_session() as session:
        latest = await leave_service._request_for_owner(session, request.id, user_id)
        if latest.status == "cancelled":
            return json.dumps({"event": "leave_final", "status": "cancelled", "request_id": str(latest.id)}, ensure_ascii=False)
        if latest.status != "pending_approval":
            return json.dumps({"event": "leave_final", "status": latest.status, "request_id": str(latest.id)}, ensure_ascii=False)
        interrupt({"type": "leave_approval_required", "request_id": str(latest.id)})
    async with async_session() as session:
        latest = await leave_service._request_for_owner(session, request.id, user_id)
        return json.dumps({"event": "leave_final", "status": latest.status, "request_id": str(latest.id)}, ensure_ascii=False)


@tool
async def query_personnel(
    person_name: str,
    config: RunnableConfig,
    employee_no: str | None = None,
    department: str | None = None,
) -> str:
    """Query one employee's internal work-directory profile by their complete name.

    Use only for a request for another employee's basic information. If more than one
    employee has the same name, ask for their employee number or department. This tool
    enforces the caller's current permission itself; never use another tool as fallback.
    """
    from uuid import UUID

    actor_id = UUID(_configured_uuid(config, "user_id"))
    client_ip = config.get("configurable", {}).get("client_ip")
    async with async_session() as session:
        result = await personnel_service.query_directory(
            session,
            actor_id,
            person_name,
            employee_no=employee_no,
            department=department,
            ip_address=str(client_ip) if client_ip else None,
        )
    return json.dumps(result, ensure_ascii=False)


tools = [retrieve_user_documents, tavily, get_leave_balance, start_leave_request, query_personnel]
