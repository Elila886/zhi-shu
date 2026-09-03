import unicodedata
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator
from uuid import UUID


Role = Literal["user", "admin", "super_admin"]


class UserAdminUpdate(BaseModel):
    role: Role | None = None
    is_active: bool | None = None
    disabled_reason: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_disabled_reason(self):
        if self.is_active is False and not (self.disabled_reason or "").strip():
            raise ValueError("禁用账号时必须填写原因")
        return self


class PasswordReset(BaseModel):
    new_password: str = Field(min_length=8, max_length=32)


EmploymentStatus = Literal["active", "inactive"]


def _clean_required(value: str) -> str:
    cleaned = " ".join(unicodedata.normalize("NFKC", value).split())
    if not cleaned:
        raise ValueError("字段不能为空")
    return cleaned


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(unicodedata.normalize("NFKC", value).split())
    return cleaned or None


class PersonnelProfileInput(BaseModel):
    full_name: Annotated[str, Field(min_length=1, max_length=80)]
    employee_no: Annotated[str, Field(min_length=1, max_length=40)]
    department: Annotated[str, Field(min_length=1, max_length=80)]
    job_title: Annotated[str, Field(min_length=1, max_length=80)]
    work_email: EmailStr | None = None
    work_phone: Annotated[str | None, Field(max_length=32)] = None
    employment_status: EmploymentStatus

    @field_validator("full_name", "employee_no", "department", "job_title")
    @classmethod
    def clean_required(cls, value: str) -> str:
        return _clean_required(value)

    @field_validator("work_phone")
    @classmethod
    def clean_phone(cls, value: str | None) -> str | None:
        return _clean_optional(value)

    @field_validator("work_email", mode="before")
    @classmethod
    def clean_email(cls, value: str | None) -> str | None:
        return _clean_optional(value)


class PersonnelProfilePublic(BaseModel):
    user_id: UUID
    full_name: str
    employee_no: str
    department: str
    job_title: str
    work_email: EmailStr | None = None
    work_phone: str | None = None
    employment_status: EmploymentStatus

    model_config = ConfigDict(from_attributes=True)


class PersonnelProfileDetail(BaseModel):
    profile: PersonnelProfilePublic | None = None
    can_query_personnel: bool


class PersonnelQueryPermissionInput(BaseModel):
    enabled: bool
