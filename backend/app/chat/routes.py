from uuid import UUID

from app.auth.dependencies import CurrentUserDep
from app.db.main import SessionDep
from app.traffic_governance.dependencies import AgentRateLimitDep, OrdinaryRateLimitDep
from fastapi import APIRouter, Request

from . import service as chat_service
from app.leave import service as leave_service
from .schemas import ChatStreamResponse, Message, PromptInput
from app.threads import service as thread_service

chat_router = APIRouter()


@chat_router.post("/{thread_id}")
async def chat_stream(
    thread_id: UUID, prompt_input: PromptInput, request: Request, current_user: CurrentUserDep, session: SessionDep, _: AgentRateLimitDep
):
    await thread_service.get_thread(thread_id, current_user.id, session)
    if await leave_service.active_request_for_thread(session, thread_id, current_user.id):
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail="该会话正在等待请假确认或审批，请先完成该流程。")
    return ChatStreamResponse(
        await chat_service.chat_stream(thread_id, prompt_input, current_user.id, request.client.host if request.client else None),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@chat_router.get("/{thread_id}", response_model=list[Message])
async def get_chat_history(thread_id: UUID, current_user: CurrentUserDep, session: SessionDep, _: OrdinaryRateLimitDep):
    await thread_service.get_thread(thread_id, current_user.id, session)
    return await chat_service.get_chat_history(thread_id, current_user.id)
