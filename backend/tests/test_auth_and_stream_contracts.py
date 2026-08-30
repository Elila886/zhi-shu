import json
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import jwt
from fastapi import Response

from app.auth.routes import _set_refresh_cookie
from app.auth.utils import create_jwt_token, decode_token
from app.chat.schemas import ChatStreamResponse
from app.config import settings


class AuthContractTests(unittest.TestCase):
    def test_tokens_include_server_session_and_surface(self):
        user_id = uuid4()
        session_id = uuid4()
        token_id = uuid4()
        token = create_jwt_token(
            {"email": "qa@example.com", "id": str(user_id)},
            refresh=True,
            session_id=session_id,
            surface="admin",
            token_id=token_id,
        )
        decoded = decode_token(token)
        self.assertIsNotNone(decoded)
        assert decoded is not None
        self.assertEqual(decoded.sid, session_id)
        self.assertEqual(decoded.jti, token_id)
        self.assertEqual(decoded.surface, "admin")
        self.assertTrue(decoded.refresh)

    def test_legacy_or_malformed_token_is_rejected_instead_of_raising(self):
        token = jwt.encode(
            {
                "user": {"email": "qa@example.com", "id": str(uuid4())},
                "exp": datetime.now(tz=UTC) + timedelta(minutes=1),
                "jti": str(uuid4()),
                "refresh": False,
            },
            key=settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        self.assertIsNone(decode_token(token))

    def test_refresh_cookie_is_http_only_and_scoped(self):
        response = Response()
        _set_refresh_cookie(response, "opaque-test-token", "user")
        header = response.headers["set-cookie"].lower()
        self.assertIn("httponly", header)
        self.assertIn("samesite=lax", header)
        self.assertIn("path=/api/v1/auth", header)

    def test_secure_cookie_flag_is_enabled_by_configuration(self):
        response = Response()
        with patch.object(settings, "cookie_secure", True):
            _set_refresh_cookie(response, "opaque-test-token", "admin")
        header = response.headers["set-cookie"].lower()
        self.assertIn("secure", header)
        self.assertIn("path=/api/v1/auth/admin", header)

    def test_credentialed_cors_rejects_wildcards_and_production_requires_secure_cookie(self):
        for origin in ["*", "", "http://test/path", "http://test?query=1", "http://test#fragment", "http://:bad"]:
            invalid = settings.model_copy(deep=True)
            invalid.frontend_origins = [origin]
            with self.assertRaisesRegex(ValueError, "Invalid credentialed CORS origin"):
                invalid.validate_browser_security()

        production = settings.model_copy(deep=True)
        production.environment = "production"
        production.cookie_secure = False
        with self.assertRaisesRegex(ValueError, "COOKIE_SECURE"):
            production.validate_browser_security()


class StreamContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_error_becomes_ndjson_error_event(self):
        async def broken_stream():
            if False:
                yield None
            raise RuntimeError("provider unavailable")

        response = ChatStreamResponse(broken_stream())
        chunks = [chunk async for chunk in response.process_stream(broken_stream())]
        event = json.loads(chunks[-1])
        self.assertEqual(event["type"], "error")
        self.assertIn("生成回答失败", event["content"])


if __name__ == "__main__":
    unittest.main()
