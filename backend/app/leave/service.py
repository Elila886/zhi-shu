from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ApprovalTask, AuditLog, LeaveBalance, LeaveBalanceLedger, LeaveOperationReceipt, LeaveRequest, LeaveType, Notification, Thread, User

from .schemas import ApprovalTaskPage, ApprovalTaskPublic, LeaveBalancePublic, LeaveRequestPublic, LeaveTypePublic, NotificationPage, NotificationPublic

ZERO = Decimal("0.0")
HALF = Decimal("0.5")
DRAFT_TTL = timedelta(seconds=60)
BUSINESS_TZ = ZoneInfo("Asia/Shanghai")


def _now() -> datetime:
    """Persist naive UTC, matching the project's existing timestamp convention."""
    return datetime.now(UTC).replace(tzinfo=None)


def business_year() -> int:
    return datetime.now(BUSINESS_TZ).year


def _forbidden(detail: str = "无权访问该请假资源") -> HTTPException:
    return HTTPException(status_code=403, detail=detail)


def _period(value: str) -> str:
    if value not in {"am", "pm"}:
        raise HTTPException(status_code=422, detail="半天时段必须为 am 或 pm")
    return value


def calculate_duration(start: date, end: date, start_period: str, end_period: str, allow_half_days: bool) -> Decimal:
    _period(start_period); _period(end_period)
    if start.year != end.year:
        raise HTTPException(status_code=422, detail="一次请假必须位于同一自然年度")
    if end < start:
        raise HTTPException(status_code=422, detail="结束日期不能早于开始日期")
    if start.weekday() >= 5 or end.weekday() >= 5:
        raise HTTPException(status_code=422, detail="起止日期必须为工作日")
    if not allow_half_days and (start_period != "am" or end_period != "pm"):
        raise HTTPException(status_code=422, detail="该假期类型不支持半天请假")
    if start == end:
        if start_period == "pm" and end_period == "am":
            raise HTTPException(status_code=422, detail="同日结束时段不能早于开始时段")
        return HALF if start_period == end_period else Decimal("1.0")
    days, cursor = ZERO, start
    while cursor <= end:
        if cursor.weekday() < 5:
            if cursor == start:
                days += Decimal("1.0") if start_period == "am" else HALF
            elif cursor == end:
                days += HALF if end_period == "am" else Decimal("1.0")
            else:
                days += Decimal("1.0")
        cursor += timedelta(days=1)
    if days <= ZERO:
        raise HTTPException(status_code=422, detail="请假范围内没有工作日")
    return days


def _payload_hash(payload: object) -> str:
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def _balance_public(balance: LeaveBalance, leave_type: LeaveType) -> LeaveBalancePublic:
    return LeaveBalancePublic(id=balance.id, leave_type_id=leave_type.id, leave_type_code=leave_type.code, leave_type_name=leave_type.name, year=balance.year, entitled_days=balance.entitled_days, reserved_days=balance.reserved_days, used_days=balance.used_days, remaining_days=balance.entitled_days - balance.reserved_days - balance.used_days, version=balance.version)


async def _type(session: AsyncSession, leave_type_id: UUID) -> LeaveType:
    item = await session.get(LeaveType, leave_type_id)
    if item is None:
        raise HTTPException(status_code=404, detail="假期类型不存在")
    return item


async def _balance(session: AsyncSession, user_id: UUID, leave_type_id: UUID, year: int, lock: bool = False) -> LeaveBalance | None:
    statement = select(LeaveBalance).where(LeaveBalance.user_id == user_id, LeaveBalance.leave_type_id == leave_type_id, LeaveBalance.year == year)
    if lock:
        statement = statement.with_for_update()
    return (await session.execute(statement)).scalar_one_or_none()


async def list_types(session: AsyncSession, active_only: bool = True) -> list[LeaveTypePublic]:
    statement = select(LeaveType).order_by(LeaveType.code)
    if active_only:
        statement = statement.where(LeaveType.is_active.is_(True))
    return [LeaveTypePublic(id=item.id, code=item.code, name=item.name, is_active=item.is_active, allow_half_days=item.allow_half_days) for item in (await session.execute(statement)).scalars()]


