from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.models import Base

engine: AsyncEngine = create_async_engine(url=settings.database_uri)
async_session: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def init_db() -> None:
    logger.info("Creating tables if not exist...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # create_all does not add columns to an existing installation. These
        # idempotent statements keep upgrades safe without dropping user data.
        for statement in (
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT 'user'",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS can_query_personnel BOOLEAN NOT NULL DEFAULT false",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ NULL",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS disabled_reason VARCHAR(255) NULL",
            "ALTER TABLE leave_requests ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now()",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'completed'",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS chunk_count INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE documents ADD COLUMN IF NOT EXISTS error_message TEXT NULL",
        ):
            await conn.execute(text(statement))
        for email in settings.super_admin_emails:
            await conn.execute(
                text("UPDATE users SET role = 'super_admin' WHERE lower(email) = :email"),
                {"email": email.lower()},
            )
        logger.info("✅ Database tables created successfully")
