from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.db.main import async_session
from app.db.models import LeaveBalance, LeaveType, Thread, User
from app.leave import service
from app.leave.schemas import ConfirmLeaveRequest, DecisionInput


@pytest.mark.asyncio
async def test_confirm_reserves_then_approval_consumes_and_notifies_employee():
    async with async_session() as session:
        employee = User(username="leaveuser", email="leaveuser@example.com", password_hash="x")
        approver = User(username="leaveadmin", email="leaveadmin@example.com", password_hash="x", role="admin")
        leave_type = LeaveType(code="annual_leave", name="年假")
        session.add_all((employee, approver, leave_type))
        await session.flush()
        thread = Thread(user_id=employee.id)
        balance = LeaveBalance(user_id=employee.id, leave_type_id=leave_type.id, year=2026, entitled_days=Decimal("5.0"))
        session.add_all((thread, balance)); await session.commit()

        draft = await service.create_draft(
            session, requester_id=employee.id, chat_thread_id=thread.id, workflow_key="workflow-1", model_name="test",
            leave_type_code="annual_leave", start_date=date(2026, 9, 7), end_date=date(2026, 9, 7),
            start_period="am", end_period="pm", reason="家庭事务",
        )
        confirmed, transitioned = await service.confirm_request(session, draft.id, employee.id, ConfirmLeaveRequest(
            leave_type_id=leave_type.id, start_date=date(2026, 9, 7), end_date=date(2026, 9, 7),
            start_period="am", end_period="pm", reason="家庭事务", version=draft.version, idempotency_key="confirm-001",
        ))
        assert transitioned and confirmed.status == "pending_approval"
        task = (await service.list_tasks(session, approver.id, "pending")).items[0]
        final, transitioned = await service.decide_task(session, task.id, approver, DecisionInput(
            decision="approved", comment="", version=task.version, idempotency_key="decision-001",
        ))
        assert transitioned and final.status == "approved"
        refreshed = await session.get(LeaveBalance, balance.id)
        assert refreshed is not None and refreshed.reserved_days == Decimal("0.0") and refreshed.used_days == Decimal("1.0")
        assert (await service.list_notifications(session, employee.id)).unread == 1