async def list_balances(session: AsyncSession, user_id: UUID, year: int) -> list[LeaveBalancePublic]:
    rows = (await session.execute(select(LeaveBalance, LeaveType).join(LeaveType, LeaveType.id == LeaveBalance.leave_type_id).where(LeaveBalance.user_id == user_id, LeaveBalance.year == year).order_by(LeaveType.code))).all()
    return [_balance_public(balance, leave_type) for balance, leave_type in rows]


async def create_draft(session: AsyncSession, *, requester_id: UUID, chat_thread_id: UUID, workflow_key: str, leave_type_code: str, start_date: date, end_date: date, start_period: str, end_period: str, reason: str, model_name: str = "") -> LeaveRequestPublic:
    existing = (await session.execute(select(LeaveRequest).where(LeaveRequest.workflow_key == workflow_key))).scalar_one_or_none()
    if existing is not None:
        return await request_public(session, existing)
    thread = (await session.execute(select(Thread).where(Thread.id == chat_thread_id).with_for_update())).scalar_one_or_none()
    if thread is None or thread.user_id != requester_id:
        raise _forbidden()
    active = (await session.execute(select(LeaveRequest.id).where(LeaveRequest.chat_thread_id == chat_thread_id, LeaveRequest.status.in_(("draft", "pending_approval"))))).first()
    if active:
        raise HTTPException(status_code=409, detail="该会话正在等待请假确认或审批，请先完成该流程。")
    leave_type = (await session.execute(select(LeaveType).where(or_(LeaveType.code == leave_type_code, LeaveType.name == leave_type_code), LeaveType.is_active.is_(True)))).scalar_one_or_none()
    if leave_type is None:
        raise HTTPException(status_code=422, detail="假期类型不存在或未启用")
    if not reason.strip():
        raise HTTPException(status_code=422, detail="请假原因不能为空")
    request = LeaveRequest(requester_id=requester_id, chat_thread_id=chat_thread_id, leave_type_id=leave_type.id, workflow_key=workflow_key, model_name=model_name, start_date=start_date, end_date=end_date, start_period=start_period, end_period=end_period, duration_days=calculate_duration(start_date, end_date, start_period, end_period, leave_type.allow_half_days), reason=reason.strip(), draft_expires_at=_now() + DRAFT_TTL)
    session.add(request); await session.commit(); await session.refresh(request)
    return await request_public(session, request)


async def request_public(session: AsyncSession, request: LeaveRequest) -> LeaveRequestPublic:
    leave_type = await _type(session, request.leave_type_id)
    balance = await _balance(session, request.requester_id, request.leave_type_id, request.start_date.year)
    return LeaveRequestPublic(id=request.id, chat_thread_id=request.chat_thread_id, leave_type_id=leave_type.id, leave_type_code=leave_type.code, leave_type_name=leave_type.name, start_date=request.start_date, end_date=request.end_date, start_period=request.start_period, end_period=request.end_period, duration_days=request.duration_days, reason=request.reason, status=request.status, cancel_reason=request.cancel_reason, draft_expires_at=request.draft_expires_at, workflow_stage=request.workflow_stage, resume_status=request.resume_status, version=request.version, created_at=request.created_at, updated_at=request.updated_at, balance=_balance_public(balance, leave_type) if balance else None)


async def _request_for_owner(session: AsyncSession, request_id: UUID, user_id: UUID, lock: bool = False) -> LeaveRequest:
    statement = select(LeaveRequest).where(LeaveRequest.id == request_id)
    if lock:
        statement = statement.with_for_update()
    request = (await session.execute(statement)).scalar_one_or_none()
    if request is None or request.requester_id != user_id:
        raise _forbidden()
    return request


def _audit(session: AsyncSession, actor_id: UUID | None, action: str, target_id: UUID, before: dict | None, after: dict | None) -> None:
    session.add(AuditLog(actor_id=actor_id, action=action, target_type="leave_request", target_id=str(target_id), before_data=before, after_data=after))


