"""Passkeys used from an Android app.

An app is not a browser: it reports an origin made of its signing certificate rather than a
URL, and before Android lets it near the site's keys it reads `/.well-known/assetlinks.json`
from the site. Both come out of ONE setting, and this file pins the two derivations plus
the whole round trip from the app's origin, against a small software authenticator (an EC
key that answers like a phone does, minus the hardware).
"""
import base64
import hashlib
import json
import struct

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from app.config import settings
from app.services import passkeys
from conftest import auth, make_user

pytestmark = pytest.mark.asyncio

FINGERPRINT = "F7:56:1B:B0:46:60:26:EC:B9:84:DB:99:AB:24:3B:AA:19:3E:E4:CB:91:C3:8C:7C:D9:DD:49:FF:FA:8A:D1:AB"
APP_ORIGIN = "android:apk-key-hash:" + base64.urlsafe_b64encode(
    bytes.fromhex(FINGERPRINT.replace(":", ""))).decode().rstrip("=")


@pytest.fixture(autouse=True)
def one_app(monkeypatch):
    monkeypatch.setattr(settings, "app_base_url", "https://traccoon.test")
    monkeypatch.setattr(settings, "android_apps", f"de.example.app:{FINGERPRINT.lower()}")


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class SoftKey:
    """One passkey as the phone holds it: a P-256 key with an id of its own."""

    def __init__(self, origin: str, rp_id: str = "traccoon.test"):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = hashlib.sha256(
            self.key.private_numbers().private_value.to_bytes(32, "big")).digest()[:16]
        self.origin, self.rp_id, self.count = origin, rp_id, 0

    def _client_data(self, kind: str, challenge: str) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": self.origin}).encode()

    def _auth_data(self, flags: int, attested: bytes = b"") -> bytes:
        self.count += 1
        return (hashlib.sha256(self.rp_id.encode()).digest() + bytes([flags])
                + struct.pack(">I", self.count) + attested)

    def register(self, options: dict) -> dict:
        nums = self.key.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, "big"),
                            -3: nums.y.to_bytes(32, "big")})
        attested = bytes(16) + struct.pack(">H", len(self.credential_id)) + self.credential_id + cose
        auth_data = self._auth_data(0x45, attested)   # present, verified, key attached
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {"id": b64url(self.credential_id), "rawId": b64url(self.credential_id),
                "type": "public-key", "clientExtensionResults": {},
                "response": {"clientDataJSON": b64url(self._client_data("webauthn.create", options["challenge"])),
                             "attestationObject": b64url(attestation), "transports": ["internal"]}}

    def sign(self, options: dict, user_id: int) -> dict:
        client_data = self._client_data("webauthn.get", options["challenge"])
        auth_data = self._auth_data(0x05)
        signature = self.key.sign(auth_data + hashlib.sha256(client_data).digest(),
                                  ec.ECDSA(hashes.SHA256()))
        return {"id": b64url(self.credential_id), "rawId": b64url(self.credential_id),
                "type": "public-key", "clientExtensionResults": {},
                "response": {"clientDataJSON": b64url(client_data), "authenticatorData": b64url(auth_data),
                             "signature": b64url(signature), "userHandle": b64url(str(user_id).encode())}}


def test_one_setting_yields_origin_and_asset_links():
    assert passkeys.origins() == ["https://traccoon.test", APP_ORIGIN]
    [entry] = passkeys.asset_links()
    assert entry["target"]["package_name"] == "de.example.app"
    # Upper case, whatever was typed: that is the form Android compares against.
    assert entry["target"]["sha256_cert_fingerprints"] == [FINGERPRINT]


async def test_the_asset_links_are_served(client):
    r = await client.get("/auth/passkey/assetlinks.json")
    assert r.status_code == 200
    assert r.json()[0]["target"]["namespace"] == "android_app"


async def test_a_key_made_in_the_app_opens_the_account(client, db, redis_stub, passkey_redis):
    user = await make_user(db, "handy")
    key = SoftKey(APP_ORIGIN)

    r = await client.post("/me/passkeys/options", headers=auth(user))
    assert r.status_code == 200, r.text
    r = await client.post("/me/passkeys", headers=auth(user),
                          json={"credential": key.register(r.json()), "label": "phone", "kind": "platform"})
    assert r.status_code == 201, r.text

    r = await client.post("/auth/passkey/options", json={"email": "handy"})
    assert r.status_code == 200, r.text
    r = await client.post("/auth/passkey/login",
                          json={"email": "handy", "credential": key.sign(r.json(), user.id)})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["id"] == user.id


async def test_an_app_that_is_not_named_stays_out(client, db, redis_stub, passkey_redis):
    user = await make_user(db, "fremd")
    key = SoftKey("android:apk-key-hash:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    r = await client.post("/me/passkeys/options", headers=auth(user))
    r = await client.post("/me/passkeys", headers=auth(user),
                          json={"credential": key.register(r.json()), "label": "x"})
    assert r.status_code == 400
