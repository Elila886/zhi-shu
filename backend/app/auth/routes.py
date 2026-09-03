from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.config import settings
from app.db.main import SessionDep
from app.db.models import AuditLog
from app.users import service as user_service
from app.users.schemas import UserCreate

from .dependencies import OAuth2PasswordRequestFormDep
from app.traffic_governance.dependencies import LoginRateLimitDep, RefreshRateLimitDep, SignupRateLimitDep
from .schemas import LoginResponse, LogoutResponse, RefreshTokenResponse, SignupResponse, TokenData
from .session_service import (
    create_refresh_session,
    get_active_session,
    revoke_session,
    rotate_refresh_session,
)
from .utils import create_jwt_token, decode_token, verify_password

auth_router = APIRouter()


def _cookie_settings(surface: str) -> tuple[str, str]:
    if surface == "admin":
        return settings.admin_refresh_cookie_name, "/api/v1/auth/admin"
    return settings.refresh_cookie_name, "/api/v1/auth"


def _set_refresh_cookie(response: Response, token: str, surface: str) -> None:
    name, path = _cookie_settings(surface)
    response.set_cookie(
        key=name,
        value=token,
        max_age=settings.refresh_token_expiry_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path=path,
    )


def _clear_refresh_cookie(response: Response, surface: str) -> None:
    name, path = _cookie_settings(surface)
    response.delete_cookie(
        key=name,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path=path,
    )


async def _authenticate(
    email: str,
    password: str,
    surface: str,
    request: Request,
    session: SessionDep,
) -> tuple[dict, str]:
    user = await user_service.get_user_by_email(email, session)
    if user is None or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Your account is not active")
    if not user.is_verified:
        raise HTTPException(status_code=403, detail="Your account is not verified")
    if surface == "admin" and user.role not in {"admin", "super_admin"}:
        raise HTTPException(status_code=403, detail="Administrator permission required")

    user.last_login_at = datetime.now(UTC).replace(tzinfo=None)
    if user.role in {"admin", "super_admin"}:
        session.add(
            AuditLog(
                actor_id=user.id,
                action="admin.login",
                target_type="user",
                target_id=str(user.id),
                after_data={"email": user.email, "role": user.role, "surface": surface},
                ip_address=request.client.host if request.client else None,
            )
        )

    refresh_session, refresh_jti = await create_refresh_session(session, user.id, surface)
    user_data = {"email": user.email, "id": str(user.id)}
    access_token = create_jwt_token(user_data, session_id=refresh_session.id, surface=surface)
    refresh_token = create_jwt_token(
        user_data,
        refresh=True,
        session_id=refresh_session.id,
        surface=surface,
        token_id=refresh_jti,
    )
    await session.commit()
    return (
        {
            "message": "Login successful",
            "access_token": access_token,
            "user": {"email": user.email, "id": str(user.id), "username": user.username, "role": user.role},
        },
        refresh_token,
    )


def _decode_refresh_cookie(request: Request, surface: str) -> TokenData:
    cookie_name, _ = _cookie_settings(surface)
    raw_token = request.cookies.get(cookie_name)
    token_data = decode_token(raw_token) if raw_token else None
    if token_data is None or not token_data.refresh or token_data.surface != surface:
        raise HTTPException(status_code=401, detail="Refresh session required")
    return token_data


async def _refresh_from_cookie(request: Request, response: Response, surface: str, session: SessionDep) -> dict:
    token_data = _decode_refresh_cookie(request, surface)
    refresh_session = await get_active_session(
        session,
        token_data,
        expected_surface=surface,
        require_current_jti=True,
    )
    user = await user_service.get_user_by_email(token_data.user.email, session)
    if user is None or not user.is_active or not user.is_verified:
        await revoke_session(session, token_data.sid)
        await session.commit()
        raise HTTPException(status_code=401, detail="Session is no longer valid")
    if surface == "admin" and user.role not in {"admin", "super_admin"}:
        await revoke_session(session, token_data.sid)
        await session.commit()
        raise HTTPException(status_code=403, detail="Administrator permission required")

    refresh_jti = await rotate_refresh_session(session, refresh_session)
    user_data = {"email": user.email, "id": str(user.id)}
    access_token = create_jwt_token(user_data, session_id=refresh_session.id, surface=surface)
    refresh_token = create_jwt_token(
        user_data,
        refresh=True,
        session_id=refresh_session.id,
        surface=surface,
        token_id=refresh_jti,
    )
    await session.commit()
    _set_refresh_cookie(response, refresh_token, surface)
    return {"access_token": access_token}


async def _logout_cookie(request: Request, response: Response, surface: str, session: SessionDep) -> dict:
    cookie_name, _ = _cookie_settings(surface)
    raw_token = request.cookies.get(cookie_name)
    token_data = decode_token(raw_token) if raw_token else None
    if token_data is not None and token_data.refresh and token_data.surface == surface:
        await revoke_session(session, token_data.sid)
        await session.commit()
    _clear_refresh_cookie(response, surface)
    return {"message": "Logged out successfully"}


@auth_router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
async def create_user_account(user_data: UserCreate, session: SessionDep, _: SignupRateLimitDep):
    new_user = await user_service.create_user(user_data, session)
    return {"message": "Account Created!", "user": new_user}


@auth_router.post("/login", response_model=LoginResponse)
async def login_users(
    response: Response,
    form_data: OAuth2PasswordRequestFormDep,
    session: SessionDep,
    request: Request,
    _: LoginRateLimitDep,
):
    payload, refresh_token = await _authenticate(form_data.username, form_data.password, "user", request, session)
    _set_refresh_cookie(response, refresh_token, "user")
    return payload


@auth_router.post("/refresh-token", response_model=RefreshTokenResponse)
async def refresh_user_session(request: Request, response: Response, session: SessionDep, _: RefreshRateLimitDep):
    return await _refresh_from_cookie(request, response, "user", session)


@auth_router.post("/logout", response_model=LogoutResponse)
async def logout_user_session(request: Request, response: Response, session: SessionDep):
    return await _logout_cookie(request, response, "user", session)


@auth_router.post("/admin/login", response_model=LoginResponse)
async def login_admin(
    response: Response,
    form_data: OAuth2PasswordRequestFormDep,
    session: SessionDep,
    request: Request,
    _: LoginRateLimitDep,
):
    payload, refresh_token = await _authenticate(form_data.username, form_data.password, "admin", request, session)
    _set_refresh_cookie(response, refresh_token, "admin")
    return payload


@auth_router.post("/admin/refresh-token", response_model=RefreshTokenResponse)
async def refresh_admin_session(request: Request, response: Response, session: SessionDep, _: RefreshRateLimitDep):
    return await _refresh_from_cookie(request, response, "admin", session)


@auth_router.post("/admin/logout", response_model=LogoutResponse)
async def logout_admin_session(request: Request, response: Response, session: SessionDep):
    return await _logout_cookie(request, response, "admin", session)
