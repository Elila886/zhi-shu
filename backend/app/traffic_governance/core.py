from __future__ import annotations

import hashlib
import ipaddress
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from fastapi import Request
from loguru import logger
from redis import asyncio as redis_asyncio
from redis.exceptions import RedisError

from app.config import settings


FailureMode = Literal["open", "closed"]


@dataclass(frozen=True)
class TrafficPolicy:
    name: str
    limit: int
    failure_mode: FailureMode


@dataclass(frozen=True)
class LimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int


class TrafficGovernanceUnavailable(RuntimeError):
    """Raised when Redis cannot make an authoritative admission decision."""


SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local member = ARGV[3]

local redis_time = redis.call('TIME')
local now_ms = (tonumber(redis_time[1]) * 1000) + math.floor(tonumber(redis_time[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - window_ms)

local current = redis.call('ZCARD', key)
if current >= limit then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry_ms = window_ms
    if oldest[2] ~= nil then
        retry_ms = math.max(1, math.ceil((tonumber(oldest[2]) + window_ms) - now_ms))
    end
    -- A rejected request must not refresh this key's lifetime.  The TTL is
    -- established only by successful admissions, so idle rate-limit state
    -- naturally disappears one window after its latest accepted request.
    return {0, limit, 0, math.max(1, math.ceil(retry_ms / 1000))}
end

redis.call('ZADD', key, now_ms, member)
redis.call('PEXPIRE', key, window_ms)
local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
local reset_ms = window_ms
if oldest[2] ~= nil then
    reset_ms = math.max(1, math.ceil((tonumber(oldest[2]) + window_ms) - now_ms))
end
return {1, limit, limit - current - 1, math.max(1, math.ceil(reset_ms / 1000))}
"""


ORDINARY_POLICY = TrafficPolicy("ordinary", settings.rate_limit_ordinary_requests, "open")
AGENT_POLICY = TrafficPolicy("agent", settings.rate_limit_agent_requests, "closed")
UPLOAD_POLICY = TrafficPolicy("upload", settings.rate_limit_upload_requests, "closed")
SIGNUP_POLICY = TrafficPolicy("signup", settings.rate_limit_signup_requests, "closed")
LOGIN_IP_POLICY = TrafficPolicy("login_ip", settings.rate_limit_login_ip_requests, "closed")
LOGIN_ACCOUNT_POLICY = TrafficPolicy("login_account", settings.rate_limit_login_account_requests, "closed")
REFRESH_SESSION_POLICY = TrafficPolicy("refresh_session", settings.rate_limit_refresh_session_requests, "open")
REFRESH_IP_POLICY = TrafficPolicy("refresh_ip", settings.rate_limit_refresh_ip_requests, "open")
PUBLIC_CONFIG_POLICY = TrafficPolicy("public_config", settings.rate_limit_public_config_requests, "open")


def create_redis_client() -> redis_asyncio.Redis:
    return redis_asyncio.from_url(settings.redis_url, decode_responses=True, health_check_interval=30)


def get_redis_client(request: Request) -> redis_asyncio.Redis:
    client = getattr(request.app.state, "traffic_redis", None)
    if client is None:
        # ASGITransport does not start the application lifespan by default.  A
        # lazy client keeps application dependencies correct in that context,
        # while normal service startup still owns the connection lifecycle.
        client = create_redis_client()
        request.app.state.traffic_redis = client
    return client


async def close_redis_client(client: redis_asyncio.Redis | None) -> None:
    if client is not None:
        await client.aclose()


def identity_digest(identity: str) -> str:
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def normalize_account(value: str) -> str:
    return value.strip().lower()


def rate_limit_key(policy: TrafficPolicy, identity: str) -> str:
    return f"{settings.rate_limit_key_prefix}:{policy.name}:{identity_digest(identity)}"


async def check_limit(request: Request, policy: TrafficPolicy, identity: str) -> LimitResult:
    try:
        result = await get_redis_client(request).eval(
            SLIDING_WINDOW_SCRIPT,
            1,
            rate_limit_key(policy, identity),
            policy.limit,
            settings.rate_limit_window_seconds * 1000,
            uuid4().hex,
        )
    except (RedisError, OSError, TimeoutError) as exc:
        raise TrafficGovernanceUnavailable from exc

    allowed, limit, remaining, reset_seconds = (int(value) for value in result)
    return LimitResult(bool(allowed), limit, remaining, reset_seconds)


def is_trusted_proxy(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(address in network for network in settings.trusted_proxy_networks)


def client_ip(request: Request) -> str:
    direct_host = request.client.host if request.client is not None else "unknown"
    if not is_trusted_proxy(direct_host):
        return direct_host

    forwarded_for = request.headers.get("x-forwarded-for")
    if not forwarded_for:
        return direct_host

    forwarded: list[str] = []
    for raw in forwarded_for.split(","):
        value = raw.strip()
        try:
            forwarded.append(str(ipaddress.ip_address(value)))
        except ValueError:
            logger.warning("traffic_governance.invalid_forwarded_for direct_host={}", direct_host)
            return direct_host

    for value in reversed(forwarded):
        if not is_trusted_proxy(value):
            return value
    # A chain made entirely of configured proxies does not identify a client.
    # Keep the conservative direct-address fallback instead of charging a
    # shared proxy address (or trusting a client-supplied leftmost value).
    return direct_host
