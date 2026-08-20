from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class AuthUserResponse(BaseModel):
    username: str
    must_change_password: bool


class AuthBootstrapResponse(BaseModel):
    """Public login hint: whether the initial password is still in force."""

    default_credentials: bool
