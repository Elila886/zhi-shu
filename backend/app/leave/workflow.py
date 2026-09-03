from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from loguru import logger
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from sqlalchemy import select

from app.chat.langgraph_agent import build_retrival_graph
from app.config import settings
from app.db.checkpointer import get_checkpointer
from app.db.main import async_session
from app.db.models import LeaveRequest


async def resume_request(request_id: UUID, action: str) -> bool:
    """Resume a committed business transition. Database state remains authoritative on failure."""
    async with async_session() as session:
        request = await session.get(LeaveRequest, request_id)
        if request is None:
            return True
        if request.resume_status == "completed":
            return True
        config = RunnableConfig(configurable={
            "thread_id": str(request.chat_thread_id), "user_id": str(request.requester_id),
            "model_name": request.model_name or settings.model_names[0],
            "leave_run_id": request.workflow_key.split(":", 2)[1],
        })
    try:
        graph = build_retrival_graph(await get_checkpointer(), request.model_name or settings.model_names[0])
        await graph.ainvoke(Command(resume={"action": action, "request_id": str(request_id)}), config=config)
    except Exception as exc:
        logger.exception("Leave workflow resume failed for {}: {}", request_id, exc)
        async with async_session() as session:
            item = await session.get(LeaveRequest, request_id)
            if item:
                item.resume_status = "resume_pending"
                item.resume_attempts += 1
                item.resume_error = str(exc)[:2000]
                await session.commit()
        return False
    async with async_session() as session:
        item = await session.get(LeaveRequest, request_id)
        if item:
            item.resume_status = "waiting" if action == "confirmed" and item.status == "pending_approval" else "completed"
            item.resume_attempts += 1
            item.resume_error = None
            await session.commit()
    return True


async def expire_and_resume() -> None:
    from .service import expire_drafts

    async with async_session() as session:
        expired = await expire_drafts(session)
    for request_id in expired:
        await resume_request(request_id, "cancelled")


async def retry_pending() -> None:
    async with async_session() as session:
        requests = list((await session.execute(
            select(LeaveRequest)
            .where(LeaveRequest.resume_status == "resume_pending")
            .order_by(LeaveRequest.updated_at.asc())
            .limit(20)
        )).scalars())
    for request in requests:
        if request.updated_at is None:
            continue
        delay_seconds = min(30 * (2 ** max(request.resume_attempts, 0)), 1800)
        # Database timestamps are UTC (naive in legacy deployments).  This
        # deliberately does not use business timezone, which applies only to
        # leave dates and annual balance selection.
        if request.updated_at + timedelta(seconds=delay_seconds) <= datetime.utcnow():
            await retry_request(request.id)


async def retry_request(request_id: UUID, force: bool = False) -> bool:
    async with async_session() as session:
        request = await session.get(LeaveRequest, request_id)
        if request is None:
            return False
        if request.resume_status != "resume_pending" and not force:
            return request.resume_status == "completed"
        action = "confirmed" if request.status == "pending_approval" else ("cancelled" if request.status == "cancelled" else "decided")
    return await resume_request(request_id, action)
