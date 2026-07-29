"""Der Curator: hält das gelernte Gedächtnis lesbar, statt es wuchern zu lassen.

Das Gedächtnis (TRA-30) ist eine Bullet-Liste je Notiz, an die jeder Lauf unten anhängt.
Es wächst damit nur — und `read_memory` kappt bei `MAX_MEMORY_CHARS`. Ab dieser Grenze
fällt das Unterste stillschweigend aus dem Prompt: erst gelernt, dann vergessen, ohne dass
es jemand merkt. Genau davor hat Hermes seinen `curator` — Aufräumen als eigene, kleine
Hintergrundarbeit auf dem Aux-Modell.

Die Regeln sind bewusst streng, weil hier fremde Erinnerungen angefasst werden:

* **Nie löschen, nur archivieren.** Was rausfliegt, landet in `Archiv-<notiz>.md`. Ein
  Fehlurteil des Modells kostet dann einen Handgriff, nicht eine Erinnerung.
* **Angepinntes ist tabu.** Eine Zeile mit 📌 bleibt Wort für Wort stehen.
* **Im Zweifel behalten.** Der Auftrag sagt ausdrücklich, dass Unklares nicht wegsortiert wird.
* **Nur zusammenfassen, nicht erfinden.** Keine neuen Aussagen, keine Umdeutungen.
* **Nichts tun ist ein gültiges Ergebnis.** Kein Aux-Modell, kurze Notiz, unklare Antwort →
  die Notiz bleibt, wie sie ist.
"""
from __future__ import annotations

import datetime as dt
import logging

from ..services.appsettings import get_setting, set_setting
from .tools_memory import _note_target, _read_note, memory_root, note_path

log = logging.getLogger("traccoon.curator")

# Erst ab dieser Länge lohnt Aufräumen — darunter ist die Liste ohnehin überschaubar.
MINDEST_ZEICHEN = 1500
# Wie oft je Notiz höchstens aufgeräumt wird.
ABSTAND_STUNDEN = 24
PIN = "📌"

AUFTRAG = (
    "Du räumst die Gedächtnis-Notiz eines Assistenten auf. Sie ist eine Liste gelernter "
    "Vorgaben seines Menschen.\n\n"
    "REGELN:\n"
    "1. Führe Dubletten und offensichtlich Gleichbedeutendes zu EINER Zeile zusammen.\n"
    "2. Widersprechen sich zwei Zeilen, behalte die JÜNGERE (weiter unten) und wirf die "
    "ältere raus.\n"
    "3. Wirf raus, was erkennbar erledigt oder überholt ist.\n"
    "4. Alles andere bleibt — im Zweifel BEHALTEN. Erfinde nichts, deute nichts um, "
    "formuliere Inhalte nicht sinnverändernd neu.\n"
    f"5. Zeilen mit {PIN} bleiben Wort für Wort unverändert und in ihrer Reihenfolge.\n\n"
    "Antworte in genau zwei Abschnitten, ohne Vorrede:\n"
    "### BEHALTEN\n"
    "<die aufgeräumte Liste, eine Bullet-Zeile je Erkenntnis>\n"
    "### ARCHIV\n"
    "<die entfernten Zeilen, unverändert, je eine Bullet-Zeile — oder das Wort: keine>\n\n"
    "--- Notiz ---\n"
)


def _teile(antwort: str) -> tuple[str, str] | None:
    """(behalten, archiv) aus der Modellantwort — None, wenn sie nicht dem Format folgt."""
    if "### BEHALTEN" not in antwort:
        return None
    rest = antwort.split("### BEHALTEN", 1)[1]
    if "### ARCHIV" in rest:
        behalten, archiv = rest.split("### ARCHIV", 1)
    else:
        behalten, archiv = rest, ""
    behalten = behalten.strip()
    archiv = archiv.strip()
    if archiv.lower() in ("keine", "keine.", "-", ""):
        archiv = ""
    return (behalten, archiv) if behalten else None


def _zeilen(text: str) -> list[str]:
    return [z.strip() for z in text.splitlines() if z.strip().startswith(("-", "*"))]


async def _zuletzt_key(owner_id: int, pfad: str) -> str:
    return f"curator_last:{owner_id}:{pfad}"


async def faellig(db, owner_id: int, pfad: str, *, jetzt: dt.datetime | None = None) -> bool:
    jetzt = jetzt or dt.datetime.now(tz=dt.timezone.utc)
    roh = await get_setting(db, await _zuletzt_key(owner_id, pfad), "")
    if not roh:
        return True
    try:
        return (jetzt - dt.datetime.fromisoformat(roh)).total_seconds() >= ABSTAND_STUNDEN * 3600
    except ValueError:
        return True