@pytest.mark.asyncio
async def test_self_approval_is_forbidden():
    async with async_session() as session:
        employee = User(username="selfleave", email="selfleave@example.com", password_hash="x", role="admin")
        other_admin = User(username="otheradmin", email="otheradmin@example.com", password_hash="x", role="admin")
        leave_type = LeaveType(code="sick_leave", name="病假")
        session.add_all((employee, other_admin, leave_type)); await session.flush()
        thread = Thread(user_id=employee.id)
        balance = LeaveBalance(user_id=employee.id, leave_type_id=leave_type.id, year=2026, entitled_days=Decimal("2.0"))
        session.add_all((thread, balance)); await session.commit()
        draft = await service.create_draft(session, requester_id=employee.id, chat_thread_id=thread.id, workflow_key="workflow-2", model_name="test", leave_type_code="sick_leave", start_date=date(2026, 9, 8), end_date=date(2026, 9, 8), start_period="am", end_period="pm", reason="不适")
        await service.confirm_request(session, draft.id, employee.id, ConfirmLeaveRequest(leave_type_id=leave_type.id, start_date=date(2026, 9, 8), end_date=date(2026, 9, 8), start_period="am", end_period="pm", reason="不适", version=draft.version, idempotency_key="confirm-002"))
        task = (await service.list_tasks(session, other_admin.id, "pending")).items[0]
        with pytest.raises(HTTPException) as exc:
            await service.decide_task(session, task.id, employee, DecisionInput(decision="approved", version=task.version, idempotency_key="decision-002"))
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_confirm_replay_is_idempotent_and_foreign_request_is_forbidden():
    async with async_session() as session:
        employee = User(username="replayuser", email="replayuser@example.com", password_hash="x")
        intruder = User(username="intruder", email="intruder@example.com", password_hash="x")
        approver = User(username="replayadmin", email="replayadmin@example.com", password_hash="x", role="admin")
        leave_type = LeaveType(code="replay_leave", name="调休")
        session.add_all((employee, intruder, approver, leave_type)); await session.flush()
        thread = Thread(user_id=employee.id)
        balance = LeaveBalance(user_id=employee.id, leave_type_id=leave_type.id, year=2026, entitled_days=Decimal("2.0"))
        session.add_all((thread, balance)); await session.commit()
        draft = await service.create_draft(session, requester_id=employee.id, chat_thread_id=thread.id, workflow_key="workflow-replay", leave_type_code="replay_leave", start_date=date(2026, 9, 9), end_date=date(2026, 9, 9), start_period="am", end_period="pm", reason="安排")
        payload = ConfirmLeaveRequest(leave_type_id=leave_type.id, start_date=date(2026, 9, 9), end_date=date(2026, 9, 9), start_period="am", end_period="pm", reason="安排", version=draft.version, idempotency_key="confirm-replay-001")
        first, first_transitioned = await service.confirm_request(session, draft.id, employee.id, payload)
        second, second_transitioned = await service.confirm_request(session, draft.id, employee.id, payload)
        assert first_transitioned and not second_transitioned and second.status == "pending_approval"
        refreshed = await session.get(LeaveBalance, balance.id)
        assert refreshed is not None and refreshed.reserved_days == Decimal("1.0")
        with pytest.raises(HTTPException) as key_reused:
            await service.confirm_request(session, draft.id, employee.id, payload.model_copy(update={"reason": "不同载荷"}))
        assert key_reused.value.status_code == 409
        with pytest.raises(HTTPException) as duplicate:
            await service.confirm_request(session, draft.id, employee.id, payload.model_copy(update={"idempotency_key": "confirm-replay-002"}))
        assert duplicate.value.status_code == 409
        with pytest.raises(HTTPException) as exc:
            await service.cancel_request(session, draft.id, intruder.id)
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_decision_receipt_replays_but_conflicting_retries_are_rejected():
    async with async_session() as session:
        employee = User(username="decisionreplay", email="decisionreplay@example.com", password_hash="x")
        admin = User(username="decisionadmin", email="decisionadmin@example.com", password_hash="x", role="admin")
        leave_type = LeaveType(code="decision_leave", name="事假")
        session.add_all((employee, admin, leave_type)); await session.flush()
        thread = Thread(user_id=employee.id)
        balance = LeaveBalance(user_id=employee.id, leave_type_id=leave_type.id, year=2026, entitled_days=Decimal("3.0"))
        session.add_all((thread, balance)); await session.commit()
        draft = await service.create_draft(session, requester_id=employee.id, chat_thread_id=thread.id, workflow_key="workflow-decision-replay", leave_type_code=leave_type.code, start_date=date(2026, 9, 10), end_date=date(2026, 9, 10), start_period="am", end_period="pm", reason="安排")
        await service.confirm_request(session, draft.id, employee.id, ConfirmLeaveRequest(leave_type_id=leave_type.id, start_date=date(2026, 9, 10), end_date=date(2026, 9, 10), start_period="am", end_period="pm", reason="安排", version=draft.version, idempotency_key="confirm-decision-replay"))
        task = (await service.list_tasks(session, admin.id, "pending")).items[0]
        payload = DecisionInput(decision="rejected", comment="人员安排", version=task.version, idempotency_key="decision-replay-001")
        first, changed = await service.decide_task(session, task.id, admin, payload)
        replay, replay_changed = await service.decide_task(session, task.id, admin, payload)
        assert changed and not replay_changed and replay.status == "rejected"
        refreshed = await session.get(LeaveBalance, balance.id)
        assert refreshed is not None and refreshed.reserved_days == Decimal("0.0") and refreshed.used_days == Decimal("0.0")
        with pytest.raises(HTTPException) as conflict:
            await service.decide_task(session, task.id, admin, DecisionInput(decision="approved", comment="", version=task.version, idempotency_key="decision-replay-001"))
        assert conflict.value.status_code == 409
        with pytest.raises(HTTPException) as duplicate:
            await service.decide_task(session, task.id, admin, DecisionInput(decision="approved", comment="", version=task.version, idempotency_key="decision-replay-002"))
        assert duplicate.value.status_code == 409


@pytest.mark.asyncio
async def test_referenced_leave_type_can_only_be_disabled():
    async with async_session() as session:
        actor = User(username="typeadmin", email="typeadmin@example.com", password_hash="x", role="admin")
        employee = User(username="typeemployee", email="typeemployee@example.com", password_hash="x")
        leave_type = LeaveType(code="immutable_leave", name="不可变假")
        session.add_all((actor, employee, leave_type)); await session.flush()
        session.add(LeaveBalance(user_id=employee.id, leave_type_id=leave_type.id, year=2026, entitled_days=Decimal("1.0"))); await session.commit()
        from app.leave.schemas import LeaveTypeInput
        disabled = await service.upsert_type(session, LeaveTypeInput(code=leave_type.code, name=leave_type.name, is_active=False, allow_half_days=True), actor.id, leave_type.id)
        assert not disabled.is_active
        with pytest.raises(HTTPException) as exc:
            await service.upsert_type(session, LeaveTypeInput(code=leave_type.code, name=leave_type.name, is_active=True, allow_half_days=True), actor.id, leave_type.id)
        assert exc.value.status_code == 409
