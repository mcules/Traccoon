"""Minimaler MCP-Client (Streamable HTTP) für Aufrufe aus dem Backend heraus.

Bisher sprach nur der *Agent* mit MCP-Servern — über die CLI, die ihre eigene Verbindung
aufbaut. Für die Spam-Erkennung reicht das nicht: eine bestätigte Mail zu verschieben ist
eine mechanische Handbewegung, kein Auftrag. Dafür einen Claude-Lauf zu starten wäre teuer,
langsam und unzuverlässig — der Bot braucht einen direkten Draht zu `imap-mcp`.

Deshalb hier das Nötigste vom Protokoll: `initialize`, Sitzungskennung merken, `tools/call`.
Kein Werkzeug-Verzeichnis, keine Ressourcen, keine Wiederaufnahme — was fehlt, fehlt

Die Server im `mcp-backends`-Netz sind intern unauthentifiziert (die Rechteprüfung sitzt in
MCPJungle davor). Dieser Weg umgeht MCPJungle bewusst: er trägt keine Nutzer-Identität,
sondern führt eine Entscheidung aus, die der Mensch gerade selbst getroffen hat.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

log = logging.getLogger("traccoon.mcp")

_PROTOCOL_VERSION = "2025-06-18"
_TIMEOUT = 30.0


class McpError(RuntimeError):
    """Der Server hat den Aufruf abgelehnt oder war nicht erreichbar."""


def _entpacken(resp: httpx.Response) -> dict[str, Any]:
    """Antwort → JSON-RPC-Objekt. Streamable HTTP darf mit `application/json` ODER als
    Ereignisstrom antworten; beides kommt vor, je nach Server und Aufruf."""
    ctype = resp.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        for zeile in resp.text.splitlines():
            if zeile.startswith("data:"):
                roh = zeile[5:].strip()
                if roh:
                    try:
                        return json.loads(roh)
                    except json.JSONDecodeError:
                        continue
        raise McpError("Ereignisstrom ohne verwertbare Antwort")
    try:
        return resp.json()
    except ValueError as exc:
        raise McpError(f"unlesbare Antwort: {resp.text[:200]}") from exc


async def call_tool(url: str, tool: str, arguments: dict[str, Any], *,
                    headers: dict[str, str] | None = None,
                    timeout: float = _TIMEOUT) -> dict[str, Any]:
    """Ein Werkzeug auf einem MCP-Server aufrufen. → Ergebnis-Inhalt des Servers.

    Wirft `McpError`, wenn der Server einen Fehler meldet — auch bei `isError` im Ergebnis,
    denn ein „konnte nicht verschieben" ist für den Aufrufer dasselbe wie ein Transportfehler:
    die Mail liegt noch da, wo sie lag.
    """
    basis = {"Content-Type": "application/json",
             "Accept": "application/json, text/event-stream", **(headers or {})}
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            init = await client.post(url, headers=basis, json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": _PROTOCOL_VERSION,
                           "capabilities": {},
                           "clientInfo": {"name": "traccoon", "version": "1"}}})
            init.raise_for_status()
        except httpx.HTTPError as exc:
            raise McpError(f"{url} nicht erreichbar: {exc}") from exc
        _entpacken(init)
        # Der Server vergibt die Sitzung erst hier; alle Folgeaufrufe müssen sie mitführen.
        sitzung = init.headers.get("mcp-session-id")
        if sitzung:
            basis["Mcp-Session-Id"] = sitzung

        # Ohne diese Benachrichtigung lehnen strenge Server jeden weiteren Aufruf ab.
        try:
            await client.post(url, headers=basis, json={
                "jsonrpc": "2.0", "method": "notifications/initialized"})
        except httpx.HTTPError as exc:      # nicht tödlich — manche Server brauchen sie nicht
            log.debug("notifications/initialized fehlgeschlagen: %s", exc)

        try:
            resp = await client.post(url, headers=basis, json={
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": tool, "arguments": arguments}})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise McpError(f"{tool} fehlgeschlagen: {exc}") from exc

    daten = _entpacken(resp)
    if "error" in daten:
        fehler = daten["error"]
        raise McpError(f"{tool}: {fehler.get('message') or fehler}")
    ergebnis = daten.get("result") or {}
    if ergebnis.get("isError"):
        raise McpError(f"{tool}: {_text(ergebnis)}")
    return ergebnis


def _text(ergebnis: dict[str, Any]) -> str:
    """Textteile eines MCP-Ergebnisses zusammenziehen (für Meldungen und Protokoll)."""
    teile = [c.get("text", "") for c in (ergebnis.get("content") or [])
             if isinstance(c, dict) and c.get("type") == "text"]
    return " ".join(t for t in teile if t).strip()


def ergebnis_text(ergebnis: dict[str, Any]) -> str:
    return _text(ergebnis)


def ergebnis_json(ergebnis: dict[str, Any]) -> dict | None:
    """Strukturiertes Ergebnis eines Werkzeugs (oder None).

    Werkzeuge liefern ihr Ergebnis doppelt: als `structuredContent` und als JSON-Text.
    Wer damit rechnen will statt es nur zu melden, nimmt diesen Weg — den Text noch einmal
    von Hand zu zerlegen wäre dieselbe Arbeit an einer zweiten Stelle.
    """
    inhalt = ergebnis.get("structuredContent")
    if isinstance(inhalt, dict):
        return inhalt
    text = _text(ergebnis)
    if not text:
        return None
    try:
        daten = json.loads(text)
    except (ValueError, TypeError):
        return None
    return daten if isinstance(daten, dict) else None
