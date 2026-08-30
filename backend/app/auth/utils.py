import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

import bcrypt
import jwt
from loguru import logger
from pydantic import ValidationError

from app.config import settings

from .schemas import TokenData


def hash_password(password: str) -> str:
    pwd_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    hashed_password = bcrypt.hashpw(password=pwd_bytes, salt=salt)
    return hashed_password.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    plain_password_byte_enc = plain_password.encode("utf-8")
    hash_password_byte_enc = hashed_password.encode("utf-8")

    return bcrypt.checkpw(password=plain_password_byte_enc, hashed_password=hash_password_byte_enc)


def create_jwt_token(
    user_data: dict,
    refresh: bool = False,
    *,
    session_id: uuid.UUID,
    surface: Literal["user", "admin"],
    token_id: uuid.UUID | None = None,
) -> str:
    if refresh:
        expiry = timedelta(days=settings.refresh_token_expiry_days)
    else:
        expiry = timedelta(minutes=settings.access_token_expiry_mins)

    payload = {
        "user": user_data,
        "exp": datetime.now(tz=UTC) + expiry,
        "jti": str(token_id or uuid.uuid4()),
        "refresh": refresh,
        "sid": str(session_id),
        "surface": surface,
    }
    token = jwt.encode(payload=payload, key=settings.jwt_secret, algorithm=settings.jwt_algorithm)

    return token


def decode_token(token: str) -> TokenData | None:
    try:
        token_data = jwt.decode(jwt=token, key=settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return TokenData(**token_data)
    except (jwt.PyJWTError, ValidationError) as e:
        logger.error(f"Error decoding jwt token: {e}")
        return None