async def _eligible_admins(session: AsyncSession, requester_id: UUID) -> list[User]:
    return list((await session.execute(select(User).where(User.is_active.is_(True), User.role.in_(("admin", "super_admin")), User.id != requester_id))).scalars())


async def _claim_receipt(session: AsyncSession, *, actor_id: UUID, request_id: UUID, task_id: UUID | None, operation: str, idempotency_key: str, payload: object) -> bool:
    request_hash = _payload_hash(payload)
    inserted = await session.execute(insert(LeaveOperationReceipt).values(actor_id=actor_id, leave_request_id=request_id, approval_task_id=task_id, operation=operation, idempotency_key=idempotency_key, request_hash=request_hash).on_conflict_do_nothing(index_elements=["actor_id", "operation", "idempotency_key"]).returning(LeaveOperationReceipt.id))
    if inserted.scalar_one_or_none() is not None:
        return True
    receipt = (await session.execute(select(LeaveOperationReceipt).where(LeaveOperationReceipt.actor_id == actor_id, LeaveOperationReceipt.operation == operation, LeaveOperationReceipt.idempotency_key == idempotency_key))).scalar_one()
    if receipt.request_hash != request_hash or receipt.leave_request_id != request_id or receipt.approval_task_id != task_id:
        raise HTTPException(status_code=409, detail="幂等键已用于不同请求")
    return False


async def confirm_request(session: AsyncSession, request_id: UUID, requester_id: UUID, payload) -> tuple[LeaveRequestPublic, bool]:
    request = await _request_for_owner(session, request_id, requester_id, lock=True)
    claimed = await _claim_receipt(session, actor_id=requester_id, request_id=request.id, task_id=None, operation="confirm", idempotency_key=payload.idempotency_key, payload=payload)
    if not claimed:
        return await request_public(session, request), False
    if request.status != "draft":
        raise HTTPException(status_code=409, detail="该草稿已结束，不能确认")
    if request.draft_expires_at is None or request.draft_expires_at <= _now():
        request.status, request.cancel_reason, request.workflow_stage = "cancelled", "expired", "completed"; request.version += 1
        await session.commit(); raise HTTPException(status_code=410, detail="请假草稿已过期")
    if request.version != payload.version:
        raise HTTPException(status_code=409, detail="草稿已更新，请刷新后重试")
    leave_type = await _type(session, payload.leave_type_id)
    if not leave_type.is_active:
        raise HTTPException(status_code=422, detail="假期类型已停用")
    if not payload.reason.strip():
        raise HTTPException(status_code=422, detail="请假原因不能为空")
    duration = calculate_duration(payload.start_date, payload.end_date, payload.start_period, payload.end_period, leave_type.allow_half_days)
    await session.execute(select(User).where(User.id == requester_id).with_for_update())
    if not await _eligible_admins(session, requester_id):
        raise HTTPException(status_code=409, detail="当前没有可处理此申请的其他管理员")
    overlap = (await session.execute(select(LeaveRequest.id).where(LeaveRequest.requester_id == requester_id, LeaveRequest.id != request.id, LeaveRequest.status.in_(("pending_approval", "approved")), LeaveRequest.start_date <= payload.end_date, LeaveRequest.end_date >= payload.start_date))).first()
    if overlap:
        raise HTTPException(status_code=409, detail="与已有待审批或已批准的请假时间重叠")
    balance = await _balance(session, requester_id, leave_type.id, payload.start_date.year, lock=True)
    if balance is None or balance.entitled_days - balance.reserved_days - balance.used_days < duration:
        raise HTTPException(status_code=409, detail="可用假期余额不足")
    before = {"status": request.status, "duration_days": str(request.duration_days)}
    request.leave_type_id, request.start_date, request.end_date = leave_type.id, payload.start_date, payload.end_date
    request.start_period, request.end_period, request.duration_days, request.reason = payload.start_period, payload.end_period, duration, payload.reason.strip()
    request.status, request.draft_expires_at, request.workflow_stage, request.resume_status = "pending_approval", None, "awaiting_admin", "resume_pending"; request.version += 1
    balance.reserved_days += duration; balance.version += 1
    session.add(LeaveBalanceLedger(balance_id=balance.id, leave_request_id=request.id, operation_key=f"reserve:{request.id}", action="reserve", reserved_delta=duration))
    session.add(ApprovalTask(leave_request_id=request.id))
    for admin in await _eligible_admins(session, requester_id):
        session.add(Notification(recipient_id=admin.id, entity_id=str(request.id), title="新的请假审批待处理", body=f"请处理 {duration} 天的{leave_type.name}申请。"))
    _audit(session, requester_id, "leave.confirm", request.id, before, {"status": request.status, "duration_days": str(duration)})
    await session.commit(); await session.refresh(request)
    return await request_public(session, request), True


