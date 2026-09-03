from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from app.config import settings
from app.traffic_governance.middleware import RateLimitHeadersMiddleware


def register_middleware(app: FastAPI):
    app.add_middleware(RateLimitHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.frontend_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
        expose_headers=["RateLimit-Limit", "RateLimit-Remaining", "RateLimit-Reset", "Retry-After"],
    )

    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["localhost", "127.0.0.1", "test", "*"],
    )
