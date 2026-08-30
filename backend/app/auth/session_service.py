from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import RefreshSession

from .schemas import TokenData


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def create_refresh_session(session: AsyncSession, user_id: UUID, surface: str) -> tuple[RefreshSession, UUID]:
    token_id = uuid4()
    record = RefreshSession(
        user_id=user_id,
        surface=surface,
        current_jti=token_id,
        expires_at=utc_now_naive() + timedelta(days=settings.refresh_token_expiry_days),
    )
    session.add(record)
    await session.flush()
    return record, token_id


async def get_active_session(
    session: AsyncSession,
    token_data: TokenData,
    *,
    expected_surface: str | None = None,
    require_current_jti: bool = False,
) -> RefreshSession:
    if token_data.sid is None:
        raise HTTPException(status_code=401, detail="Session required")
    statement = select(RefreshSession).where(RefreshSession.id == token_data.sid)
    if require_current_jti:
        statement = statement.with_for_update()
    record = (await session.execute(statement)).scalar_one_or_none()
    if record is None or record.revoked_at is not None or record.expires_at <= utc_now_naive():
        raise HTTPException(status_code=401, detail="Session expired")
    if record.user_id != token_data.user.id or record.surface != token_data.surface:
        raise HTTPException(status_code=401, detail="Invalid session")
    if expected_surface is not None and record.surface != expected_surface:
        raise HTTPException(status_code=401, detail="Invalid session surface")
    if require_current_jti and record.current_jti != token_data.jti:
        record.revoked_at = utc_now_naive()
        await session.commit()
        raise HTTPException(status_code=401, detail="Refresh token has already been used")
    return record


async def rotate_refresh_session(session: AsyncSession, record: RefreshSession) -> UUID:
    token_id = uuid4()
    record.current_jti = token_id
    await session.flush()
    return token_id


async def revoke_session(session: AsyncSession, session_id: UUID) -> None:
    record = await session.get(RefreshSession, session_id)
    if record is not None and record.revoked_at is None:
        record.revoked_at = utc_now_naive()


async def revoke_user_sessions(session: AsyncSession, user_id: UUID) -> None:
    await session.execute(
        update(RefreshSession)
        .where(RefreshSession.user_id == user_id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=utc_now_naive())
    )
