import unicodedata
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.schemas import PersonnelProfileInput
from app.db.models import AuditLog, PersonnelProfile, User


def normalize_person_name(value: str) -> str:
    """Normalize a name without changing its displayed spelling."""
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _audit(
    session: AsyncSession,
    actor_id: UUID | None,
    action: str,
    target_id: str | None,
    after: dict[str, Any],
    ip_address: str | None = None,
) -> None:
    session.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            target_type="personnel_profile",
            target_id=target_id,
            after_data=after,
            ip_address=ip_address,
        )
    )


async def _user_or_404(session: AsyncSession, user_id: UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


async def profile_detail(session: AsyncSession, user_id: UUID) -> dict[str, Any]:
    user = await _user_or_404(session, user_id)
    profile = await session.get(PersonnelProfile, user.id)
    return {"profile": profile, "can_query_personnel": user.can_query_personnel}


async def upsert_profile(
    session: AsyncSession,
    actor: User,
    user_id: UUID,
    payload: PersonnelProfileInput,
    ip_address: str | None,
) -> PersonnelProfile:
    await _user_or_404(session, user_id)
    profile = await session.get(PersonnelProfile, user_id)
    values = payload.model_dump()
    values["normalized_name"] = normalize_person_name(values.pop("full_name"))
    full_name = payload.full_name
    try:
        if profile is None:
            profile = PersonnelProfile(user_id=user_id, full_name=full_name, **values)
            session.add(profile)
        else:
            profile.full_name = full_name
            for key, value in values.items():
                setattr(profile, key, value)
        _audit(session, actor.id, "personnel.profile.upsert", str(user_id), {"employment_status": profile.employment_status}, ip_address)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="员工工号已存在") from exc
    await session.refresh(profile)
    return profile


async def update_query_permission(
    session: AsyncSession,
    actor: User,
    user_id: UUID,
    enabled: bool,
    ip_address: str | None,
) -> User:
    target = await _user_or_404(session, user_id)
    if target.role not in {"admin", "super_admin"}:
        raise HTTPException(status_code=400, detail="只能向管理员或超级管理员授予人员查询权限")
    target.can_query_personnel = enabled
    _audit(session, actor.id, "personnel.permission.update", str(target.id), {"enabled": enabled}, ip_address)
    await session.commit()
    await session.refresh(target)
    return target


async def query_directory(
    session: AsyncSession,
    actor_id: UUID,
    person_name: str,
    employee_no: str | None = None,
    department: str | None = None,
    ip_address: str | None = None,
) -> dict[str, Any]:
    """Authorize and query the internal directory without exposing raw search terms in audits."""
    actor = await session.get(User, actor_id)
    if (
        actor is None
        or not actor.is_active
        or not actor.is_verified
        or actor.role not in {"admin", "super_admin"}
        or not actor.can_query_personnel
    ):
        _audit(session, actor_id if actor else None, "personnel.query.denied", None, {"result": "forbidden"}, ip_address)
        await session.commit()
        return {"event": "personnel_query", "status": "forbidden"}

    normalized_name = normalize_person_name(person_name)
    if not normalized_name:
        _audit(session, actor.id, "personnel.query.invalid", None, {"result": "invalid_request"}, ip_address)
        await session.commit()
        return {"event": "personnel_query", "status": "invalid_request"}

    filters = [PersonnelProfile.normalized_name == normalized_name]
    if employee_no and employee_no.strip():
        filters.append(PersonnelProfile.employee_no == " ".join(unicodedata.normalize("NFKC", employee_no).split()))
    if department and department.strip():
        normalized_department = " ".join(unicodedata.normalize("NFKC", department).split()).casefold()
        filters.append(func.lower(PersonnelProfile.department) == normalized_department)
    profiles = list((await session.execute(select(PersonnelProfile).where(*filters).order_by(PersonnelProfile.employee_no).limit(11))).scalars())
    if not profiles:
        _audit(session, actor.id, "personnel.query.not_found", None, {"result": "not_found"}, ip_address)
        await session.commit()
        return {"event": "personnel_query", "status": "not_found"}
    if len(profiles) != 1:
        candidates = [{"employee_no": profile.employee_no, "department": profile.department} for profile in profiles[:10]]
        _audit(session, actor.id, "personnel.query.ambiguous", None, {"result": "ambiguous", "candidate_count": len(candidates)}, ip_address)
        await session.commit()
        return {"event": "personnel_query", "status": "ambiguous", "candidates": candidates}

    profile = profiles[0]
    public_profile = {
        "full_name": profile.full_name,
        "employee_no": profile.employee_no,
        "department": profile.department,
        "job_title": profile.job_title,
        "work_email": profile.work_email,
        "work_phone": profile.work_phone,
        "employment_status": profile.employment_status,
    }
    _audit(session, actor.id, "personnel.query.found", str(profile.user_id), {"result": "found"}, ip_address)
    await session.commit()
    return {"event": "personnel_query", "status": "found", "profile": public_profile}
