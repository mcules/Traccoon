"""`POST /auth/refresh` — verlängern darf er, verleihen nicht.

Der Endpunkt ist die Voraussetzung dafür, dass ein Tab länger offen bleiben kann als
`jwt_expire_minutes` (720) — der Kiosk-Wandschirm des Büros ist der erste, der das braucht.
Genau deshalb ist er auch eine Sicherheitsfläche, und diese Datei nagelt sie fest:

* was **nicht** mehr gilt, wird nicht wiederbelebt (abgelaufen, Passwort gewechselt, Konto
  deaktiviert),
* und was gilt, wird **unverändert** verlängert: dasselbe Subjekt, dieselbe Rolle, keine
  zusätzlichen Ansprüche im Token.

Der letzte Punkt ist der eigentliche Grund für die Datei. Ein Refresh, der beim Neuausstellen
etwas dazulegt, ist eine Rechteausweitung mit Zeitschalter.
"""
import datetime as dt

import jwt
import pytest

from app.config import settings
from app.core.security import create_access_token
from app.models.enums import GlobalRole, UserStatus
from conftest import make_user

pytestmark = pytest.mark.asyncio


def token_mit(user_id: int, *, iat: dt.datetime, exp: dt.datetime) -> str:
    """Ein Token mit frei gewählten Zeitstempeln — dieselbe Form wie `create_access_token`."""
    return jwt.encode(
        {"sub": str(user_id), "iat": int(iat.timestamp()), "exp": int(exp.timestamp())},
        settings.jwt_secret, algorithm=settings.jwt_algorithm,
    )


def kopf(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_gueltiges_token_wird_verlaengert(client, db):
    """Der Normalfall: neues Token, und das neue Token trägt."""
    user = await make_user(db, "wandschirm")
    alt = create_access_token(user.id)

    r = await client.post("/auth/refresh", headers=kopf(alt))
    assert r.status_code == 200, r.text
    neu = r.json()["access_token"]
    assert r.json()["token_type"] == "bearer"

    # Der Beweis, dass es ein echtes Token ist, ist nicht seine Form, sondern ein Aufruf damit.
    me = await client.get("/auth/me", headers=kopf(neu))
    assert me.status_code == 200
    assert me.json()["id"] == user.id


async def test_abgelaufenes_token_wird_abgelehnt(client, db):
    """Ein Refresh verlängert eine lebende Sitzung — er weckt keine tote."""
    user = await make_user(db, "spaet")
    jetzt = dt.datetime.now(tz=dt.timezone.utc)
    tot = token_mit(user.id, iat=jetzt - dt.timedelta(days=2), exp=jetzt - dt.timedelta(hours=1))

    r = await client.post("/auth/refresh", headers=kopf(tot))
    assert r.status_code == 401


async def test_token_von_vor_dem_passwortwechsel(client, db):
    """Sitzungs-Invalidierung: wer sein Passwort geändert hat, hat alle alten Tokens beendet.

    Ein Refresh, der das übergeht, wäre ein Hintertürchen genau für den Fall, für den die
    Prüfung gebaut wurde — ein fremder Tab, der weiterläuft.
    """
    user = await make_user(db, "passwortwechsler")
    jetzt = dt.datetime.now(tz=dt.timezone.utc)
    # Zwei Tage Abstand: unter SQLite kommt `password_changed_at` naiv zurück und wird in
    # `deps.py` als Ortszeit gelesen. Der Test soll an der Prüfung hängen, nicht an der
    # Zeitzone des Containers.
    alt = token_mit(user.id, iat=jetzt - dt.timedelta(days=2), exp=jetzt + dt.timedelta(hours=1))
    user.password_changed_at = jetzt
    await db.commit()

    r = await client.post("/auth/refresh", headers=kopf(alt))
    assert r.status_code == 401


async def test_deaktiviertes_konto(client, db):
    """Deaktiviert heißt deaktiviert — auch für ein Token, das noch zwölf Stunden gültig wäre.

    403 ist hier die Antwort des Hauses (`deps.get_current_user`: „Account not active");
    der Test lässt 401 gelten, damit er nicht an einer Verschärfung zerbricht.
    """
    user = await make_user(db, "gesperrt")
    token = create_access_token(user.id)
    user.status = UserStatus.disabled
    await db.commit()

    r = await client.post("/auth/refresh", headers=kopf(token))
    assert r.status_code in (401, 403)


async def test_ohne_token(client):
    r = await client.post("/auth/refresh")
    assert r.status_code == 401


async def test_kein_rechtezuwachs(client, db):
    """Das neue Token ist dasselbe Token, nur später — insbesondere dieselbe Rolle.

    Geprüft auf beiden Ebenen: die Ansprüche im Token (es gibt genau drei, und keiner davon
    ist eine Rolle) und die tatsächliche Wirkung (`/auth/me` sagt weiter `user`).
    """
    user = await make_user(db, "einfach")          # global_role = user, kein Admin
    assert user.global_role == GlobalRole.user

    r = await client.post("/auth/refresh", headers=kopf(create_access_token(user.id)))
    assert r.status_code == 200
    neu = r.json()["access_token"]

    payload = jwt.decode(neu, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    assert payload["sub"] == str(user.id)
    # Keine zusätzlichen Ansprüche: Rollen kommen bei jedem Aufruf aus der Datenbank, nie
    # aus dem Token. Wächst diese Menge, wächst die Angriffsfläche.
    assert set(payload) == {"sub", "iat", "exp"}

    me = await client.get("/auth/me", headers=kopf(neu))
    assert me.status_code == 200
    assert me.json()["global_role"] == GlobalRole.user.value

    # Und der Admin-Bereich bleibt zu — der eigentliche Punkt hinter der Anspruchsmenge.
    admin = await client.get("/admin/run-retention", headers=kopf(neu))
    assert admin.status_code == 403
