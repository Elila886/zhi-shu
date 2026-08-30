import asyncio
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.main import async_session
from app.db.models import Document, User
from app.main import app


PASSWORD = "Integration123!"


async def signup(client: AsyncClient, name: str) -> dict:
    response = await client.post(
        "/api/v1/auth/signup",
        json={
            "username": name,
            "email": f"{name}@example.com",
            "password": PASSWORD,
            "first_name": "Test",
            "last_name": "User",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["user"]


async def login(client: AsyncClient, name: str, *, admin: bool = False, password: str = PASSWORD) -> dict:
    prefix = "/api/v1/auth/admin" if admin else "/api/v1/auth"
    response = await client.post(f"{prefix}/login", data={"username": f"{name}@example.com", "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def bearer(payload: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {payload['access_token']}"}


async def set_role(email: str, role: str) -> None:
    async with async_session() as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        user.role = role
        await session.commit()


@pytest.mark.asyncio
async def test_cookie_rotation_replay_and_idempotent_logout(client: AsyncClient):
    await signup(client, "rotate")
    await login(client, "rotate")
    set_cookie = client.cookies.get("zhishu_refresh")
    assert set_cookie

    refreshed = await client.post("/api/v1/auth/refresh-token")
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"]

    replay = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    replay.cookies.set("zhishu_refresh", set_cookie, path="/api/v1/auth")
    try:
        rejected = await replay.post("/api/v1/auth/refresh-token")
        assert rejected.status_code == 401
    finally:
        await replay.aclose()

    assert (await client.post("/api/v1/auth/refresh-token")).status_code == 401

    await login(client, "rotate")
    assert (await client.post("/api/v1/auth/logout")).status_code == 200
    assert (await client.post("/api/v1/auth/logout")).status_code == 200


@pytest.mark.asyncio
async def test_concurrent_refresh_allows_only_one_use(client: AsyncClient):
    await signup(client, "concurrent")
    await login(client, "concurrent")
    cookie = client.cookies.get("zhishu_refresh")
    assert cookie

    async def refresh_once() -> int:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as contender:
            contender.cookies.set("zhishu_refresh", cookie, path="/api/v1/auth")
            return (await contender.post("/api/v1/auth/refresh-token")).status_code

    statuses = await asyncio.gather(refresh_once(), refresh_once())
    assert sorted(statuses) == [200, 401]


@pytest.mark.asyncio
async def test_admin_refresh_rotation_replay_and_idempotent_logout(client: AsyncClient):
    await signup(client, "adminrotate")
    await set_role("adminrotate@example.com", "admin")
    await login(client, "adminrotate", admin=True)
    old_cookie = client.cookies.get("zhishu_admin_refresh")
    assert old_cookie

    refreshed = await client.post("/api/v1/auth/admin/refresh-token")
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as replay:
        replay.cookies.set("zhishu_admin_refresh", old_cookie, path="/api/v1/auth/admin")
        assert (await replay.post("/api/v1/auth/admin/refresh-token")).status_code == 401

    assert (await client.post("/api/v1/auth/admin/refresh-token")).status_code == 401
    await login(client, "adminrotate", admin=True)
    assert (await client.post("/api/v1/auth/admin/logout")).status_code == 200
    assert (await client.post("/api/v1/auth/admin/logout")).status_code == 200


@pytest.mark.asyncio
async def test_concurrent_admin_refresh_allows_only_one_use(client: AsyncClient):
    await signup(client, "adminconcurrent")
    await set_role("adminconcurrent@example.com", "admin")
    await login(client, "adminconcurrent", admin=True)
    cookie = client.cookies.get("zhishu_admin_refresh")
    assert cookie

    async def refresh_once() -> int:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as contender:
            contender.cookies.set("zhishu_admin_refresh", cookie, path="/api/v1/auth/admin")
            return (await contender.post("/api/v1/auth/admin/refresh-token")).status_code

    statuses = await asyncio.gather(refresh_once(), refresh_once())
    assert sorted(statuses) == [200, 401]


@pytest.mark.asyncio
async def test_user_and_admin_token_surfaces_are_isolated(client: AsyncClient):
    await signup(client, "member")
    await signup(client, "manager")
    await signup(client, "rootadmin")
    await set_role("manager@example.com", "admin")
    await set_role("rootadmin@example.com", "super_admin")

    member_login = await login(client, "member")
    assert (await client.get("/api/v1/admin/overview", headers=bearer(member_login))).status_code == 403
    assert (await client.post("/api/v1/auth/admin/login", data={"username": "member@example.com", "password": PASSWORD})).status_code == 403

    manager_user_login = await login(client, "manager")
    assert (await client.get("/api/v1/admin/overview", headers=bearer(manager_user_login))).status_code == 401

    manager_admin_login = await login(client, "manager", admin=True)
    assert (await client.get("/api/v1/admin/overview", headers=bearer(manager_admin_login))).status_code == 200
    admin_profile = await client.get("/api/v1/admin/me", headers=bearer(manager_admin_login))
    assert admin_profile.status_code == 200
    assert admin_profile.json()["role"] == "admin"
    assert (await client.get("/api/v1/users/me", headers=bearer(manager_admin_login))).status_code == 401
    assert (await client.get("/api/v1/admin/audit-logs", headers=bearer(manager_admin_login))).status_code == 403

    root_login = await login(client, "rootadmin", admin=True)
    assert (await client.get("/api/v1/admin/audit-logs", headers=bearer(root_login))).status_code == 200


@pytest.mark.asyncio
async def test_disable_enable_and_password_reset_revoke_existing_sessions(client: AsyncClient):
    target = await signup(client, "target")
    await signup(client, "rootreset")
    await set_role("rootreset@example.com", "super_admin")
    target_login = await login(client, "target")
    root_login = await login(client, "rootreset", admin=True)

    disabled = await client.patch(
        f"/api/v1/admin/users/{target['id']}",
        headers=bearer(root_login),
        json={"is_active": False, "disabled_reason": "integration test"},
    )
    assert disabled.status_code == 200
    assert (await client.get("/api/v1/users/me", headers=bearer(target_login))).status_code == 401
    assert (await client.post("/api/v1/auth/login", data={"username": "target@example.com", "password": PASSWORD})).status_code == 403

    enabled = await client.patch(
        f"/api/v1/admin/users/{target['id']}",
        headers=bearer(root_login),
        json={"is_active": True},
    )
    assert enabled.status_code == 200
    restored_login = await login(client, "target")
    old_cookie = client.cookies.get("zhishu_refresh")
    assert old_cookie

    replacement_password = "Replacement123!"
    reset = await client.post(
        f"/api/v1/admin/users/{target['id']}/reset-password",
        headers=bearer(root_login),
        json={"new_password": replacement_password},
    )
    assert reset.status_code == 200
    assert (await client.get("/api/v1/users/me", headers=bearer(restored_login))).status_code == 401

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as replay:
        replay.cookies.set("zhishu_refresh", old_cookie, path="/api/v1/auth")
        assert (await replay.post("/api/v1/auth/refresh-token")).status_code == 401

    old_password = await client.post(
        "/api/v1/auth/login", data={"username": "target@example.com", "password": PASSWORD}
    )
    assert old_password.status_code == 401
    replacement_login = await login(client, "target", password=replacement_password)
    assert (await client.get("/api/v1/users/me", headers=bearer(replacement_login))).status_code == 200


@pytest.mark.asyncio
async def test_admin_role_and_status_permissions_are_backend_enforced(client: AsyncClient):
    await signup(client, "member")
    await signup(client, "manager")
    await signup(client, "root")
    await set_role("manager@example.com", "admin")
    await set_role("root@example.com", "super_admin")

    manager = await login(client, "manager", admin=True)
    root = await login(client, "root", admin=True)
    member = await signup(client, "managed")

    denied_role = await client.patch(
        f"/api/v1/admin/users/{member['id']}",
        headers=bearer(manager),
        json={"role": "admin"},
    )
    assert denied_role.status_code == 403

    disabled = await client.patch(
        f"/api/v1/admin/users/{member['id']}",
        headers=bearer(manager),
        json={"is_active": False, "disabled_reason": "policy"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["is_active"] is False

    promoted = await client.patch(
        f"/api/v1/admin/users/{member['id']}",
        headers=bearer(root),
        json={"role": "admin", "is_active": True},
    )
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "admin"
    assert promoted.json()["is_active"] is True

    manager_self_disable = await client.patch(
        f"/api/v1/admin/users/{(await client.get('/api/v1/admin/me', headers=bearer(manager))).json()['id']}",
        headers=bearer(manager),
        json={"is_active": False, "disabled_reason": "self"},
    )
    assert manager_self_disable.status_code == 400



@pytest.mark.asyncio
async def test_thread_and_document_ownership_isolation(client: AsyncClient):
    await signup(client, "owner")
    await signup(client, "intruder")
    owner_login = await login(client, "owner")
    intruder_login = await login(client, "intruder")
    created = await client.post("/api/v1/threads/", headers=bearer(owner_login))
    assert created.status_code == 201
    thread_id = created.json()["id"]

    async with async_session() as session:
        document = Document(file_name="private.txt", thread_id=UUID(thread_id), status="completed")
        session.add(document)
        await session.commit()
        await session.refresh(document)
        document_id = document.id

    owner_thread = await client.get(f"/api/v1/threads/{thread_id}", headers=bearer(owner_login))
    assert owner_thread.status_code == 200
    assert (await client.get(f"/api/v1/documents/{thread_id}", headers=bearer(owner_login))).status_code == 200
    renamed = await client.patch(
        f"/api/v1/threads/{thread_id}", headers=bearer(owner_login), json={"title": "owner title"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "owner title"

    assert (await client.get(f"/api/v1/threads/{thread_id}", headers=bearer(intruder_login))).status_code == 403
    assert (await client.get(f"/api/v1/documents/{thread_id}", headers=bearer(intruder_login))).status_code == 403
    assert (await client.delete(f"/api/v1/documents/{document_id}", headers=bearer(intruder_login))).status_code == 403

    # Every thread-facing mutation and chat entrypoint must enforce ownership
    # before touching model, checkpointer, or document storage state.
    assert (
        await client.patch(
            f"/api/v1/threads/{thread_id}",
            headers=bearer(intruder_login),
            json={"title": "intruder"},
        )
    ).status_code == 403
    assert (await client.delete(f"/api/v1/threads/{thread_id}", headers=bearer(intruder_login))).status_code == 403
    assert (
        await client.get(f"/api/v1/chat/{thread_id}", headers=bearer(intruder_login))
    ).status_code == 403
    assert (
        await client.post(
            f"/api/v1/chat/{thread_id}",
            headers=bearer(intruder_login),
            json={"prompt": "private", "model_name": "test-model"},
        )
    ).status_code == 403
    assert (
        await client.post(
            f"/api/v1/documents/upload/{thread_id}",
            headers=bearer(intruder_login),
            files={"file": ("private.txt", b"no access", "text/plain")},
        )
    ).status_code == 403


@pytest.mark.asyncio
async def test_anonymous_chat_endpoint_is_not_registered(client: AsyncClient):
    response = await client.post("/api/v1/chat/", json={"prompt": "hello", "model_name": "test-model"})
    assert response.status_code in {404, 405}


@pytest.mark.asyncio
async def test_malformed_and_wrong_surface_access_tokens_are_rejected(client: AsyncClient):
    await signup(client, "tokenmember")
    member = await login(client, "tokenmember")
    assert (await client.get("/api/v1/users/me", headers={"Authorization": "Bearer malformed"})).status_code == 401
    assert (await client.get("/api/v1/admin/overview", headers=bearer(member))).status_code == 403
