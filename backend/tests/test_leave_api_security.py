from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from app.db.main import async_session
from app.db.models import LeaveBalance, LeaveType, Thread, User
from app.leave import service
from app.leave.schemas import ConfirmLeaveRequest

from .test_auth_integration import bearer, login, set_role, signup


@pytest.mark.asyncio
async def test_foreign_leave_mutations_are_forbidden_before_workflow_resume(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    await signup(client, "leaveowner")
    await signup(client, "leaveintruder")
    await signup(client, "leaveapprover")
    await set_role("leaveapprover@example.com", "admin")
    owner_token, intruder_token, admin_token = await login(client, "leaveowner"), await login(client, "leaveintruder"), await login(client, "leaveapprover", admin=True)
    async with async_session() as session:
        owner = (await session.execute(__import__("sqlalchemy").select(User).where(User.email == "leaveowner@example.com"))).scalar_one()
        approver = (await session.execute(__import__("sqlalchemy").select(User).where(User.email == "leaveapprover@example.com"))).scalar_one()
        leave_type = LeaveType(code="api_security_leave", name="安全假")
        session.add(leave_type); await session.flush()
        thread = Thread(user_id=owner.id)
        session.add_all((thread, LeaveBalance(user_id=owner.id, leave_type_id=leave_type.id, year=2026, entitled_days=Decimal("2.0")))); await session.commit()
        draft = await service.create_draft(session, requester_id=owner.id, chat_thread_id=thread.id, workflow_key="workflow-api-security", leave_type_code=leave_type.code, start_date=date(2026, 9, 11), end_date=date(2026, 9, 11), start_period="am", end_period="pm", reason="安全验证")
        await service.confirm_request(session, draft.id, owner.id, ConfirmLeaveRequest(leave_type_id=leave_type.id, start_date=date(2026, 9, 11), end_date=date(2026, 9, 11), start_period="am", end_period="pm", reason="安全验证", version=draft.version, idempotency_key="confirm-api-security"))
        task = (await service.list_tasks(session, approver.id, "pending")).items[0]

    from app.leave import routes
    resume = AsyncMock(return_value=True)
    monkeypatch.setattr(routes.workflow, "resume_request", resume)
    confirm_body = {"leave_type_id": str(draft.leave_type_id), "start_date": "2026-09-11", "end_date": "2026-09-11", "start_period": "am", "end_period": "pm", "reason": "安全验证", "version": 1, "idempotency_key": "foreign-confirm-001"}
    assert (await client.post(f"/api/v1/leave/requests/{draft.id}/confirm", headers=bearer(intruder_token), json=confirm_body)).status_code == 403
    assert (await client.post(f"/api/v1/leave/requests/{draft.id}/cancel", headers=bearer(intruder_token))).status_code == 403
    assert (await client.post(f"/api/v1/leave/requests/{draft.id}/heartbeat", headers=bearer(intruder_token))).status_code == 403
    assert (await client.post(f"/api/v1/admin/approval-tasks/{task.id}/decision", headers=bearer(admin_token), json={"decision": "approved", "comment": "", "version": task.version, "idempotency_key": "foreign-task-001"})).status_code == 200
    resume.assert_awaited_once()
