"""Start an isolated E2E API after seeding administrator test accounts."""

import asyncio
import os
from uuid import uuid4

import uvicorn
from sqlalchemy import or_, select

from app.auth.utils import hash_password
from app.db.main import async_session, engine, init_db
from app.db.models import AuditLog, Document, Thread, User
from app.db.pgvector_utils import vector_store


async def _upsert_account(email_env: str, password_env: str, username: str, role: str) -> User:
    email = os.environ[email_env]
    password = os.environ[password_env]
    async with async_session() as session:
        user = (
            await session.execute(select(User).where(or_(User.email == email, User.username == username)))
        ).scalar_one_or_none()
        if user is None:
            user = User(
                username=username,
                email=email,
                password_hash=hash_password(password),
                first_name="E2E",
                last_name=role,
                role=role,
            )
            session.add(user)
        else:
            user.email = email
            user.username = username
            user.password_hash = hash_password(password)
            user.role = role
            user.is_active = True
        await session.commit()
        await session.refresh(user)
        return user


async def _seed_stable_records(member: User, super_admin: User) -> None:
    """Seed deterministic records, including vector rows without embedding calls."""
    vector_fixtures: list[tuple[Document, Thread]] = []
    async with async_session() as session:
        titles = ("E2E 稳定会话 A", "E2E 稳定会话 B", "E2E 移动会话")
        existing_threads = {
            thread.title: thread
            for thread in (
                await session.execute(select(Thread).where(Thread.user_id == member.id, Thread.title.in_(titles)))
            ).scalars()
        }
        for title in titles:
            if title not in existing_threads:
                thread = Thread(title=title, user_id=member.id)
                session.add(thread)
                existing_threads[title] = thread
        await session.flush()
        primary = existing_threads[titles[0]]
        secondary = existing_threads[titles[1]]
        mobile = existing_threads[titles[2]]

        document_specs = (
            ("E2E 待删除文档.txt", primary),
            ("E2E 管理待删除文档.txt", secondary),
            ("E2E 移动待删除文档.txt", mobile),
        )
        for file_name, thread in document_specs:
            document = (
                await session.execute(select(Document).where(Document.thread_id == thread.id, Document.file_name == file_name))
            ).scalar_one_or_none()
            if document is None:
                document = Document(file_name=file_name, thread_id=thread.id, status="completed", chunk_count=1)
                session.add(document)
                vector_fixtures.append((document, thread))
        await session.flush()
        seeded_audit = await session.scalar(
            select(AuditLog).where(AuditLog.action == "e2e.seed", AuditLog.target_id == str(primary.id))
        )
        if seeded_audit is None:
            session.add(
                AuditLog(
                    actor_id=super_admin.id,
                    action="e2e.seed",
                    target_type="thread",
                    target_id=str(primary.id),
                    after_data={"fixture": "stable"},
                )
            )
        await session.commit()

    # Use a fixed vector directly so non-AI E2E fixtures never call an embedding provider.
    if not vector_fixtures:
        return
    await vector_store.acreate_collection()
    async with vector_store._make_async_session() as vector_session:  # type: ignore[attr-defined]
        collection = await vector_store.aget_collection(vector_session)
        assert collection is not None
        for document, thread in vector_fixtures:
            vector_session.add(
                vector_store.EmbeddingStore(  # type: ignore[attr-defined]
                    id=str(uuid4()),
                    collection_id=collection.uuid,
                    embedding=[0.0],
                    document="e2e deterministic vector fixture",
                    cmetadata={
                        "id": str(uuid4()),
                        "file_name": document.file_name,
                        "document_id": str(document.id),
                        "thread_id": str(thread.id),
                        "user_id": str(member.id),
                    },
                )
            )
        await vector_session.commit()


async def prepare() -> None:
    await init_db()
    member = await _upsert_account("E2E_USER_EMAIL", "E2E_USER_PASSWORD", "e2e_member", "user")
    await _upsert_account("E2E_ADMIN_EMAIL", "E2E_ADMIN_PASSWORD", "e2e_admin", "admin")
    super_admin = await _upsert_account(
        "E2E_SUPER_ADMIN_EMAIL",
        "E2E_SUPER_ADMIN_PASSWORD",
        "e2e_super_admin",
        "super_admin",
    )
    await _seed_stable_records(member, super_admin)
    # uvicorn creates its own event loop after asyncio.run() returns.
    await vector_store._async_engine.dispose()  # type: ignore[attr-defined]
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(prepare())
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)
