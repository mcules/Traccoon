"""Langen Verlauf zusammenfassen, statt in den Provider-Fehler zu laufen.

`agent_definitions.max_context_tokens` gab es schon — gelesen hat es niemand. Wer den Wert
setzte, glaubte an eine Schutzgrenze, die nicht existierte: der Lauf wuchs weiter, bis das
Modell den Request ablehnte, und dann war der ganze Lauf verloren.

Jetzt wird nach jedem Modell-Aufruf die tatsächliche Kontextgröße (`usage.input_tokens`)
gemessen. Reißt sie die Schwelle, wird der mittlere Teil des Verlaufs durch eine
Zusammenfassung ersetzt — erzeugt vom Aux-Modell, nicht vom Arbeitsmodell.

Die zwei Fallen, die das hier bestimmen:

1. **Man darf nicht irgendwo schneiden.** Ein `assistant` mit `tool_calls` und die
   zugehörigen `tool`-Antworten sind eine Einheit; trennt man sie, lehnt der Provider den
   Request ab (HTTP 400) — aus einem drohenden Fehler wäre ein sicherer geworden. Geschnitten
   wird darum nur vor einer `user`/`system`-Nachricht.
2. **Der Auftrag bleibt.** System-Prompt und die erste Anweisung überleben jede Kompaktierung.
   Wer den Auftrag wegkürzt, spart Tokens und verliert die Aufgabe.

Schlägt das Aux-Modell fehl, wird trotzdem gekürzt — mit ehrlichem Marker an der Schnittstelle.
Ein Lauf ohne Teile seiner Vorgeschichte ist unangenehm; ein abgebrochener Lauf ist schlimmer.
"""
from __future__ import annotations

import logging

log = logging.getLogger("traccoon.compaction")

# Ab welchem Anteil des erlaubten Kontexts kompaktiert wird. Nicht erst bei 100 %: die
# Zusammenfassung selbst und die nächste Antwort brauchen ebenfalls Platz.
SCHWELLE = 0.8
# So viele Nachrichten am Ende bleiben unangetastet — der unmittelbare Arbeitszusammenhang.
BEHALTEN = 6
# Weniger als das lohnt nicht: dann ist der Verlauf so kurz, dass die Zusammenfassung
# ungefähr so lang würde wie das Original.
MINDEST_BLOCK = 4
# Wie viel Text höchstens in EINEN Zusammenfassungs-Auftrag geht. Das Aux-Modell ist
# bewusst klein (lokal, 32k Kontext) — schickt man ihm den ganzen Verlauf eines großen
# Modells, lehnt es ab, und die Kompaktierung liefe immer in den harten Schnitt. Passt der
# Block nicht, wird eben nur sein ältester Teil gefasst; der Rest kommt beim nächsten Mal.
MAX_AUX_ZEICHEN = 50_000


def _kopf_ende(messages: list[dict]) -> int:
    """Index hinter dem unantastbaren Anfang (führende system-Nachrichten + erster Auftrag)."""
    i = 0
    while i < len(messages) and messages[i].get("role") == "system":
        i += 1
    if i < len(messages) and messages[i].get("role") == "user":
        i += 1                                  # der eigentliche Auftrag
    return i


def _sichere_grenze(messages: list[dict], ab: int) -> int:
    """Nächster Index ab `ab`, an dem geschnitten werden darf.

    Sicher ist nur der Beginn einer `user`- oder `system`-Nachricht: `tool`-Ergebnisse hängen
    an einem vorausgehenden `assistant` mit `tool_calls` und dürfen nie davon getrennt werden.
    """
    for i in range(ab, len(messages)):
        m = messages[i]
        if m.get("role") in ("user", "system") and not m.get("tool_call_id"):
            return i
    return len(messages)


def plan(messages: list[dict], grenze_tokens: int, gemessen: int) -> tuple[int, int] | None:
    """(von, bis) des zusammenzufassenden Blocks — oder None, wenn nichts zu tun ist."""
    if not grenze_tokens or gemessen < grenze_tokens * SCHWELLE:
        return None
    von = _kopf_ende(messages)
    bis = _sichere_grenze(messages, max(von, len(messages) - BEHALTEN))
    if bis - von < MINDEST_BLOCK:
        return None
    return von, bis


