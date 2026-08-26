from typing import Literal

from pydantic import BaseModel, Field, model_validator


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