async def cancel_request(session: AsyncSession, request_id: UUID, requester_id: UUID, reason: str = "cancelled") -> tuple[LeaveRequestPublic, bool]:
    request = await _request_for_owner(session, request_id, requester_id, lock=True)
    if request.status == "cancelled":
        return await request_public(session, request), False
    if request.status != "draft":
        raise HTTPException(status_code=409, detail="只有未确认草稿可以取消")
    request.status, request.cancel_reason, request.workflow_stage, request.resume_status = "cancelled", reason, "completed", "resume_pending"; request.draft_expires_at = None; request.version += 1
    _audit(session, requester_id, "leave.cancel", request.id, {"status": "draft"}, {"status": "cancelled", "reason": reason})
    await session.commit(); await session.refresh(request)
    return await request_public(session, request), True


async def heartbeat(session: AsyncSession, request_id: UUID, requester_id: UUID) -> LeaveRequestPublic:
    request = await _request_for_owner(session, request_id, requester_id, lock=True)
    if request.status != "draft":
        raise HTTPException(status_code=409, detail="草稿已结束")
    request.draft_expires_at = _now() + DRAFT_TTL
    await session.commit(); await session.refresh(request)
    return await request_public(session, request)


async def decide_task(session: AsyncSession, task_id: UUID, admin: User, payload) -> tuple[LeaveRequestPublic, bool]:
    task = (await session.execute(select(ApprovalTask).where(ApprovalTask.id == task_id).with_for_update())).scalar_one_or_none()
    if task is None:
        raise _forbidden()
    request = (await session.execute(select(LeaveRequest).where(LeaveRequest.id == task.leave_request_id).with_for_update())).scalar_one()
    if request.requester_id == admin.id:
        raise _forbidden("不能审批自己的请假申请")
    claimed = await _claim_receipt(session, actor_id=admin.id, request_id=request.id, task_id=task.id, operation="decision", idempotency_key=payload.idempotency_key, payload=payload)
    if not claimed:
        return await request_public(session, request), False
    if task.status != "pending":
        raise HTTPException(status_code=409, detail="该审批任务已被处理")
    if task.version != payload.version:
        raise HTTPException(status_code=409, detail="审批任务已更新，请刷新后重试")
    balance = await _balance(session, request.requester_id, request.leave_type_id, request.start_date.year, lock=True)
    if balance is None:
        raise HTTPException(status_code=409, detail="找不到对应的假期余额")
    task.status, task.decided_by, task.decision_comment, task.decided_at = payload.decision, admin.id, (payload.comment or "").strip() or None, _now(); task.version += 1
    request.status, request.workflow_stage, request.resume_status, request.version = payload.decision, "completed", "resume_pending", request.version + 1
    if payload.decision == "approved":
        balance.reserved_days -= request.duration_days; balance.used_days += request.duration_days; ledger_action, reserved_delta, used_delta, notice = "consume", -request.duration_days, request.duration_days, "您的请假申请已获批准。"
    else:
        balance.reserved_days -= request.duration_days; ledger_action, reserved_delta, used_delta, notice = "release", -request.duration_days, ZERO, f"您的请假申请被拒绝：{task.decision_comment}"
    balance.version += 1
    session.add(LeaveBalanceLedger(balance_id=balance.id, leave_request_id=request.id, operation_key=f"{ledger_action}:{request.id}", action=ledger_action, reserved_delta=reserved_delta, used_delta=used_delta))
    session.add(Notification(recipient_id=request.requester_id, entity_id=str(request.id), title="请假审批结果", body=notice))
    _audit(session, admin.id, f"leave.{payload.decision}", request.id, {"status": "pending_approval"}, {"status": payload.decision, "comment": task.decision_comment})
    await session.commit(); await session.refresh(request)
    return await request_public(session, request), True


