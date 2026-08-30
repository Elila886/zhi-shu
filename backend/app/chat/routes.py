from uuid import UUID

from app.auth.dependencies import CurrentUserDep
from app.db.main import SessionDep
from fastapi import APIRouter

from . import service as chat_service
from .schemas import ChatStreamResponse, Message, PromptInput
from app.threads import service as thread_service

chat_router = APIRouter()


@chat_router.post("/{thread_id}")
async def chat_stream(
    thread_id: UUID, prompt_input: PromptInput, current_user: CurrentUserDep, session: SessionDep
):
    await thread_service.get_thread(thread_id, current_user.id, session)
    return ChatStreamResponse(
        await chat_service.chat_stream(thread_id, prompt_input, current_user.id),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@chat_router.get("/{thread_id}", response_model=list[Message])
async def get_chat_history(thread_id: UUID, current_user: CurrentUserDep, session: SessionDep):
    await thread_service.get_thread(thread_id, current_user.id, session)
    return await chat_service.get_chat_history(thread_id, current_user.id)
