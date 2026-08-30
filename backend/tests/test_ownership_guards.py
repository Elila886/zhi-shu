"""Prove cross-user requests stop before side-effecting integrations."""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.main import async_session
from app.db.models import Document, Thread, User

from .test_auth_integration import bearer, login, signup


async def _empty_stream() -> AsyncIterator[None]:
    if False:
        yield None


async def _document_for(thread_id: str) -> Document:
    async with async_session() as session:
        document = Document(file_name="owner.txt", thread_id=UUID(thread_id), status="completed")
        session.add(document)
        await session.commit()
        await session.refresh(document)
        return document


@pytest.mark.asyncio
async def test_cross_user_ownership_checks_precede_all_external_effects(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    """Each rejected endpoint has an owner-success control and no integration call."""
    from app.chat import routes as chat_routes
    from app.documents import routes as document_routes
    from app.threads import routes as thread_routes
    from app.threads import service as thread_service

    await signup(client, "ownerproof")
    await signup(client, "intruderproof")
    owner = await login(client, "ownerproof")
    intruder = await login(client, "intruderproof")
    created = await client.post("/api/v1/threads/", headers=bearer(owner))
    assert created.status_code == 201
    thread_id = created.json()["id"]
    document = await _document_for(thread_id)

    get_checkpointer = AsyncMock(name="get_checkpointer")
    delete_thread_vectors = AsyncMock(name="delete_thread_vectors", return_value=0)
    delete_document_vectors = AsyncMock(name="delete_document_vectors", return_value=0)
    index_document = AsyncMock(name="index_document", return_value=[])
    chat_history = AsyncMock(name="chat_history", return_value=[])
    chat_stream = AsyncMock(name="chat_stream", return_value=_empty_stream())
    monkeypatch.setattr(thread_routes, "get_checkpointer", get_checkpointer)
    monkeypatch.setattr(thread_service, "delete_document_chunks_by_thread_id", delete_thread_vectors)
    monkeypatch.setattr(document_routes, "delete_document_chunks_by_document_id", delete_document_vectors)
    monkeypatch.setattr(document_routes, "index_document_to_pgvector", index_document)
    monkeypatch.setattr(chat_routes.chat_service, "get_chat_history", chat_history)
    monkeypatch.setattr(chat_routes.chat_service, "chat_stream", chat_stream)

    denied = bearer(intruder)
    assert (await client.get(f"/api/v1/threads/{thread_id}", headers=denied)).status_code == 403
    assert (await client.patch(f"/api/v1/threads/{thread_id}", headers=denied, json={"title": "no"})).status_code == 403
    assert (await client.delete(f"/api/v1/threads/{thread_id}", headers=denied)).status_code == 403
    assert (await client.get(f"/api/v1/documents/{thread_id}", headers=denied)).status_code == 403
    assert (await client.delete(f"/api/v1/documents/{document.id}", headers=denied)).status_code == 403
    assert (await client.post(
        f"/api/v1/documents/upload/{thread_id}", headers=denied,
        files={"file": ("blocked.txt", b"blocked", "text/plain")},
    )).status_code == 403
    assert (await client.get(f"/api/v1/chat/{thread_id}", headers=denied)).status_code == 403
    assert (await client.post(
        f"/api/v1/chat/{thread_id}", headers=denied,
        json={"prompt": "blocked", "model_name": "test-model"},
    )).status_code == 403

    get_checkpointer.assert_not_awaited()
    delete_thread_vectors.assert_not_awaited()
    delete_document_vectors.assert_not_awaited()
    index_document.assert_not_awaited()
    chat_history.assert_not_awaited()
    chat_stream.assert_not_awaited()

    # Owner controls prove the routes are reachable; mocked integrations stay
    # local so this suite is evidence about authorization ordering, not AI.
    assert (await client.get(f"/api/v1/threads/{thread_id}", headers=bearer(owner))).status_code == 200
    assert (await client.patch(f"/api/v1/threads/{thread_id}", headers=bearer(owner), json={"title": "owner"})).status_code == 200
    assert (await client.get(f"/api/v1/documents/{thread_id}", headers=bearer(owner))).status_code == 200
    assert (await client.get(f"/api/v1/chat/{thread_id}", headers=bearer(owner))).status_code == 200
    assert (await client.post(
        f"/api/v1/chat/{thread_id}", headers=bearer(owner),
        json={"prompt": "owner", "model_name": "test-model"},
    )).status_code == 200
    assert (await client.post(
        f"/api/v1/documents/upload/{thread_id}", headers=bearer(owner),
        files={"file": ("owner.txt", b"owner", "text/plain")},
    )).status_code == 200
    assert (await client.delete(f"/api/v1/documents/{document.id}", headers=bearer(owner))).status_code == 200

    assert chat_history.await_count == 1
    assert chat_stream.await_count == 1
    assert index_document.await_count == 1
    assert delete_document_vectors.await_count == 1


@pytest.mark.asyncio
async def test_login_and_refresh_cookie_attributes_and_invalid_cookies(client: AsyncClient):
    await signup(client, "cookiecontract")
    user_login = await client.post(
        "/api/v1/auth/login", data={"username": "cookiecontract@example.com", "password": "Integration123!"}
    )
    assert user_login.status_code == 200
    user_cookie = user_login.headers["set-cookie"].lower()
    for attribute in ("zhishu_refresh=", "httponly", "samesite=lax", "path=/api/v1/auth"):
        assert attribute in user_cookie
    assert "secure" not in user_cookie

    assert (await client.post("/api/v1/auth/refresh-token", cookies={"zhishu_refresh": "malformed"})).status_code == 401
    assert (await client.post("/api/v1/auth/admin/refresh-token", cookies={"zhishu_admin_refresh": "malformed"})).status_code == 401

    async with async_session() as session:
        user = (await session.execute(select(User).where(User.email == "cookiecontract@example.com"))).scalar_one()
        user.role = "admin"
        await session.commit()
    admin_login = await client.post(
        "/api/v1/auth/admin/login", data={"username": "cookiecontract@example.com", "password": "Integration123!"}
    )
    assert admin_login.status_code == 200
    admin_cookie = admin_login.headers["set-cookie"].lower()
    for attribute in ("zhishu_admin_refresh=", "httponly", "samesite=lax", "path=/api/v1/auth/admin"):
        assert attribute in admin_cookie
