"""Passkeys: what the browser hands in, and what may come of it.

A passkey is a key pair the device makes. The private half stays there and never travels;
what is stored here is the public half. That is the whole trade: a database that leaks gives
away nothing anybody could log in with, and there is no secret to phish out of a person
either — the key only ever answers to the domain it was made for.

Which is also its limit, and it belongs written down: **the key hangs on the domain.** One
made on `traccoon.afu.tools` says nothing on an IP address or a LAN name. The password
therefore stays as a way in; taking it out would lock the house from a second door nobody
can open from the garden.

Two things are deliberately NOT in here:

* **No `user_verification=required`.** A hardware key without a PIN is a legitimate second
  factor beside the username; demanding it would turn away devices people already own.
  The check is `preferred`, so whoever can does it.
* **No attestation.** It says which make the authenticator is, and answering that question
  is a tracking surface. What is verified here is that the key signed the challenge — for a
  login that is the whole question.
"""
from __future__ import annotations

import datetime as dt
import logging
import secrets
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from ..config import settings
from ..core.redis import get_redis
from ..models.passkey import Passkey
from ..models.user import User

log = logging.getLogger("passkeys")

# How long an offer stands. A challenge is a one-shot, and a minute is longer than anybody
# needs to touch a key — leaving it valid for an hour only widens the window in which a
# stolen one could be replayed.
CHALLENGE_TTL = 120
_KEY = "traccoon:passkey:challenge:"


class NoPasskey(Exception):
    """This person has no key, or the one offered does not belong to them."""


def house() -> tuple[str, str]:
    """(rp_id, origin) — which domain a key is made for, and who may ask it something.

    Both out of `APP_BASE_URL`, because they must not drift apart: an `rp_id` that does not
    match the origin is a login that fails in the browser with a message nobody can act on.
    """
    base = (settings.app_base_url or "").rstrip("/")
    if not base:
        raise RuntimeError("APP_BASE_URL is not set — a passkey needs a domain")
    parts = urlparse(base)
    if not parts.hostname:
        raise RuntimeError(f"APP_BASE_URL is no URL: {base!r}")
    return parts.hostname, f"{parts.scheme}://{parts.netloc}"


def configured() -> bool:
    """Whether passkeys can work here at all. The login page asks before offering them."""
    try:
        house()
    except RuntimeError:
        return False
    return True


async def _remember(kind: str, handle: str, challenge: bytes) -> None:
    await get_redis().set(f"{_KEY}{kind}:{handle}", challenge.hex(), ex=CHALLENGE_TTL)


async def _redeem(kind: str, handle: str) -> bytes | None:
    """The challenge, and gone with the reading.

    One shot, and that is the point: a challenge that survives its use is a signature that
    can be handed in a second time. `getdel` does both in one step, so two logins racing
    each other cannot both get it.
    """
    raw = await get_redis().getdel(f"{_KEY}{kind}:{handle}")
    return bytes.fromhex(raw) if raw else None


async def keys_of(db: AsyncSession, user_id: int) -> list[Passkey]:
    return list((await db.execute(select(Passkey)
                                  .where(Passkey.user_id == user_id)
                                  .order_by(Passkey.id))).scalars().all())


# ── Registering a key ───────────────────────────────────────────────────────

async def offer_registration(db: AsyncSession, user: User) -> str:
    """What the browser needs to make a key. JSON, as `navigator.credentials.create` wants it."""
    rp_id, _ = house()
    have = await keys_of(db, user.id)
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name="Traccoon",
        user_id=str(user.id).encode(),
        user_name=user.username or (user.email or str(user.id)),
        user_display_name=user.display_name or user.username,
        # The keys already here are named so the authenticator does not make a second one for
        # the same account on the same device — that would be two lines in the list that
        # nobody can tell apart.
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(k.credential_id))
                             for k in have],
        authenticator_selection=AuthenticatorSelectionCriteria(
            # The key may live on the device; it does not have to. Demanding it would turn
            # away the hardware keys that only hold a handful of slots.
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    await _remember("reg", str(user.id), options.challenge)
    return options_to_json(options)


async def take_registration(db: AsyncSession, user: User, credential: dict,
                            label: str = "", kind: str = "") -> Passkey:
    rp_id, origin = house()
    challenge = await _redeem("reg", str(user.id))
    if challenge is None:
        raise NoPasskey("the offer has expired — please try again")
    checked = verify_registration_response(
        credential=credential, expected_challenge=challenge,
        expected_rp_id=rp_id, expected_origin=origin)
    from webauthn.helpers import bytes_to_base64url

    key = Passkey(
        user_id=user.id,
        credential_id=bytes_to_base64url(checked.credential_id),
        public_key=checked.credential_public_key,
        sign_count=checked.sign_count or 0,
        label=(label or "").strip()[:80],
        kind=(kind or "")[:20],
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)
    return key


# ── Logging in with one ─────────────────────────────────────────────────────

async def offer_login(db: AsyncSession, user: User) -> str:
    """What the browser needs to sign. Named keys only: this flow starts with a username, so
    the person is known and their keys are the ones to offer."""
    rp_id, _ = house()
    have = await keys_of(db, user.id)
    if not have:
        raise NoPasskey("this account has no passkey")
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(k.credential_id))
                           for k in have],
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    await _remember("login", str(user.id), options.challenge)
    return options_to_json(options)


async def take_login(db: AsyncSession, user: User, credential: dict) -> Passkey:
    """Check a signature. Raises when anything is off — the caller answers one 401 to all of it."""
    rp_id, origin = house()
    challenge = await _redeem("login", str(user.id))
    if challenge is None:
        raise NoPasskey("the offer has expired — please try again")
    given = str(credential.get("id") or "")
    key = next((k for k in await keys_of(db, user.id) if k.credential_id == given), None)
    if key is None:
        # A signature from a key that belongs to somebody else, or to nobody.
        raise NoPasskey("unknown passkey")
    checked = verify_authentication_response(
        credential=credential, expected_challenge=challenge,
        expected_rp_id=rp_id, expected_origin=origin,
        credential_public_key=key.public_key,
        credential_current_sign_count=key.sign_count)
    # The counter of an authenticator only ever grows. One that stands still or goes back is
    # the sign of a copied key — the library checks it, and it is why the new value is
    # written back here rather than being ignored. Some keys report 0 for good and are
    # exempt from the rule on both sides.
    key.sign_count = checked.new_sign_count
    key.last_used_at = dt.datetime.now(tz=dt.timezone.utc)
    await db.commit()
    return key


def decoy() -> str:
    """An offer for an account that has no key — or does not exist.

    Without it the login form answers differently for a known and an unknown name, and that
    is a list of accounts for whoever asks patiently. The challenge is real and belongs to
    nobody, so the browser finds nothing to sign and says so; the server never learns of it.
    """
    rp_id, _ = house()
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[PublicKeyCredentialDescriptor(id=secrets.token_bytes(32))],
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    return options_to_json(options)
