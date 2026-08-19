"""Schemas für Ziele. Geheimnisse gehen nur hinein, nie hinaus."""
import datetime as dt

from pydantic import BaseModel, Field


class _DestinationBase(BaseModel):
    label: str = Field(default="", max_length=200)
    description: str = ""
    base_url: str = Field(min_length=1, max_length=1000)
    auth_type: str = "none"          # none|basic|bearer|api_key|hmac|oauth2_cc
    username: str = Field(default="", max_length=200)
    api_key_name: str = Field(default="X-API-Key", max_length=120)
    api_key_in: str = "header"       # header|query
    hmac_header: str = Field(default="X-Webhook-Signature", max_length=120)
    hmac_algo: str = Field(default="sha256", max_length=20)
    hmac_prefix: str = Field(default="", max_length=20)
    oauth_token_url: str = Field(default="", max_length=1000)
    oauth_client_id: str = Field(default="", max_length=300)
    oauth_scope: str = Field(default="", max_length=500)
    oauth_audience: str = Field(default="", max_length=500)
    default_headers: dict = {}
    timeout_sec: int = Field(default=30, ge=1, le=600)
    verify_tls: bool = True
    enabled: bool = True
    allow_agents: bool = False
    # How much of the answer the caller sees at most. The default protects the context;
    # raise it only for counterparts that deliberately deliver their state in one call.
    max_response_chars: int = Field(default=4000, ge=500, le=60000)


class DestinationCreate(_DestinationBase):
    name: str = Field(min_length=1, max_length=80)
    # Geltungsbereich: beides leer = systemweit
    user_id: int | None = None
    project_id: int | None = None
    # Fill exactly one of these, depending on the method; it lands encrypted in the same field.
    password: str | None = None       # basic
    token: str | None = None          # bearer
    api_key: str | None = None        # api_key
    hmac_secret: str | None = None    # hmac
    client_secret: str | None = None  # oauth2_cc
    secret: str | None = None         # allgemein


class DestinationUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=200)
    description: str | None = None
    base_url: str | None = Field(default=None, max_length=1000)
    auth_type: str | None = None
    username: str | None = Field(default=None, max_length=200)
    api_key_name: str | None = Field(default=None, max_length=120)
    api_key_in: str | None = None
    hmac_header: str | None = Field(default=None, max_length=120)
    hmac_algo: str | None = Field(default=None, max_length=20)
    hmac_prefix: str | None = Field(default=None, max_length=20)
    oauth_token_url: str | None = Field(default=None, max_length=1000)
    oauth_client_id: str | None = Field(default=None, max_length=300)
    oauth_scope: str | None = Field(default=None, max_length=500)
    oauth_audience: str | None = Field(default=None, max_length=500)
    default_headers: dict | None = None
    timeout_sec: int | None = Field(default=None, ge=1, le=600)
    verify_tls: bool | None = None
    enabled: bool | None = None
    allow_agents: bool | None = None
    max_response_chars: int | None = Field(default=None, ge=500, le=60000)
    # Leer/weggelassen = bestehendes Geheimnis bleibt unangetastet.
    password: str | None = None
    token: str | None = None
    api_key: str | None = None
    hmac_secret: str | None = None
    client_secret: str | None = None
    secret: str | None = None


class DestinationOut(_DestinationBase):
    id: int
    name: str
    user_id: int | None
    project_id: int | None
    scope: str                 # global | user | project
    has_secret: bool           # only WHETHER one is stored
    last_used_at: dt.datetime | None = None
    created_at: dt.datetime


class DestinationTestIn(BaseModel):
    """Test call. The default is deliberately GET: a test should change nothing."""
    method: str = "GET"
    path: str = ""
    query: dict | None = None
    headers: dict | None = None
    body: object | None = None
    timeout_sec: int | None = Field(default=None, ge=1, le=120)
