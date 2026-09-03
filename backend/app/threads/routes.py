from uuid import UUID

from fastapi import APIRouter, Request, status

from app.auth.dependencies import CurrentUserDep
from app.db.checkpointer import get_checkpointer
from app.db.main import SessionDep
from app.traffic_governance.dependencies import OrdinaryRateLimitDep

from . import service as thread_service
from .schemas import ThreadPublic, ThreadUpdate

thread_router = APIRouter()


@thread_router.post("/", response_model=ThreadPublic, status_code=status.HTTP_201_CREATED)
async def create_new_thread(current_user: CurrentUserDep, session: SessionDep, _: OrdinaryRateLimitDep):
    return await thread_service.create_new_thread(current_user.id, session)


@thread_router.get("/", response_model=list[ThreadPublic])
async def get_user_threads(current_user: CurrentUserDep, session: SessionDep, _: OrdinaryRateLimitDep):
    return await thread_service.get_user_threads(current_user.id, session)


@thread_router.get("/{thread_id}", response_model=ThreadPublic)
async def get_thread(thread_id: UUID, current_user: CurrentUserDep, session: SessionDep, _: OrdinaryRateLimitDep):
    return await thread_service.get_thread(thread_id, current_user.id, session)


@thread_router.patch("/{thread_id}", response_model=ThreadPublic)
async def update_thread(thread_data: ThreadUpdate, thread_id: UUID, current_user: CurrentUserDep, session: SessionDep, _: OrdinaryRateLimitDep):
    return await thread_service.update_thread(thread_data, thread_id, current_user.id, session)


@thread_router.delete("/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(request: Request, thread_id: UUID, current_user: CurrentUserDep, session: SessionDep, _: OrdinaryRateLimitDep):
    # Check ownership before initializing the checkpointer.  Besides avoiding
    # unnecessary database work, this keeps unauthorized requests from
    # touching chat state or the LangGraph persistence layer.
    await thread_service.get_thread(thread_id, current_user.id, session)
    checkpointer = await get_checkpointer()
    request.app.state.checkpointer = checkpointer
    return await thread_service.delete_thread(thread_id, current_user.id, session, checkpointer)