async def kuratiere_notiz(db, mcp, *, owner_id: int, pfad: str, agent, tokens: dict,
                          base_urls: dict) -> str | None:
    """Eine Gedächtnis-Notiz aufräumen. Rückgabe = kurzer Bericht, None = nichts getan."""
    inhalt = (await _read_note(mcp, pfad)).strip()
    if len(inhalt) < MINDEST_ZEICHEN:
        return None

    angepinnt = [z for z in _zeilen(inhalt) if PIN in z]

    from .aux import aux_chat
    antwort = await aux_chat(db, owner_id=owner_id, task="curator",
                             messages=[{"role": "user", "content": AUFTRAG + inhalt}],
                             agent=agent, tokens=tokens, base_urls=base_urls, max_tokens=3000)
    if not antwort:
        return None
    geteilt = _teile(antwort)
    if geteilt is None:
        log.warning("Curator: Antwort folgt nicht dem Format — %s bleibt unverändert", pfad)
        return None
    behalten, archiv = geteilt

    # Sicherungsnetze gegen ein übereifriges Modell. Sie greifen VOR dem Schreiben, weil ein
    # überschriebenes Gedächtnis nur noch aus dem Archiv zu retten wäre.
    if not _zeilen(behalten):
        log.warning("Curator: Ergebnis hat keine Einträge — %s bleibt unverändert", pfad)
        return None
    fehlend = [z for z in angepinnt if z not in behalten]
    if fehlend:
        log.warning("Curator: %d angepinnte Zeile(n) fehlten im Ergebnis — %s bleibt unverändert",
                    len(fehlend), pfad)
        return None
    if len(_zeilen(behalten)) < len(_zeilen(inhalt)) / 3:
        log.warning("Curator: Ergebnis wirft mehr als zwei Drittel weg — %s bleibt unverändert", pfad)
        return None

    kopf = f"# {pfad.rsplit('/', 1)[-1].removesuffix('.md')}\n\n"
    if archiv:
        # ERST archivieren, DANN kürzen: bricht der zweite Schritt ab, ist nichts verloren.
        arch_pfad = pfad.rsplit("/", 1)[0] + "/Archiv-" + pfad.rsplit("/", 1)[-1]
        stempel = dt.datetime.now(tz=dt.timezone.utc).strftime("%Y-%m-%d")
        try:
            await mcp.call("obsidian__obsidian_append_to_note",
                           {"target": _note_target(arch_pfad),
                            "content": f"\n## Aussortiert am {stempel}\n{archiv}\n"})
        except Exception as exc:  # noqa: BLE001
            log.warning("Curator: Archiv %s nicht schreibbar (%s) — %s bleibt unverändert",
                        arch_pfad, exc, pfad)
            return None

    try:
        await mcp.call("obsidian__obsidian_write_note",
                       {"target": _note_target(pfad), "content": kopf + behalten + "\n",
                        "overwrite": True})
    except Exception as exc:  # noqa: BLE001
        log.warning("Curator: %s nicht schreibbar (%s)", pfad, exc)
        return None

    await set_setting(db, await _zuletzt_key(owner_id, pfad),
                      dt.datetime.now(tz=dt.timezone.utc).isoformat())
    vorher, nachher = len(_zeilen(inhalt)), len(_zeilen(behalten))
    return f"{pfad}: {vorher} → {nachher} Einträge, {len(_zeilen(archiv))} archiviert"


async def kuratiere(db, mcp, *, owner_id: int, agent_role: str = "", project_key: str = "",
                    agent=None, tokens: dict | None = None, base_urls: dict | None = None) -> list[str]:
    """Alle einschlägigen Gedächtnis-Notizen eines Menschen aufräumen, sofern fällig."""
    root = await memory_root(db, owner_id)
    if not root:
        return []
    berichte = []
    for bereich in ("mensch", "agent", "projekt"):
        pfad = note_path(root, bereich, agent_role, project_key)
        if not pfad or not await faellig(db, owner_id, pfad):
            continue
        bericht = await kuratiere_notiz(db, mcp, owner_id=owner_id, pfad=pfad, agent=agent,
                                        tokens=tokens or {}, base_urls=base_urls or {})
        if bericht:
            berichte.append(bericht)
    return berichte
