from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class TrafficGovernanceError(Exception):
    def __init__(self, status_code: int, code: str, detail: str, policy: str, retry_after: int, headers: dict[str, str]):
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.policy = policy
        self.retry_after = retry_after
        self.headers = headers

    @classmethod
    def exceeded(cls, policy: str, retry_after: int, headers: dict[str, str]) -> "TrafficGovernanceError":
        return cls(
            429,
            "rate_limit_exceeded",
            f"请求过于频繁，请在 {retry_after} 秒后重试。",
            policy,
            retry_after,
            {**headers, "Retry-After": str(retry_after)},
        )

    @classmethod
    def unavailable(cls, policy: str, retry_after: int) -> "TrafficGovernanceError":
        return cls(
            503,
            "traffic_governance_unavailable",
            "流量治理服务暂时不可用，请稍后再试。",
            policy,
            retry_after,
            {"Retry-After": str(retry_after)},
        )


async def traffic_governance_exception_handler(_: Request, exc: TrafficGovernanceError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.code,
            "detail": exc.detail,
            "policy": exc.policy,
            "retry_after": exc.retry_after,
        },
        headers=exc.headers,
    )
