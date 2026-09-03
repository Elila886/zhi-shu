from uuid import UUID

from fastapi import APIRouter, Query, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.auth.dependencies import AdminUserDep, CurrentUserDep
from app.db.main import SessionDep
from app.db.models import LeaveRequest
from app.traffic_governance.dependencies import AdminRateLimitDep, OrdinaryRateLimitDep

from . import service, workflow
from .schemas import ConfirmLeaveRequest, DecisionInput, LeaveBalanceInput, LeaveEvent, LeaveTransitionResponse, LeaveTypeInput

leave_router = APIRouter()
admin_leave_router = APIRouter()


def _transition_response(request, workflow_resume: str, events: list[LeaveEvent], failed: bool = False) -> JSONResponse:
    payload = LeaveTransitionResponse(request=request, workflow_resume=workflow_resume, events=events)
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED if failed else status.HTTP_200_OK, content=jsonable_encoder(payload))


@leave_router.get("/types")
async def get_types(current_user: CurrentUserDep, session: SessionDep, _: OrdinaryRateLimitDep):
    return await service.list_types(session)


@leave_router.get("/balances")
async def get_balances(current_user: CurrentUserDep, session: SessionDep, _: OrdinaryRateLimitDep, year: int | None = Query(default=None)):
    return await service.list_balances(session, current_user.id, year or service.business_year())


@leave_router.get("/requests")
async def get_requests(current_user: CurrentUserDep, session: SessionDep, _: OrdinaryRateLimitDep):
    return await service.list_requests(session, current_user.id)


@leave_router.post("/requests/{request_id}/confirm")
async def confirm(request_id: UUID, payload: ConfirmLeaveRequest, current_user: CurrentUserDep, session: SessionDep, _: OrdinaryRateLimitDep):
    request, transitioned = await service.confirm_request(session, request_id, current_user.id, payload)
    ok = not transitioned or await workflow.resume_request(request_id, "confirmed")
    # The workflow resume uses an independent transaction.  Refresh before
    # returning so REST clients never receive the pre-resume `resume_pending`
    # snapshot after a successful checkpoint write.
    latest = await session.get(LeaveRequest, request.id, populate_existing=True)
    request = await service.request_public(session, latest)
    events = [LeaveEvent(type="leave_submitted", request_id=request_id)]
    if not ok:
        events.append(LeaveEvent(type="leave_workflow_error", request_id=request_id, content="申请已提交，工作流恢复将在后台重试。"))
    return _transition_response(request, "waiting" if ok else "resume_pending", events, not ok)


@leave_router.post("/requests/{request_id}/cancel")
async def cancel(request_id: UUID, current_user: CurrentUserDep, session: SessionDep, _: OrdinaryRateLimitDep):
    request, transitioned = await service.cancel_request(session, request_id, current_user.id)
    ok = not transitioned or await workflow.resume_request(request_id, "cancelled")
    latest = await session.get(LeaveRequest, request.id, populate_existing=True)
    request = await service.request_public(session, latest)
    events = [LeaveEvent(type="leave_cancelled", request_id=request_id)]
    if not ok:
        events.append(LeaveEvent(type="leave_workflow_error", request_id=request_id, content="草稿已取消，聊天工作流恢复将在后台重试。"))
    return _transition_response(request, "completed" if ok else "resume_pending", events, not ok)


@leave_router.post("/requests/{request_id}/heartbeat")
async def renew_draft(request_id: UUID, current_user: CurrentUserDep, session: SessionDep, _: OrdinaryRateLimitDep):
    return await service.heartbeat(session, request_id, current_user.id)


@leave_router.get("/notifications")
async def get_notifications(current_user: CurrentUserDep, session: SessionDep, _: OrdinaryRateLimitDep):
    return await service.list_notifications(session, current_user.id)


@leave_router.post("/notifications/{notification_id}/read")
async def read_notification(notification_id: UUID, current_user: CurrentUserDep, session: SessionDep, _: OrdinaryRateLimitDep):
    await service.mark_notification_read(session, notification_id, current_user.id)
    return {"ok": True}


@admin_leave_router.get("/approval-tasks")
async def get_tasks(admin: AdminUserDep, session: SessionDep, _: AdminRateLimitDep, status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    return await service.list_tasks(session, admin.id, status, page, page_size)


@admin_leave_router.get("/approval-tasks/{task_id}")
async def get_task(task_id: UUID, admin: AdminUserDep, session: SessionDep, _: AdminRateLimitDep):
    return await service.get_task(session, task_id, admin.id)


@admin_leave_router.post("/approval-tasks/{task_id}/decision")
async def decide(task_id: UUID, payload: DecisionInput, admin: AdminUserDep, session: SessionDep, _: AdminRateLimitDep):
    request, transitioned = await service.decide_task(session, task_id, admin, payload)
    ok = not transitioned or await workflow.resume_request(request.id, "decided")
    latest = await session.get(LeaveRequest, request.id, populate_existing=True)
    request = await service.request_public(session, latest)
    events: list[LeaveEvent] = []
    if not ok:
        events.append(LeaveEvent(type="leave_workflow_error", request_id=request.id, content="审批结果已保存，聊天工作流恢复将在后台重试。"))
    return _transition_response(request, "completed" if ok else "resume_pending", events, not ok)


@admin_leave_router.post("/leave-requests/{request_id}/resume")
async def resume(request_id: UUID, admin: AdminUserDep, session: SessionDep, _: AdminRateLimitDep):
    await service.assert_resume_pending(session, request_id)
    return {"ok": await workflow.retry_request(request_id, force=True)}


@admin_leave_router.get("/notifications")
async def admin_notifications(admin: AdminUserDep, session: SessionDep, _: AdminRateLimitDep):
    return await service.list_notifications(session, admin.id)


@admin_leave_router.post("/notifications/{notification_id}/read")
async def admin_read_notification(notification_id: UUID, admin: AdminUserDep, session: SessionDep, _: AdminRateLimitDep):
    await service.mark_notification_read(session, notification_id, admin.id)
    return {"ok": True}


@admin_leave_router.get("/leave-types")
async def admin_types(admin: AdminUserDep, session: SessionDep, _: AdminRateLimitDep):
    return await service.list_types(session, active_only=False)


@admin_leave_router.post("/leave-types")
async def create_type(payload: LeaveTypeInput, admin: AdminUserDep, session: SessionDep, _: AdminRateLimitDep):
    return await service.upsert_type(session, payload, admin.id)


@admin_leave_router.put("/leave-types/{type_id}")
async def update_type(type_id: UUID, payload: LeaveTypeInput, admin: AdminUserDep, session: SessionDep, _: AdminRateLimitDep):
    return await service.upsert_type(session, payload, admin.id, type_id)


@admin_leave_router.put("/leave-balances/{user_id}")
async def update_balance(user_id: UUID, payload: LeaveBalanceInput, admin: AdminUserDep, session: SessionDep, _: AdminRateLimitDep):
    return await service.upsert_balance(session, user_id, payload, admin.id)
