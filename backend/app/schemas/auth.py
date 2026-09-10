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
    # Email address or username. The field keeps its name because every client sends it that
    # way, and renaming it would be a broken login for one deploy.
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _trimmed(cls, v: str) -> str:
        """No `@` check here, unlike at registration.

        A username is a login too, and demanding an email in the schema made it a 422 before
        the password was ever looked at — with the field validation message of a form the
        person had filled in correctly.
        """
        v = v.strip()
        if not v:
            raise ValueError("Email address or username is missing")
        return v


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
    # Timezone (IANA): the UI computes its times with it, not with that of the browser.
    timezone: str = "Europe/Berlin"
    mail_last_account_id: int | None = None
    mail_threads: bool = False
    passkey_declined: bool = False
    ticket_open_mode: str = "popup"
    ticket_layout: dict = {}
    list_sort: dict = {}
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
