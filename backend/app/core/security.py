"""Passwort-Hashing (Argon2id), JWT (HS256) und reversible Secrets (Fernet)."""
from __future__ import annotations

import datetime as dt

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
import logging

from cryptography.fernet import Fernet, InvalidToken

from ..config import settings

_ph = PasswordHasher()
ENC_PREFIX = "enc:v1:"


# ---------- Passwörter ----------

def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except (VerifyMismatchError, Exception):
        return False


# ---------- JWT ----------

def create_access_token(user_id: int) -> str:
    now = dt.datetime.now(tz=dt.timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + dt.timedelta(minutes=settings.jwt_expire_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


log = logging.getLogger("traccoon.security")


# ---------- Fernet-Secrets ----------

def _fernet() -> Fernet | None:
    key = settings.secret_encryption_key.strip()
    if not key:
        return None
    return Fernet(key.encode())


def encrypt_secret(plaintext: str) -> str:
    """Verschlüsselt einen Wert. Ohne SECRET_ENCRYPTION_KEY landet er im Klartext
    in der Datenbank — das ist nur als Notnagel gedacht und wird laut protokolliert."""
    if plaintext == "":
        return ""
    f = _fernet()
    if f is None:
        log.error("SECRET_ENCRYPTION_KEY fehlt — Geheimnis wird IM KLARTEXT gespeichert. "
                  "Key setzen (Fernet.generate_key()) und Dienste neu starten.")
        return plaintext
    return ENC_PREFIX + f.encrypt(plaintext.encode()).decode()


def decrypt_secret(stored: str) -> str:
    if not stored:
        return ""
    if not stored.startswith(ENC_PREFIX):
        return stored  # Alt-Klartext
    f = _fernet()
    if f is None:
        return ""
    try:
        return f.decrypt(stored[len(ENC_PREFIX):].encode()).decode()
    except InvalidToken:
        return ""
