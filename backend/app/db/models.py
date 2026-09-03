from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, JSON, CheckConstraint, Date, ForeignKey, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(AsyncAttrs, DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(16), unique=True)
    email: Mapped[str] = mapped_column(String(40), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    first_name: Mapped[str] = mapped_column(String(25), nullable=True)
    last_name: Mapped[str] = mapped_column(String(25), nullable=True)
    is_verified: Mapped[bool] = mapped_column(default=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    role: Mapped[str] = mapped_column(String(20), default="user", server_default="user")
    can_query_personnel: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    last_login_at: Mapped[datetime | None] = mapped_column(nullable=True)
    disabled_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<User {self.username}>"


class Thread(Base):
    __tablename__ = "threads"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(100), default="New Chat")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))

    def __repr__(self):
        return f"<Thread {self.title}>"


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    file_name: Mapped[str] = mapped_column(String(255))
    uploaded_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    status: Mapped[str] = mapped_column(String(20), default="processing", server_default="processing")
    chunk_count: Mapped[int] = mapped_column(default=0, server_default="0")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    thread_id: Mapped[UUID] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"))

    def __repr__(self):
        return f"<Document {self.file_name}>"


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    target_type: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    before_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)


class RefreshSession(Base):
    """Server-side record backing a rotated refresh-token cookie."""

    __tablename__ = "refresh_sessions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    surface: Mapped[str] = mapped_column(String(20), default="user", server_default="user")
    current_jti: Mapped[UUID] = mapped_column(unique=True, index=True)
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class LeaveType(Base):
    __tablename__ = "leave_types"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")
    allow_half_days: Mapped[bool] = mapped_column(default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class LeaveBalance(Base):
    __tablename__ = "leave_balances"
    __table_args__ = (
        UniqueConstraint("user_id", "leave_type_id", "year", name="uq_leave_balance_user_type_year"),
        CheckConstraint("entitled_days >= 0 AND reserved_days >= 0 AND used_days >= 0", name="ck_leave_balance_nonnegative"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    leave_type_id: Mapped[UUID] = mapped_column(ForeignKey("leave_types.id", ondelete="RESTRICT"), index=True)
    year: Mapped[int] = mapped_column(index=True)
    entitled_days: Mapped[Decimal] = mapped_column(Numeric(7, 1), default=Decimal("0.0"), server_default="0")
    reserved_days: Mapped[Decimal] = mapped_column(Numeric(7, 1), default=Decimal("0.0"), server_default="0")
    used_days: Mapped[Decimal] = mapped_column(Numeric(7, 1), default=Decimal("0.0"), server_default="0")
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class LeaveRequest(Base):
    __tablename__ = "leave_requests"
    __table_args__ = (UniqueConstraint("workflow_key", name="uq_leave_request_workflow_key"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    requester_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    chat_thread_id: Mapped[UUID] = mapped_column(ForeignKey("threads.id", ondelete="CASCADE"), index=True)
    leave_type_id: Mapped[UUID] = mapped_column(ForeignKey("leave_types.id", ondelete="RESTRICT"), index=True)
    workflow_key: Mapped[str] = mapped_column(String(180))
    model_name: Mapped[str] = mapped_column(String(80), default="", server_default="")
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    start_period: Mapped[str] = mapped_column(String(2), default="am", server_default="am")
    end_period: Mapped[str] = mapped_column(String(2), default="pm", server_default="pm")
    duration_days: Mapped[Decimal] = mapped_column(Numeric(7, 1))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="draft", server_default="draft", index=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    draft_expires_at: Mapped[datetime | None] = mapped_column(nullable=True, index=True)
    workflow_stage: Mapped[str] = mapped_column(String(32), default="awaiting_employee", server_default="awaiting_employee")
    resume_status: Mapped[str] = mapped_column(String(24), default="ready", server_default="ready")
    resume_attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    resume_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class PersonnelProfile(Base):
    """Work-directory data kept separate from authentication and account state."""

    __tablename__ = "personnel_profiles"
    __table_args__ = (
        CheckConstraint("employment_status IN ('active', 'inactive')", name="ck_personnel_profile_employment_status"),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(80))
    # Case folding can expand a few Unicode characters, so the lookup key has
    # a little more room than the displayed name.
    normalized_name: Mapped[str] = mapped_column(String(160), index=True)
    employee_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    department: Mapped[str] = mapped_column(String(80))
    job_title: Mapped[str] = mapped_column(String(80))
    work_email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    work_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    employment_status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class ApprovalTask(Base):
    __tablename__ = "approval_tasks"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    leave_request_id: Mapped[UUID] = mapped_column(ForeignKey("leave_requests.id", ondelete="CASCADE"), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", server_default="pending", index=True)
    decided_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    decision_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)
    version: Mapped[int] = mapped_column(default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)


class LeaveBalanceLedger(Base):
    __tablename__ = "leave_balance_ledger"
    __table_args__ = (UniqueConstraint("operation_key", name="uq_leave_balance_ledger_operation_key"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    balance_id: Mapped[UUID] = mapped_column(ForeignKey("leave_balances.id", ondelete="CASCADE"), index=True)
    leave_request_id: Mapped[UUID] = mapped_column(ForeignKey("leave_requests.id", ondelete="CASCADE"), index=True)
    operation_key: Mapped[str] = mapped_column(String(140))
    action: Mapped[str] = mapped_column(String(24))
    reserved_delta: Mapped[Decimal] = mapped_column(Numeric(7, 1), default=Decimal("0.0"), server_default="0")
    used_delta: Mapped[Decimal] = mapped_column(Numeric(7, 1), default=Decimal("0.0"), server_default="0")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class LeaveOperationReceipt(Base):
    """Durable idempotency claim for externally retried leave mutations."""

    __tablename__ = "leave_operation_receipts"
    __table_args__ = (
        UniqueConstraint("actor_id", "operation", "idempotency_key", name="uq_leave_operation_receipt_actor_operation_key"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    leave_request_id: Mapped[UUID] = mapped_column(ForeignKey("leave_requests.id", ondelete="CASCADE"), index=True)
    approval_task_id: Mapped[UUID | None] = mapped_column(ForeignKey("approval_tasks.id", ondelete="CASCADE"), nullable=True, index=True)
    operation: Mapped[str] = mapped_column(String(24))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))
    result_status: Mapped[str] = mapped_column(String(24), default="accepted", server_default="accepted")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    recipient_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(40), default="leave", server_default="leave")
    entity_type: Mapped[str] = mapped_column(String(40), default="leave_request", server_default="leave_request")
    entity_id: Mapped[str] = mapped_column(String(100), index=True)
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    read_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
