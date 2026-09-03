from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import text

from app.auth.dependencies import AdminUserDep, SuperAdminUserDep
from app.db.main import SessionDep
from app.db.models import Document, Thread
from app.db.pgvector_utils import delete_document_chunks_by_document_id
from app.traffic_governance.core import get_redis_client
from app.traffic_governance.dependencies import AdminRateLimitDep
from app.users.schemas import UserPublic

from . import service
from .schemas import (
    PersonnelProfileDetail,
    PersonnelProfileInput,
    PersonnelProfilePublic,
    PersonnelQueryPermissionInput,
    PasswordReset,
    UserAdminUpdate,
)
from app.personnel import service as personnel_service

admin_router = APIRouter()


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@admin_router.get("/me", response_model=UserPublic)
async def get_admin_profile(admin: AdminUserDep, _: AdminRateLimitDep):
    """Return the administrator profile without crossing into the user surface."""
    return admin


@admin_router.get("/overview")
async def get_overview(admin: AdminUserDep, session: SessionDep, _: AdminRateLimitDep):
    return await service.overview(session)


@admin_router.get("/health")
async def get_health(request: Request, admin: AdminUserDep, session: SessionDep, _: AdminRateLimitDep):
    await session.execute(text("SELECT 1"))
    pgvector = bool(await session.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector')")))
    try:
        await get_redis_client(request).ping()
        redis_status = "healthy"
    except Exception:
        redis_status = "unavailable"
    return {
        "backend": "healthy",
        "database": "healthy",
        "pgvector": "healthy" if pgvector else "unavailable",
        "redis": redis_status,
        "traffic_governance": "healthy" if redis_status == "healthy" else "degraded",
    }


@admin_router.get("/users")
async def get_users(admin: AdminUserDep, session: SessionDep, _: AdminRateLimitDep, page: int = Query(1, ge=1),
                    page_size: int = Query(20, ge=1, le=100), q: str | None = None,
                    role: str | None = None, active: bool | None = None):
    return await service.list_users(session, page, page_size, q, role, active)


@admin_router.patch("/users/{user_id}")
async def patch_user(user_id: UUID, payload: UserAdminUpdate, request: Request, admin: AdminUserDep, session: SessionDep, _: AdminRateLimitDep):
    user = await service.update_user(session, admin, user_id, payload.model_dump(exclude_unset=True), _ip(request))
    return {"id": str(user.id), "role": user.role, "is_active": user.is_active, "disabled_reason": user.disabled_reason}


@admin_router.post("/users/{user_id}/reset-password")
async def post_reset_password(user_id: UUID, payload: PasswordReset, request: Request, admin: AdminUserDep, session: SessionDep, _: AdminRateLimitDep):
    await service.reset_password(session, admin, user_id, payload.new_password, _ip(request))
    return {"message": "Password reset successfully"}


@admin_router.get("/users/{user_id}/personnel-profile", response_model=PersonnelProfileDetail)
async def get_personnel_profile(
    user_id: UUID,
    admin: SuperAdminUserDep,
    session: SessionDep,
    _: AdminRateLimitDep,
):
    return await personnel_service.profile_detail(session, user_id)


@admin_router.put("/users/{user_id}/personnel-profile", response_model=PersonnelProfilePublic)
async def put_personnel_profile(
    user_id: UUID,
    payload: PersonnelProfileInput,
    request: Request,
    admin: SuperAdminUserDep,
    session: SessionDep,
    _: AdminRateLimitDep,
):
    return await personnel_service.upsert_profile(session, admin, user_id, payload, _ip(request))


@admin_router.patch("/users/{user_id}/personnel-query-permission")
async def patch_personnel_query_permission(
    user_id: UUID,
    payload: PersonnelQueryPermissionInput,
    request: Request,
    admin: SuperAdminUserDep,
    session: SessionDep,
    _: AdminRateLimitDep,
):
    user = await personnel_service.update_query_permission(session, admin, user_id, payload.enabled, _ip(request))
    return {"id": str(user.id), "can_query_personnel": user.can_query_personnel}


@admin_router.get("/documents")
async def get_documents(admin: AdminUserDep, session: SessionDep, _: AdminRateLimitDep, page: int = Query(1, ge=1),
                        page_size: int = Query(20, ge=1, le=100), q: str | None = None,
                        status: str | None = None, user_id: UUID | None = None):
    return await service.list_documents(session, page, page_size, q, status, user_id)


@admin_router.delete("/documents/{document_id}")
async def delete_document(document_id: UUID, request: Request, admin: AdminUserDep, session: SessionDep, _: AdminRateLimitDep):
    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    thread = await session.get(Thread, document.thread_id)
    if thread is None:
        raise HTTPException(status_code=409, detail="Document ownership record not found")
    before = {"file_name": document.file_name, "thread_id": str(document.thread_id), "status": document.status}
    deleted_chunks = await delete_document_chunks_by_document_id(
        document_id=document_id,
        thread_id=thread.id,
        user_id=thread.user_id,
    )
    await session.delete(document)
    service.add_audit(session, admin, "document.delete", "document", str(document_id), before=before,
                      after={"deleted_chunks": deleted_chunks}, ip_address=_ip(request))
    await session.commit()
    return {"message": "Document deleted", "deleted_chunks": deleted_chunks}


@admin_router.get("/audit-logs")
async def get_audit_logs(admin: SuperAdminUserDep, session: SessionDep, _: AdminRateLimitDep, page: int = Query(1, ge=1),
                         page_size: int = Query(20, ge=1, le=100), q: str | None = None):
    return await service.list_audits(session, page, page_size, q)
