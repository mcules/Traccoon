"""MCP-Werkzeuge in einem Ablauf — Traccoons Antwort auf „400 Integrationen".

Traccoon betreibt längst ein Dutzend MCP-Server (Mail, Vault, Paperless, Nextcloud, Immich,
Zeiterfassung, Hausautomation, …). Erreichbar waren sie bisher nur für Agenten: wer in einem
Ablauf eine Notiz schreiben oder ein Dokument ablegen wollte, musste dafür einen
Sprachmodell-Lauf starten — teuer und langsam für einen Handgriff, den ein Werkzeug direkt
kann.

Die Rechte laufen über den **Eigentümer des Laufs**: aufgerufen wird über seinen
MCPJungle-Gruppen-Endpoint, nicht über einen globalen Zugang. Wer selbst keinen Zugriff auf
einen Dienst hat, bekommt ihn auch nicht über einen selbstgebauten Ablauf.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger("workflow_tools")


async def _server_des_besitzers(db: AsyncSession, owner_id: int | None) -> list[dict]:
    """Die MCP-Server, die diesem Menschen gehören (plus die globalen).

    Genau dieselbe Quelle wie beim Agenten: die **Registry** (Einstellungen → MCP-Server).
    Damit ist das Anbinden eines fremden Systems Konfiguration und kein Programmieren —
    wer einen Server einträgt, hat seine Werkzeuge sofort im Ablauf zur Auswahl.

    Server mit Variablen-Schema brauchen eine ausgefüllte Instanz; die hängt heute am
    Agenten. Ohne Instanz bleiben sie außen vor, statt mit halben Kopfzeilen zu scheitern.
    """
    from sqlalchemy import or_, select

    from ..models.plugins import McpServer
    from ..worker.runtime import _server_spec

    q = select(McpServer).where(McpServer.enabled.is_(True))
    q = q.where(or_(McpServer.user_id.is_(None), McpServer.user_id == owner_id)
                if owner_id is not None else McpServer.user_id.is_(None))
    out = []
    for r in (await db.execute(q)).scalars().all():
        if r.variables:
            continue
        spec = _server_spec(r)
        if spec:
            out.append(spec)
    return out


async def _sitzung(db: AsyncSession, owner_id: int | None):
    """Kontextmanager für die MCP-Sitzung des Eigentümers (oder None, wenn er keine hat).

    Zwei Quellen, beide über den Menschen hinter dem Lauf: seine Registry-Server und —
    falls eingerichtet — sein MCPJungle-Gruppen-Endpoint.
    """
    from ..worker.mcp_client import mcp_session
    from ..worker.runtime import _owner_gateway

    url, token = await _owner_gateway(db, owner_id)
    server = await _server_des_besitzers(db, owner_id)
    if not url and not server:
        return None
    return mcp_session(gateway_url=url, gateway_token=token, servers=server)


async def werkzeuge(db: AsyncSession, owner_id: int | None) -> list[dict]:
    """Welche Werkzeuge dieser Mensch hat — Name, Beschreibung, Pflichtfelder.

    Speist die Auswahl im Editor. Fällt der Gateway aus, ist die Liste leer statt kaputt:
    ein Ablauf lässt sich auch ohne Werkzeugliste weiterbauen (Name von Hand eintragen).
    """
    sitzung = await _sitzung(db, owner_id)
    if sitzung is None:
        return []
    try:
        async with sitzung as mcp:
            roh = await mcp.list_tools()
    except Exception:  # noqa: BLE001 — Werkzeugliste ist Komfort, kein Betriebsmittel
        log.warning("MCP-Werkzeugliste für Nutzer %s nicht abrufbar", owner_id, exc_info=True)
        return []
    out = []
    for t in roh:
        schema = t.schema if isinstance(t.schema, dict) else {}
        felder = list((schema.get("properties") or {}).keys())
        out.append({
            "name": t.name,
            "server": t.name.split("__", 1)[0] if "__" in t.name else "",
            "beschreibung": (t.description or "").strip().split("\n")[0][:300],
            "felder": felder[:20],
            "pflicht": list(schema.get("required") or [])[:20],
        })
    return sorted(out, key=lambda w: w["name"])


async def aufrufen(db: AsyncSession, owner_id: int | None, name: str,
                   arguments: dict) -> dict:
    """Ein Werkzeug aufrufen. → {ok, text, json?, error?}.

    Fehler werden zurückgegeben, nicht geworfen: der Ablauf soll selbst entscheiden können,
    ob ein misslungener Aufruf ihn beendet (`fail_on_error`) oder ob er weiterläuft.
    """
    if not name:
        return {"ok": False, "text": "", "error": "kein Werkzeug angegeben"}
    sitzung = await _sitzung(db, owner_id)
    if sitzung is None:
        return {"ok": False, "text": "",
                "error": "kein MCP-Zugang für den Eigentümer dieses Ablaufs"}
    # Unbekannter Server im Namen (`server__werkzeug`) und kein Gateway, das ihn auffangen
    # könnte: dann antwortet die Sitzung mit einem Hinweis-TEXT statt mit einem Fehler, und
    # der Ablauf liefe weiter, als wäre alles gut. Lieber vorher nachsehen.
    if "__" in name:
        server = name.split("__", 1)[0]
        from ..worker.runtime import _owner_gateway
        url, _ = await _owner_gateway(db, owner_id)
        if not url and server not in {
                s["name"] for s in await _server_des_besitzers(db, owner_id)}:
            return {"ok": False, "text": "",
                    "error": f"unbekannter MCP-Server {server!r} — in den Einstellungen "
                             f"eintragen oder Namen prüfen"}
    try:
        async with sitzung as mcp:
            text = await mcp.call(name, arguments or {})
    except Exception as exc:  # noqa: BLE001
        log.warning("Werkzeug %s fehlgeschlagen: %s", name, exc)
        return {"ok": False, "text": "", "error": str(exc)[:500]}

    text = text if isinstance(text, str) else str(text)
    ergebnis: dict = {"ok": True, "text": text[:20000]}
    # Wer mit dem Ergebnis weiterrechnen will, braucht es zerlegt — die meisten Werkzeuge
    # antworten ohnehin in JSON.
    try:
        daten = json.loads(text)
    except (ValueError, TypeError):
        daten = None
    if isinstance(daten, (dict, list)):
        ergebnis["json"] = daten
    # Werkzeuge melden ihren eigenen Fehlschlag oft im Text — das ist kein Transportfehler,
    # aber der Ablauf soll darauf verzweigen können.
    if isinstance(daten, dict) and daten.get("error"):
        ergebnis["ok"] = False
        ergebnis["error"] = str(daten["error"])[:500]
    return ergebnis
