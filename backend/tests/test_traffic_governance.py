import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError
from starlette.requests import Request

from app.chat import routes as chat_routes
from app.config import settings
from app.documents import routes as document_routes
from app.main import app
from app.traffic_governance import dependencies as governance_dependencies
from app.traffic_governance.core import (
    SLIDING_WINDOW_SCRIPT,
    TrafficPolicy,
    client_ip,
    create_redis_client,
    rate_limit_key,
)

from .test_auth_integration import bearer, login, signup


@pytest.mark.asyncio
async def test_ordinary_limit_returns_headers_and_is_scoped_per_user(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(governance_dependencies, "ORDINARY_POLICY", TrafficPolicy("ordinary_test", 2, "open"))
    first = await signup(client, "governance_one")
    second = await signup(client, "governance_two")
    first_token = await login(client, "governance_one")
    second_token = await login(client, "governance_two")

    allowed_one = await client.get("/api/v1/users/me", headers=bearer(first_token))
    allowed_two = await client.get("/api/v1/users/me", headers=bearer(first_token))
    rejected = await client.get("/api/v1/users/me", headers=bearer(first_token))
    other_user = await client.get("/api/v1/users/me", headers=bearer(second_token))

    assert allowed_one.status_code == 200
    assert allowed_one.headers["ratelimit-limit"] == "2"
    assert allowed_two.headers["ratelimit-remaining"] == "0"
    assert rejected.status_code == 429
    assert rejected.json()["code"] == "rate_limit_exceeded"
    assert rejected.headers["retry-after"]
    assert other_user.status_code == 200


@pytest.mark.asyncio
async def test_agent_and_upload_rejections_precede_expensive_work(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    await signup(client, "gexpensive")
    token = await login(client, "gexpensive")
    headers = bearer(token)
    created = await client.post("/api/v1/threads/", headers=headers)
    assert created.status_code == 201
    thread_id = created.json()["id"]

    stream_calls = 0
    index_calls = 0

    async def fake_stream(*_args, **_kwargs):
        nonlocal stream_calls
        stream_calls += 1

        async def events():
            if False:
                yield None

        return events()

    async def fake_index(*_args, **_kwargs):
        nonlocal index_calls
        index_calls += 1
        return [str(uuid4())]

    monkeypatch.setattr(chat_routes.chat_service, "chat_stream", fake_stream)
    monkeypatch.setattr(document_routes, "index_document_to_pgvector", fake_index)

    for attempt in range(settings.rate_limit_agent_requests):
        allowed = await client.post(
            f"/api/v1/chat/{thread_id}",
            headers=headers,
            json={"prompt": f"hello-{attempt}", "model_name": "test-model"},
        )
        assert allowed.status_code == 200
        assert allowed.headers["ratelimit-limit"] == str(settings.rate_limit_agent_requests)
        assert allowed.headers["content-type"].startswith("application/x-ndjson")
        assert allowed.headers["x-accel-buffering"] == "no"
    agent_rejected = await client.post(f"/api/v1/chat/{thread_id}", headers=headers, json={"prompt": "again", "model_name": "test-model"})
    assert agent_rejected.status_code == 429
    assert agent_rejected.json()["policy"] == "agent"
    assert stream_calls == settings.rate_limit_agent_requests

    for attempt in range(settings.rate_limit_upload_requests):
        allowed = await client.post(
            f"/api/v1/documents/upload/{thread_id}",
            headers=headers,
            files={"file": (f"{attempt}.txt", b"one", "text/plain")},
        )
        assert allowed.status_code == 200
    upload_rejected = await client.post(f"/api/v1/documents/upload/{thread_id}", headers=headers, files={"file": ("two.txt", b"two", "text/plain")})
    assert upload_rejected.status_code == 429
    assert upload_rejected.json()["policy"] == "upload"
    assert index_calls == settings.rate_limit_upload_requests


@pytest.mark.asyncio
async def test_redis_outage_degrades_ordinary_but_rejects_agent(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    await signup(client, "goutage")
    token = await login(client, "goutage")
    headers = bearer(token)

    class UnavailableRedis:
        async def eval(self, *_args, **_kwargs):
            raise RedisConnectionError("unavailable")

    app.state.traffic_redis = UnavailableRedis()
    ordinary = await client.get("/api/v1/users/me", headers=headers)
    agent = await client.post(f"/api/v1/chat/{uuid4()}", headers=headers, json={"prompt": "hello", "model_name": "test-model"})
    assert ordinary.status_code == 200
    assert agent.status_code == 503
    assert agent.json()["code"] == "traffic_governance_unavailable"
    assert agent.headers["retry-after"] == "5"


@pytest.mark.asyncio
async def test_default_ordinary_limit_rejects_the_sixty_first_request(client: AsyncClient):
    await signup(client, "ordinary_default")
    token = await login(client, "ordinary_default")
    headers = bearer(token)

    for _ in range(settings.rate_limit_ordinary_requests):
        assert (await client.get("/api/v1/users/me", headers=headers)).status_code == 200

    rejected = await client.get("/api/v1/users/me", headers=headers)
    assert rejected.status_code == 429
    body = rejected.json()
    assert body == {
        "code": "rate_limit_exceeded",
        "detail": f"请求过于频繁，请在 {body['retry_after']} 秒后重试。",
        "policy": "ordinary",
        "retry_after": body["retry_after"],
    }
    assert rejected.headers["ratelimit-limit"] == str(settings.rate_limit_ordinary_requests)
    assert rejected.headers["ratelimit-remaining"] == "0"
    assert rejected.headers["ratelimit-reset"] == rejected.headers["retry-after"]


@pytest.mark.asyncio
async def test_lua_window_expires_and_a_denial_does_not_refresh_ttl():
    redis = create_redis_client()
    policy = TrafficPolicy("ttl_test", 1, "open")
    key = rate_limit_key(policy, "ttl-subject")
    try:
        await redis.delete(key)
        allowed = await redis.eval(SLIDING_WINDOW_SCRIPT, 1, key, 1, 250, "allowed")
        assert int(allowed[0]) == 1
        await asyncio.sleep(0.05)
        before = await redis.pttl(key)
        denied = await redis.eval(SLIDING_WINDOW_SCRIPT, 1, key, 1, 250, "denied")
        after = await redis.pttl(key)
        assert int(denied[0]) == 0
        # Redis rounds PTTL to milliseconds, so an immediate non-refreshing
        # command may observe the same value.  A refreshed TTL would jump back
        # to the full 250ms and therefore be greater than ``before``.
        assert 0 < after <= before
        await asyncio.sleep(0.3)
        assert await redis.exists(key) == 0
        restored = await redis.eval(SLIDING_WINDOW_SCRIPT, 1, key, 1, 250, "restored")
        assert int(restored[0]) == 1
    finally:
        await redis.delete(key)
        await redis.aclose()


@pytest.mark.asyncio
async def test_two_redis_clients_cannot_exceed_one_shared_limit():
    first = create_redis_client()
    second = create_redis_client()
    policy = TrafficPolicy("concurrency_test", 10, "open")
    key = rate_limit_key(policy, "shared-subject")
    try:
        await first.delete(key)

        async def attempt(index: int) -> int:
            redis = first if index % 2 else second
            result = await redis.eval(SLIDING_WINDOW_SCRIPT, 1, key, 10, 5_000, f"member-{index}")
            return int(result[0])

        outcomes = await asyncio.gather(*(attempt(index) for index in range(40)))
        assert sum(outcomes) == 10

        other_identity = await first.eval(
            SLIDING_WINDOW_SCRIPT, 1, rate_limit_key(policy, "other-subject"), 10, 5_000, "other-member"
        )
        other_policy = TrafficPolicy("concurrency_other_policy", 10, "open")
        other_policy_result = await first.eval(
            SLIDING_WINDOW_SCRIPT, 1, rate_limit_key(other_policy, "shared-subject"), 10, 5_000, "other-policy-member"
        )
        assert int(other_identity[0]) == 1
        assert int(other_policy_result[0]) == 1
    finally:
        keys = [
            key,
            rate_limit_key(policy, "other-subject"),
            rate_limit_key(TrafficPolicy("concurrency_other_policy", 10, "open"), "shared-subject"),
        ]
        await first.delete(*keys)
        await first.aclose()
        await second.aclose()


@pytest.mark.asyncio
async def test_login_uses_shared_normalized_account_and_ip_quotas(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(governance_dependencies, "LOGIN_IP_POLICY", TrafficPolicy("login_ip_test", 10, "closed"))
    monkeypatch.setattr(governance_dependencies, "LOGIN_ACCOUNT_POLICY", TrafficPolicy("login_account_test", 2, "closed"))

    first = await client.post("/api/v1/auth/login", data={"username": "  MIXED@EXAMPLE.COM ", "password": "wrong"})
    second = await client.post("/api/v1/auth/admin/login", data={"username": "mixed@example.com", "password": "wrong"})
    third = await client.post("/api/v1/auth/login", data={"username": "mixed@example.com", "password": "wrong"})
    assert first.status_code == second.status_code == 401
    assert third.status_code == 429
    assert third.json()["policy"] == "login_account_test"

    monkeypatch.setattr(governance_dependencies, "LOGIN_IP_POLICY", TrafficPolicy("login_ip_limited_test", 2, "closed"))
    monkeypatch.setattr(governance_dependencies, "LOGIN_ACCOUNT_POLICY", TrafficPolicy("login_account_open_test", 10, "closed"))
    assert (await client.post("/api/v1/auth/login", data={"username": "one@example.com", "password": "wrong"})).status_code == 401
    assert (await client.post("/api/v1/auth/login", data={"username": "two@example.com", "password": "wrong"})).status_code == 401
    rejected = await client.post("/api/v1/auth/login", data={"username": "three@example.com", "password": "wrong"})
    assert rejected.status_code == 429
    assert rejected.json()["policy"] == "login_ip_limited_test"


@pytest.mark.asyncio
async def test_refresh_uses_session_then_ip_fallback_and_logout_is_exempt(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(governance_dependencies, "REFRESH_SESSION_POLICY", TrafficPolicy("refresh_session_test", 2, "open"))
    monkeypatch.setattr(governance_dependencies, "REFRESH_IP_POLICY", TrafficPolicy("refresh_ip_test", 2, "open"))
    await signup(client, "refresh_policy")
    await login(client, "refresh_policy")

    assert (await client.post("/api/v1/auth/refresh-token")).status_code == 200
    assert (await client.post("/api/v1/auth/refresh-token")).status_code == 200
    session_rejected = await client.post("/api/v1/auth/refresh-token")
    assert session_rejected.status_code == 429
    assert session_rejected.json()["policy"] == "refresh_session_test"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as no_cookie:
        assert (await no_cookie.post("/api/v1/auth/refresh-token")).status_code == 401
        assert (await no_cookie.post("/api/v1/auth/refresh-token")).status_code == 401
        ip_rejected = await no_cookie.post("/api/v1/auth/refresh-token")
        assert ip_rejected.status_code == 429
        assert ip_rejected.json()["policy"] == "refresh_ip_test"

    class UnavailableRedis:
        async def eval(self, *_args, **_kwargs):
            raise RedisConnectionError("unavailable")

    app.state.traffic_redis = UnavailableRedis()
    assert (await client.post("/api/v1/auth/logout")).status_code == 200


@pytest.mark.asyncio
async def test_redis_outage_applies_every_policy_and_health_reports_degraded(client: AsyncClient):
    from .test_auth_integration import set_role

    await signup(client, "outage_user")
    await signup(client, "outage_admin")
    await set_role("outage_admin@example.com", "admin")
    user = await login(client, "outage_user")
    admin = await login(client, "outage_admin", admin=True)
    thread = await client.post("/api/v1/threads/", headers=bearer(user))
    assert thread.status_code == 201

    class UnavailableRedis:
        async def eval(self, *_args, **_kwargs):
            raise RedisConnectionError("unavailable")

        async def ping(self):
            raise RedisConnectionError("unavailable")

    app.state.traffic_redis = UnavailableRedis()
    assert (await client.get("/api/v1/users/me", headers=bearer(user))).status_code == 200
    assert (await client.get("/api/v1/config/public")).status_code == 200
    assert (await client.post("/api/v1/auth/refresh-token")).status_code == 200

    for response in (
        await client.post("/api/v1/auth/signup", json={"username": "outage_new", "email": "outage_new@example.com", "password": "Integration123!", "first_name": "Test", "last_name": "User"}),
        await client.post("/api/v1/auth/login", data={"username": "outage_user@example.com", "password": "Integration123!"}),
        await client.post(f"/api/v1/chat/{thread.json()['id']}", headers=bearer(user), json={"prompt": "blocked", "model_name": "test-model"}),
        await client.post(f"/api/v1/documents/upload/{thread.json()['id']}", headers=bearer(user), files={"file": ("blocked.txt", b"blocked", "text/plain")}),
    ):
        assert response.status_code == 503
        assert response.json()["code"] == "traffic_governance_unavailable"
        assert response.headers["retry-after"] == "5"

    health = await client.get("/api/v1/admin/health", headers=bearer(admin))
    assert health.status_code == 200
    assert health.json()["redis"] == "unavailable"
    assert health.json()["traffic_governance"] == "degraded"


@pytest.mark.asyncio
async def test_cors_exposes_rate_limit_headers(client: AsyncClient):
    response = await client.get("/api/v1/config/public", headers={"Origin": "http://test"})
    assert response.status_code == 200
    exposed = {value.strip().lower() for value in response.headers["access-control-expose-headers"].split(",")}
    assert {"ratelimit-limit", "ratelimit-remaining", "ratelimit-reset", "retry-after"} <= exposed


def test_client_ip_only_honors_forwarded_header_from_trusted_proxy():
    direct = Request({"type": "http", "headers": [(b"x-forwarded-for", b"203.0.113.10")], "client": ("198.51.100.4", 1234)})
    trusted = Request({"type": "http", "headers": [(b"x-forwarded-for", b"203.0.113.10, 127.0.0.1")], "client": ("127.0.0.1", 1234)})
    ipv6 = Request({"type": "http", "headers": [], "client": ("2001:db8::9", 1234)})
    all_trusted = Request({"type": "http", "headers": [(b"x-forwarded-for", b"::1, 127.0.0.1")], "client": ("127.0.0.1", 1234)})
    invalid = Request({"type": "http", "headers": [(b"x-forwarded-for", b"invalid, 127.0.0.1")], "client": ("127.0.0.1", 1234)})
    missing = Request({"type": "http", "headers": []})
    assert client_ip(direct) == "198.51.100.4"
    assert client_ip(trusted) == "203.0.113.10"
    assert client_ip(ipv6) == "2001:db8::9"
    assert client_ip(all_trusted) == "127.0.0.1"
    assert client_ip(invalid) == "127.0.0.1"
    assert client_ip(missing) == "unknown"


def test_governance_log_uses_normalized_route_and_never_raw_identity(monkeypatch: pytest.MonkeyPatch):
    captured: list[tuple[object, ...]] = []

    def capture(*args, **_kwargs):
        captured.append(args)

    monkeypatch.setattr(governance_dependencies.logger, "warning", capture)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"/api/v1/chat/{uuid4()}",
            "headers": [],
            "client": ("198.51.100.4", 1234),
            "route": SimpleNamespace(path="/api/v1/chat/{thread_id}"),
        }
    )
    governance_dependencies._log("traffic_governance.rejected", request, TrafficPolicy("agent", 10, "closed"), "member@example.com", 4)
    assert captured
    values = captured[0]
    assert values[5] == "/api/v1/chat/{thread_id}"
    assert "member@example.com" not in values
