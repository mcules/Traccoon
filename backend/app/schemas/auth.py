import datetime as dt

from pydantic import BaseModel, Field, field_validator

from ..models.enums import GlobalRole, UserStatus


def _valid_email(v: str) -> str:
    v = v.strip().lower()
    if "@" not in v or v.startswith("@") or v.endswith("@"):
        raise ValueError("Ungültige E-Mail-Adresse")
    return v


class RegisterIn(BaseModel):
    email: str
    username: str = Field(min_length=2, max_length=100)
    password: str = Field(min_length=8)
    display_name: str = ""

    _norm_email = field_validator("email")(_valid_email)


class LoginIn(BaseModel):
    email: str
    password: str

    _norm_email = field_validator("email")(_valid_email)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class PasswordChangeIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8)


class UserOut(BaseModel):
    id: int
    email: str | None = None
    username: str
    display_name: str
    avatar_color: str
    theme: str
    global_role: GlobalRole
    status: UserStatus
    max_runners: int
    onboarded: bool
    default_project_view: str
    ticket_open_mode: str = "popup"
    ticket_layout: dict = {}
    pm_chat_style: str = "bubbles"
    claude_token_set: bool
    created_at: dt.datetime

    model_config = {"from_attributes": True}
