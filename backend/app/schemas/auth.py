import datetime as dt

from pydantic import BaseModel, Field, field_validator

from ..models.enums import GlobalRole, UserStatus


def _valid_email(v: str) -> str:
    v = v.strip().lower()
    if "@" not in v or v.startswith("@") or v.endswith("@"):
        raise ValueError("Invalid email address")
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
    # Zeitzone (IANA): die Oberfläche rechnet ihre Uhrzeiten damit, nicht mit der des Browsers.
    timezone: str = "Europe/Berlin"
    mail_last_account_id: int | None = None
    ticket_open_mode: str = "popup"
    ticket_layout: dict = {}
    pm_chat_style: str = "bubbles"
    # Benachrichtigungswege dieser Person (Profil).
    locale: str = "de"
    notify_default: str = "telegram"
    notify_email: str | None = None
    telegram_chat_id: str | None = None
    notify_destination_id: int | None = None
    # Personal process set (applies to all projects in which the user is an owner).
    workflow_set_id: int | None = None
    claude_token_set: bool
    created_at: dt.datetime

    model_config = {"from_attributes": True}
