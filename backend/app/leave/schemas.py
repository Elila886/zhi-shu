from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


Period = Literal["am", "pm"]
Decision = Literal["approved", "rejected"]


class LeaveTypeInput(BaseModel):
    code: str = Field(min_length=2, max_length=32, pattern=r"^[a-z0-9_\-]+$")
    name: str = Field(min_length=1, max_length=80)
    is_active: bool = True
    allow_half_days: bool = True


class LeaveBalanceInput(BaseModel):
    leave_type_id: UUID
    year: int = Field(ge=2020, le=2100)
    entitled_days: Decimal = Field(ge=0, multiple_of=Decimal("0.5"), max_digits=7, decimal_places=1)


class ConfirmLeaveRequest(BaseModel):
    leave_type_id: UUID
    start_date: date
    end_date: date
    start_period: Period = "am"
    end_period: Period = "pm"
    reason: str = Field(min_length=1, max_length=2000)
    version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=100)


class DecisionInput(BaseModel):
    decision: Decision
    comment: str | None = Field(default=None, max_length=2000)
    version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=100)

    @model_validator(mode="after")
    def reject_needs_comment(self):
        if self.decision == "rejected" and not (self.comment or "").strip():
            raise ValueError("拒绝申请时必须填写审批意见")
        return self


class LeaveTypePublic(BaseModel):
    id: UUID
    code: str
    name: str
    is_active: bool
    allow_half_days: bool


class LeaveBalancePublic(BaseModel):
    id: UUID
    leave_type_id: UUID
    leave_type_code: str
    leave_type_name: str
    year: int
    entitled_days: Decimal
    reserved_days: Decimal
    used_days: Decimal
    remaining_days: Decimal
    version: int


class LeaveRequestPublic(BaseModel):
    id: UUID
    chat_thread_id: UUID
    leave_type_id: UUID
    leave_type_code: str
    leave_type_name: str
    start_date: date
    end_date: date
    start_period: Period
    end_period: Period
    duration_days: Decimal
    reason: str
    status: str
    cancel_reason: str | None
    draft_expires_at: datetime | None
    workflow_stage: str
    resume_status: str
    version: int
    created_at: datetime
    updated_at: datetime
    balance: LeaveBalancePublic | None = None


class ApprovalTaskPublic(BaseModel):
    id: UUID
    leave_request: LeaveRequestPublic
    status: str
    requester_username: str
    requester_email: str
    decision_comment: str | None
    decided_at: datetime | None
    version: int
    created_at: datetime


class ApprovalTaskPage(BaseModel):
    items: list[ApprovalTaskPublic]
    total: int
    page: int
    page_size: int


class LeaveEvent(BaseModel):
    type: Literal["leave_submitted", "leave_cancelled", "leave_workflow_error"]
    request_id: UUID | None = None
    content: str | None = None


class LeaveTransitionResponse(BaseModel):
    request: LeaveRequestPublic
    workflow_resume: Literal["waiting", "completed", "resume_pending"]
    events: list[LeaveEvent]


class NotificationPublic(BaseModel):
    id: UUID
    category: str
    entity_type: str
    entity_id: str
    title: str
    body: str
    read_at: datetime | None
    created_at: datetime


class NotificationPage(BaseModel):
    items: list[NotificationPublic]
    unread: int