async def list_requests(session: AsyncSession, requester_id: UUID) -> list[LeaveRequestPublic]:
    requests = (await session.execute(select(LeaveRequest).where(LeaveRequest.requester_id == requester_id).order_by(LeaveRequest.created_at.desc()))).scalars()
    return [await request_public(session, request) for request in requests]


async def active_request_for_thread(session: AsyncSession, chat_thread_id: UUID, requester_id: UUID) -> LeaveRequest | None:
    statement = select(LeaveRequest).where(
        LeaveRequest.chat_thread_id == chat_thread_id,
        LeaveRequest.requester_id == requester_id,
        LeaveRequest.status.in_(("draft", "pending_approval")),
    ).order_by(LeaveRequest.created_at.desc())
    return (await session.execute(statement)).scalars().first()


async def _task_public(session: AsyncSession, task: ApprovalTask, request: LeaveRequest, requester: User) -> ApprovalTaskPublic:
    return ApprovalTaskPublic(id=task.id, leave_request=await request_public(session, request), status=task.status, requester_username=requester.username, requester_email=requester.email, decision_comment=task.decision_comment, decided_at=task.decided_at, version=task.version, created_at=task.created_at)


async def list_tasks(session: AsyncSession, admin_id: UUID, task_status: str | None = None, page: int = 1, page_size: int = 20) -> ApprovalTaskPage:
    statement = select(ApprovalTask, LeaveRequest, User).join(LeaveRequest, LeaveRequest.id == ApprovalTask.leave_request_id).join(User, User.id == LeaveRequest.requester_id).where(LeaveRequest.requester_id != admin_id)
    if task_status:
        statement = statement.where(ApprovalTask.status == task_status)
    total = await session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = (await session.execute(statement.order_by(ApprovalTask.created_at.desc()).offset((page - 1) * page_size).limit(page_size))).all()
    return ApprovalTaskPage(items=[await _task_public(session, task, request, requester) for task, request, requester in rows], total=total, page=page, page_size=page_size)


async def get_task(session: AsyncSession, task_id: UUID, admin_id: UUID) -> ApprovalTaskPublic:
    row = (await session.execute(select(ApprovalTask, LeaveRequest, User).join(LeaveRequest, LeaveRequest.id == ApprovalTask.leave_request_id).join(User, User.id == LeaveRequest.requester_id).where(ApprovalTask.id == task_id))).first()
    if row is None or row[1].requester_id == admin_id:
        raise _forbidden()
    return await _task_public(session, *row)


async def assert_resume_pending(session: AsyncSession, request_id: UUID) -> LeaveRequest:
    request = await session.get(LeaveRequest, request_id)
    if request is None:
        raise _forbidden()
    if request.resume_status != "resume_pending":
        raise HTTPException(status_code=409, detail="该工作流当前无需恢复")
    return request


async def list_notifications(session: AsyncSession, user_id: UUID) -> NotificationPage:
    rows = list((await session.execute(select(Notification).where(Notification.recipient_id == user_id).order_by(Notification.created_at.desc()).limit(100))).scalars())
    unread = await session.scalar(select(func.count(Notification.id)).where(Notification.recipient_id == user_id, Notification.read_at.is_(None))) or 0
    return NotificationPage(items=[NotificationPublic(id=n.id, category=n.category, entity_type=n.entity_type, entity_id=n.entity_id, title=n.title, body=n.body, read_at=n.read_at, created_at=n.created_at) for n in rows], unread=unread)


