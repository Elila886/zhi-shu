from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import text

from app.auth.dependencies import AdminUserDep, SuperAdminUserDep
from app.db.main import SessionDep
from app.db.models import Document
from app.db.pgvector_utils import delete_document_chunks_by_document_id
from app.users.schemas import UserPublic

from . import service
from .schemas import PasswordReset, UserAdminUpdate

admin_router = APIRouter()


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@admin_router.get("/me", response_model=UserPublic)
async def get_admin_profile(admin: AdminUserDep):
    """Return the administrator profile without crossing into the user surface."""
    return admin


@admin_router.get("/overview")
async def get_overview(admin: AdminUserDep, session: SessionDep):
    return await service.overview(session)


@admin_router.get("/health")
async def get_health(admin: AdminUserDep, session: SessionDep):
    await session.execute(text("SELECT 1"))
    pgvector = bool(await session.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector')")))
    return {"backend": "healthy", "database": "healthy", "pgvector": "healthy" if pgvector else "unavailable"}


@admin_router.get("/users")
async def get_users(admin: AdminUserDep, session: SessionDep, page: int = Query(1, ge=1),
                    page_size: int = Query(20, ge=1, le=100), q: str | None = None,
                    role: str | None = None, active: bool | None = None):
    return await service.list_users(session, page, page_size, q, role, active)


@admin_router.patch("/users/{user_id}")
async def patch_user(user_id: UUID, payload: UserAdminUpdate, request: Request, admin: AdminUserDep, session: SessionDep):
    user = await service.update_user(session, admin, user_id, payload.model_dump(exclude_unset=True), _ip(request))
    return {"id": str(user.id), "role": user.role, "is_active": user.is_active, "disabled_reason": user.disabled_reason}


@admin_router.post("/users/{user_id}/reset-password")
async def post_reset_password(user_id: UUID, payload: PasswordReset, request: Request, admin: AdminUserDep, session: SessionDep):
    await service.reset_password(session, admin, user_id, payload.new_password, _ip(request))
    return {"message": "Password reset successfully"}


@admin_router.get("/documents")
async def get_documents(admin: AdminUserDep, session: SessionDep, page: int = Query(1, ge=1),
                        page_size: int = Query(20, ge=1, le=100), q: str | None = None,
                        status: str | None = None, user_id: UUID | None = None):
    return await service.list_documents(session, page, page_size, q, status, user_id)


@admin_router.delete("/documents/{document_id}")
async def delete_document(document_id: UUID, request: Request, admin: AdminUserDep, session: SessionDep):
    document = await session.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    before = {"file_name": document.file_name, "thread_id": str(document.thread_id), "status": document.status}
    deleted_chunks = await delete_document_chunks_by_document_id(document_id)
    await session.delete(document)
    service.add_audit(session, admin, "document.delete", "document", str(document_id), before=before,
                      after={"deleted_chunks": deleted_chunks}, ip_address=_ip(request))
    await session.commit()
    return {"message": "Document deleted", "deleted_chunks": deleted_chunks}


@admin_router.get("/audit-logs")
async def get_audit_logs(admin: SuperAdminUserDep, session: SessionDep, page: int = Query(1, ge=1),
                         page_size: int = Query(20, ge=1, le=100), q: str | None = None):
    return await service.list_audits(session, page, page_size, q)