def _als_text(messages: list[dict]) -> str:
    teile = []
    for m in messages:
        rolle = m.get("role", "?")
        inhalt = m.get("content")
        if isinstance(inhalt, list):        # Anthropic-Blöcke → nur die Textanteile
            inhalt = " ".join(b.get("text", "") for b in inhalt if isinstance(b, dict))
        inhalt = (inhalt or "").strip()
        if not inhalt and m.get("tool_calls"):
            inhalt = "(ruft Werkzeuge auf: " + ", ".join(
                (c.get("function") or {}).get("name", "?") for c in m["tool_calls"]) + ")"
        if inhalt:
            teile.append(f"[{rolle}] {inhalt[:4000]}")
    return "\n\n".join(teile)


AUFTRAG = (
    "Fasse den folgenden Ausschnitt eines Agenten-Laufs zusammen. Die Zusammenfassung ERSETZT "
    "den Ausschnitt — was hier fehlt, ist für den weiteren Lauf verloren.\n\n"
    "Nimm auf: erledigte Schritte und ihr Ergebnis, getroffene Entscheidungen samt Begründung, "
    "gefundene Fakten (Namen, Pfade, IDs, Zahlen), offene Fäden und alles, was der Mensch "
    "vorgegeben hat. Lass weg: Wiederholungen, Werkzeug-Rohausgaben, Höflichkeiten.\n\n"
    "Schreib in Stichpunkten, deutsch, ohne Vorrede.\n\n--- Ausschnitt ---\n"
)


async def kompaktiere(db, *, messages: list[dict], grenze_tokens: int, gemessen: int,
                      owner_id: int | None, agent, tokens: dict, base_urls: dict) -> list[dict] | None:
    """Verlauf kürzen. Gibt die neue Nachrichtenliste zurück — oder None, wenn nichts zu tun war."""
    bereich = plan(messages, grenze_tokens, gemessen)
    if bereich is None:
        return None
    von, bis = bereich
    # Nur so viel in einen Auftrag, wie das (kleine) Aux-Modell fassen kann. Lieber den
    # ältesten Teil zusammenfassen und den Rest wörtlich stehen lassen, als einen Auftrag
    # zu schicken, den das Modell abweist.
    while bis > von + MINDEST_BLOCK and len(_als_text(messages[von:bis])) > MAX_AUX_ZEICHEN:
        bis = _sichere_grenze(messages, von + (bis - von) // 2)
        if bis <= von + MINDEST_BLOCK:
            bis = _sichere_grenze(messages, von + MINDEST_BLOCK)
            break

    from .aux import aux_chat
    zusammenfassung = await aux_chat(
        db, owner_id=owner_id, task="compression",
        messages=[{"role": "user", "content": AUFTRAG + _als_text(messages[von:bis])}],
        agent=agent, tokens=tokens, base_urls=base_urls, max_tokens=2048)

    if zusammenfassung:
        ersatz = ("# Zusammenfassung des bisherigen Verlaufs\n"
                  "(Der ausführliche Verlauf wurde gekürzt, um im Kontextfenster zu bleiben. "
                  "Was hier steht, ist alles, was davon bleibt.)\n\n" + zusammenfassung)
    else:
        # Aux nicht erreichbar: trotzdem kürzen, aber ehrlich sagen, dass hier etwas fehlt —
        # sonst hält der Agent seine Lücke für Vollständigkeit.
        ersatz = (f"# Verlauf gekürzt\n{bis - von} Nachrichten wurden entfernt, um im "
                  "Kontextfenster zu bleiben; eine Zusammenfassung war nicht möglich. Frühere "
                  "Schritte sind dir NICHT mehr bekannt — verlass dich nur auf das, was noch "
                  "hier steht, und prüfe im Zweifel nach.")
        log.warning("Kompaktierung ohne Zusammenfassung (Aux nicht verfügbar) — %d Nachrichten entfernt",
                    bis - von)

    return messages[:von] + [{"role": "system", "content": ersatz}] + messages[bis:]