async def mark_notification_read(session: AsyncSession, notification_id: UUID, user_id: UUID) -> None:
    notification = (await session.execute(select(Notification).where(Notification.id == notification_id).with_for_update())).scalar_one_or_none()
    if notification is None or notification.recipient_id != user_id:
        raise _forbidden()
    notification.read_at = notification.read_at or _now(); await session.commit()


async def upsert_type(session: AsyncSession, payload, actor_id: UUID, type_id: UUID | None = None) -> LeaveTypePublic:
    item = await session.get(LeaveType, type_id) if type_id else None
    if type_id and item is None:
        # Resource-changing endpoints intentionally do not distinguish a
        # fabricated identifier from an inaccessible one.
        raise _forbidden()
    if item is None:
        item = LeaveType(code=payload.code, name=payload.name, is_active=payload.is_active, allow_half_days=payload.allow_half_days); session.add(item); await session.flush()
        session.add(AuditLog(actor_id=actor_id, action="leave_type.create", target_type="leave_type", target_id=str(item.id), after_data=payload.model_dump(mode="json")))
    else:
        referenced = bool(await session.scalar(select(func.count()).select_from(LeaveRequest).where(LeaveRequest.leave_type_id == item.id))) or bool(await session.scalar(select(func.count()).select_from(LeaveBalance).where(LeaveBalance.leave_type_id == item.id)))
        if referenced and (payload.code != item.code or payload.name != item.name or payload.allow_half_days != item.allow_half_days or payload.is_active or not item.is_active):
            raise HTTPException(status_code=409, detail="已被引用的假期类型只能停用")
        before = {"code": item.code, "name": item.name, "is_active": item.is_active, "allow_half_days": item.allow_half_days}
        item.code, item.name, item.is_active, item.allow_half_days = payload.code, payload.name, payload.is_active, payload.allow_half_days
        session.add(AuditLog(actor_id=actor_id, action="leave_type.update", target_type="leave_type", target_id=str(item.id), before_data=before, after_data=payload.model_dump(mode="json")))
    await session.commit(); await session.refresh(item)
    return LeaveTypePublic(id=item.id, code=item.code, name=item.name, is_active=item.is_active, allow_half_days=item.allow_half_days)


async def upsert_balance(session: AsyncSession, user_id: UUID, payload, actor_id: UUID) -> LeaveBalancePublic:
    if await session.get(User, user_id) is None:
        raise _forbidden()
    leave_type = await _type(session, payload.leave_type_id)
    balance = await _balance(session, user_id, payload.leave_type_id, payload.year, lock=True)
    if balance is None:
        balance = LeaveBalance(user_id=user_id, leave_type_id=payload.leave_type_id, year=payload.year, entitled_days=payload.entitled_days); session.add(balance); await session.flush(); before = None
    else:
        if payload.entitled_days < balance.reserved_days + balance.used_days:
            raise HTTPException(status_code=409, detail="额度不能低于已使用和已预占额度")
        before = {"entitled_days": str(balance.entitled_days), "version": balance.version}; balance.entitled_days, balance.version = payload.entitled_days, balance.version + 1
    session.add(AuditLog(actor_id=actor_id, action="leave_balance.update", target_type="leave_balance", target_id=str(balance.id), before_data=before, after_data=payload.model_dump(mode="json")))
    await session.commit(); await session.refresh(balance)
    return _balance_public(balance, leave_type)


async def expire_drafts(session: AsyncSession) -> list[UUID]:
    rows = list((await session.execute(select(LeaveRequest).where(LeaveRequest.status == "draft", LeaveRequest.draft_expires_at <= _now()).with_for_update())).scalars())
    ids: list[UUID] = []
    for request in rows:
        request.status, request.cancel_reason, request.workflow_stage, request.resume_status = "cancelled", "expired", "completed", "resume_pending"; request.draft_expires_at = None; request.version += 1; ids.append(request.id)
    if ids:
        await session.commit()
    return ids
