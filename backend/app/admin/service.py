from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.utils import hash_password
from app.auth.session_service import revoke_user_sessions
from app.db.models import AuditLog, Document, Thread, User


def _json_value(value: Any) -> Any:
    if isinstance(value, (UUID, datetime)):
        return str(value)
    return value


def _snapshot(user: User) -> dict:
    return {
        "id": str(user.id), "email": user.email, "role": user.role,
        "is_active": user.is_active, "disabled_reason": user.disabled_reason,
    }


def add_audit(session: AsyncSession, actor: User, action: str, target_type: str, target_id: str | None,
              before: dict | None = None, after: dict | None = None, ip_address: str | None = None) -> None:
    session.add(AuditLog(actor_id=actor.id, action=action, target_type=target_type, target_id=target_id,
                         before_data=before, after_data=after, ip_address=ip_address))


async def overview(session: AsyncSession) -> dict:
    # User and thread timestamps are stored as UTC without timezone metadata
    # in existing deployments, so comparison bounds must be naive as well.
    now = datetime.now(UTC).replace(tzinfo=None)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now - timedelta(days=30)

    async def count(model, *conditions) -> int:
        return int((await session.scalar(select(func.count()).select_from(model).where(*conditions))) or 0)

    return {
        "users": await count(User), "active_users_30d": await count(User, User.last_login_at >= month_start),
        "threads": await count(Thread), "documents": await count(Document),
        "today_threads": await count(Thread, Thread.created_at >= day_start),
        "failed_documents": await count(Document, Document.status == "failed"), "generated_at": now.isoformat(),
    }


async def list_users(session: AsyncSession, page: int, page_size: int, query: str | None,
                     role: str | None, active: bool | None) -> dict:
    filters = []
    if query:
        term = f"%{query.strip()}%"
        filters.append(or_(User.username.ilike(term), User.email.ilike(term)))
    if role:
        filters.append(User.role == role)
    if active is not None:
        filters.append(User.is_active == active)
    total = int((await session.scalar(select(func.count()).select_from(User).where(*filters))) or 0)
    statement = select(User).where(*filters).order_by(desc(User.created_at)).offset((page - 1) * page_size).limit(page_size)
    users = (await session.execute(statement)).scalars().all()
    thread_counts = dict((await session.execute(select(Thread.user_id, func.count(Thread.id)).group_by(Thread.user_id))).all())
    rows = []
    for user in users:
        row = {column.name: _json_value(getattr(user, column.name)) for column in User.__table__.columns if column.name != "password_hash"}
        row["thread_count"] = int(thread_counts.get(user.id, 0))
        rows.append(row)
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


async def update_user(session: AsyncSession, actor: User, user_id: UUID, values: dict, ip_address: str | None) -> User:
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if target.id == actor.id and values.get("is_active") is False:
        raise HTTPException(status_code=400, detail="不能禁用当前登录账号")
    role_change = "role" in values and values["role"] != target.role
    if (target.role in {"admin", "super_admin"} or role_change) and actor.role != "super_admin":
        raise HTTPException(status_code=403, detail="只有超级管理员可以管理管理员角色")
    if target.role == "super_admin" and target.id != actor.id:
        raise HTTPException(status_code=403, detail="不能修改其他超级管理员")
    before = _snapshot(target)
    for key, value in values.items():
        setattr(target, key, value)
    if target.is_active:
        target.disabled_reason = None
    if not target.is_active or role_change:
        await revoke_user_sessions(session, target.id)
    add_audit(session, actor, "user.update", "user", str(target.id), before, _snapshot(target), ip_address)
    await session.commit()
    await session.refresh(target)
    return target


async def reset_password(session: AsyncSession, actor: User, user_id: UUID, password: str, ip_address: str | None) -> None:
    target = await session.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if target.role in {"admin", "super_admin"} and actor.role != "super_admin":
        raise HTTPException(status_code=403, detail="只有超级管理员可以重置管理员密码")
    target.password_hash = hash_password(password)
    await revoke_user_sessions(session, target.id)
    add_audit(session, actor, "user.password_reset", "user", str(target.id), ip_address=ip_address)
    await session.commit()


async def list_documents(session: AsyncSession, page: int, page_size: int, query: str | None,
                         status_value: str | None, user_id: UUID | None) -> dict:
    filters = []
    if query:
        filters.append(Document.file_name.ilike(f"%{query.strip()}%"))
    if status_value:
        filters.append(Document.status == status_value)
    if user_id:
        filters.append(Thread.user_id == user_id)
    base = select(Document, Thread, User).join(Thread, Document.thread_id == Thread.id).join(User, Thread.user_id == User.id).where(*filters)
    total = int((await session.scalar(select(func.count()).select_from(base.subquery()))) or 0)
    rows = (await session.execute(base.order_by(desc(Document.uploaded_at)).offset((page - 1) * page_size).limit(page_size))).all()
    items = [{
        "id": str(document.id), "file_name": document.file_name, "status": document.status,
        "chunk_count": document.chunk_count, "error_message": document.error_message,
        "uploaded_at": str(document.uploaded_at), "thread_id": str(thread.id),
        "user_id": str(user.id), "username": user.username, "email": user.email,
    } for document, thread, user in rows]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


async def list_audits(session: AsyncSession, page: int, page_size: int, query: str | None) -> dict:
    filters = []
    if query:
        term = f"%{query.strip()}%"
        filters.append(or_(AuditLog.action.ilike(term), AuditLog.target_id.ilike(term)))
    total = int((await session.scalar(select(func.count()).select_from(AuditLog).where(*filters))) or 0)
    statement = select(AuditLog, User).outerjoin(User, AuditLog.actor_id == User.id).where(*filters).order_by(desc(AuditLog.created_at)).offset((page - 1) * page_size).limit(page_size)
    rows = (await session.execute(statement)).all()
    items = [{
        "id": str(log.id), "actor": user.email if user else "system", "action": log.action,
        "target_type": log.target_type, "target_id": log.target_id, "before_data": log.before_data,
        "after_data": log.after_data, "ip_address": log.ip_address, "created_at": str(log.created_at),
    } for log, user in rows]
    return {"items": items, "total": total, "page": page, "page_size": page_size}
