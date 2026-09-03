from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from loguru import logger

from app.auth.dependencies import AdminUserDep, CurrentUserDep, OAuth2PasswordRequestFormDep
from app.auth.utils import decode_token
from app.config import settings

from .core import (
    AGENT_POLICY,
    LOGIN_ACCOUNT_POLICY,
    LOGIN_IP_POLICY,
    ORDINARY_POLICY,
    PUBLIC_CONFIG_POLICY,
    REFRESH_IP_POLICY,
    REFRESH_SESSION_POLICY,
    SIGNUP_POLICY,
    UPLOAD_POLICY,
    LimitResult,
    TrafficGovernanceUnavailable,
    TrafficPolicy,
    check_limit,
    client_ip,
    identity_digest,
    normalize_account,
)
from .errors import TrafficGovernanceError


def _headers(result: LimitResult) -> dict[str, str]:
    return {
        "RateLimit-Limit": str(result.limit),
        "RateLimit-Remaining": str(result.remaining),
        "RateLimit-Reset": str(result.reset_seconds),
    }


def _log(event: str, request: Request, policy: TrafficPolicy, identity: str, retry_after: int) -> None:
    route = request.scope.get("route")
    normalized_route = getattr(route, "path", None) or request.url.path
    logger.warning(
        "{} policy={} identity_hash={} method={} path={} retry_after={}",
        event,
        policy.name,
        identity_digest(identity),
        request.method,
        normalized_route,
        retry_after,
    )


async def enforce(request: Request, policy: TrafficPolicy, identity: str) -> None:
    try:
        result = await check_limit(request, policy, identity)
    except TrafficGovernanceUnavailable:
        _log("traffic_governance.unavailable", request, policy, identity, settings.rate_limit_failure_retry_after_seconds)
        if policy.failure_mode == "closed":
            raise TrafficGovernanceError.unavailable(policy.name, settings.rate_limit_failure_retry_after_seconds)
        return

    if not result.allowed:
        _log("traffic_governance.rejected", request, policy, identity, result.reset_seconds)
        raise TrafficGovernanceError.exceeded(policy.name, result.reset_seconds, _headers(result))

    request.state.rate_limit_headers = _headers(result)


async def ordinary_rate_limit(request: Request, current_user: CurrentUserDep) -> None:
    await enforce(request, ORDINARY_POLICY, str(current_user.id))


async def agent_rate_limit(request: Request, current_user: CurrentUserDep) -> None:
    await enforce(request, AGENT_POLICY, str(current_user.id))


async def upload_rate_limit(request: Request, current_user: CurrentUserDep) -> None:
    await enforce(request, UPLOAD_POLICY, str(current_user.id))


async def admin_rate_limit(request: Request, admin_user: AdminUserDep) -> None:
    await enforce(request, ORDINARY_POLICY, str(admin_user.id))


async def signup_rate_limit(request: Request) -> None:
    await enforce(request, SIGNUP_POLICY, client_ip(request))


async def login_rate_limit(request: Request, form_data: OAuth2PasswordRequestFormDep) -> None:
    ip = client_ip(request)
    await enforce(request, LOGIN_IP_POLICY, ip)
    await enforce(request, LOGIN_ACCOUNT_POLICY, normalize_account(form_data.username))


async def refresh_rate_limit(request: Request) -> None:
    is_admin = request.url.path.startswith("/api/v1/auth/admin/")
    cookie_name = settings.admin_refresh_cookie_name if is_admin else settings.refresh_cookie_name
    token_data = decode_token(request.cookies.get(cookie_name))
    if token_data is not None and token_data.refresh and token_data.surface == ("admin" if is_admin else "user"):
        await enforce(request, REFRESH_SESSION_POLICY, str(token_data.sid))
    else:
        await enforce(request, REFRESH_IP_POLICY, client_ip(request))


async def public_config_rate_limit(request: Request) -> None:
    await enforce(request, PUBLIC_CONFIG_POLICY, client_ip(request))


OrdinaryRateLimitDep = Annotated[None, Depends(ordinary_rate_limit)]
AgentRateLimitDep = Annotated[None, Depends(agent_rate_limit)]
UploadRateLimitDep = Annotated[None, Depends(upload_rate_limit)]
AdminRateLimitDep = Annotated[None, Depends(admin_rate_limit)]
SignupRateLimitDep = Annotated[None, Depends(signup_rate_limit)]
LoginRateLimitDep = Annotated[None, Depends(login_rate_limit)]
RefreshRateLimitDep = Annotated[None, Depends(refresh_rate_limit)]
PublicConfigRateLimitDep = Annotated[None, Depends(public_config_rate_limit)]
