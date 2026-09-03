from __future__ import annotations

from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RateLimitHeadersMiddleware:
    """Append rate-limit headers without buffering a streaming response."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                state: dict[str, Any] = scope.get("state", {})
                configured = state.get("rate_limit_headers")
                if configured:
                    existing = {key.lower() for key, _ in message.get("headers", [])}
                    additions = [
                        (key.encode("latin-1"), value.encode("latin-1"))
                        for key, value in configured.items()
                        if key.lower().encode("latin-1") not in existing
                    ]
                    message = {**message, "headers": [*message.get("headers", []), *additions]}
            await send(message)

        await self.app(scope, receive, send_with_headers)
