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
   Request ab (HTTP 400) — aus einem drohenden Fehler wäre ein sicherer geworden. Verboten
   ist deshalb genau eine Schnittstelle: vor einer `tool`-Antwort.
2. **Der Auftrag bleibt.** System-Prompt und die erste Anweisung überleben jede Kompaktierung.
   Wer den Auftrag wegkürzt, spart Tokens und verliert die Aufgabe.
3. **Zusammengefasst wird stückweise.** Das Aux-Modell ist klein; ein 500k-Zeichen-Verlauf
   passt nicht in einen Auftrag. Er wird darum an zulässigen Nähten in Häppchen geschnitten
   und Stück für Stück zusammengefasst — statt es mit allem auf einmal zu versuchen und mit
   leeren Händen dazustehen.

Schlägt das Aux-Modell für ein Häppchen fehl, steht an dessen Stelle ein ehrlicher Marker;
der Rest der Zusammenfassung bleibt. Ein Lauf ohne Teile seiner Vorgeschichte ist unangenehm;
ein abgebrochener Lauf ist schlimmer — ein Lauf ohne jede Vorgeschichte fängt von vorn an.
"""
from __future__ import annotations

import asyncio
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
# So viele Häppchen höchstens pro Kompaktierung. Jedes ist ein eigener Aux-Aufruf; ohne
# Deckel könnte ein sehr langer Verlauf einen großen Teil der Laufzeit im Zusammenfassen
# verbringen. Was nicht mehr drankommt, bleibt wörtlich stehen und wird beim nächsten Mal
# gefasst — nichts geht verloren, es dauert nur eine Runde länger.
MAX_STUECKE = 12
# Wie viele Häppchen gleichzeitig ans Aux-Modell gehen. Nacheinander wären zwölf Aufrufe
# à bis zu zwei Minuten die halbe Laufzeit eines Agentenlaufs; alle auf einmal erschlagen
# den kleinen lokalen Endpoint.
AUX_PARALLEL = 3


def _kopf_ende(messages: list[dict]) -> int:
    """Index hinter dem unantastbaren Anfang (führende system-Nachrichten + erster Auftrag)."""
    i = 0
    while i < len(messages) and messages[i].get("role") == "system":
        i += 1
    if i < len(messages) and messages[i].get("role") == "user":
        i += 1                                  # der eigentliche Auftrag
    return i


def _schnittfaehig(m: dict) -> bool:
    """Darf VOR dieser Nachricht geschnitten werden?

    Unzulässig ist genau eine Sorte: die `tool`-Antwort. Sie hängt am vorausgehenden
    `assistant` mit `tool_calls` und darf nie von ihm getrennt werden. Alles andere —
    `user`, `system` UND `assistant` — beginnt einen neuen Zug und ist eine saubere Naht.

    Früher galten nur `user`/`system` als Naht. Bei einem Agenten, der 60 Runden lang
    ausschließlich Werkzeuge ruft, gibt es die praktisch nicht: der Verlauf besteht aus
    assistant/tool-Paaren. Damit kannte die Kürzung nur zwei Ausgänge — fast nichts
    (die ältesten vier Nachrichten) oder alles (Verlauf auf drei Nachrichten eingedampft,
    Agent ohne Gedächtnis, fängt von vorn an). Genau daran hing UNI-4 am 2026-08-06 zwei
    volle Läufe lang fest, ohne eine einzige Datei zu schreiben.
    """
    return m.get("role") != "tool" and not m.get("tool_call_id")


def _sichere_grenze(messages: list[dict], ab: int) -> int:
    """Nächster Index ab `ab`, an dem geschnitten werden darf.

    Sicher ist der Beginn jeder Nachricht, die keine `tool`-Antwort ist: die hängt an einem
    vorausgehenden `assistant` mit `tool_calls` und darf nie davon getrennt werden.
    """
    for i in range(ab, len(messages)):
        if _schnittfaehig(messages[i]):
            return i
    return len(messages)


def _sichere_grenze_rueckwaerts(messages: list[dict], bis_hoechstens: int) -> int | None:
    """Größter zulässiger Schnitt, der NICHT hinter `bis_hoechstens` liegt — oder None.

    Braucht es, weil die Vorwärtssuche einen Block nie kleiner machen kann: liegt zwischen
    Wunschstelle und aktueller Grenze keine Naht, springt sie über die Grenze hinaus. Wer
    damit einen Block verkleinern will, dreht sich im Kreis.
    """
    for i in range(min(bis_hoechstens, len(messages)) - 1, -1, -1):
        if _schnittfaehig(messages[i]):
            return i
    return None


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


UEBERGABE_AUFTRAG = (
    "Der folgende Agenten-Lauf wurde an einer Grenze beendet (Zeit, Iterationen oder Tokens) "
    "und wird gleich in einem FRISCHEN Lauf fortgesetzt — der weiß nichts außer dem, was du "
    "jetzt aufschreibst. Schreib die Übergabe an ihn, in genau diesen drei Abschnitten:\n\n"
    "**Erkenntnisse** — was ich über den Code herausgefunden habe, mit Datei-Pfaden, "
    "Funktions- und Feldnamen. Das erspart dem nächsten Lauf das erneute Suchen.\n"
    "**Erledigt** — welche Dateien ich bereits geändert habe und was darin steht. Wenn "
    "nichts geändert wurde: schreib genau das hin.\n"
    "**Nächster Schritt** — was der nächste Lauf ALS ERSTES tun soll, konkret.\n\n"
    "Keine Vorrede, deutsch, dicht. Erfinde nichts: was nicht im Ausschnitt steht, gehört "
    "nicht in die Übergabe.\n\n--- Lauf ---\n"
)


async def uebergabe(db, *, messages: list[dict], grund: str, letzter_text: str,
                    owner_id, agent, tokens: dict, base_urls: dict) -> str:
    """Übergabe an den Fortsetzungs-Lauf: was gelernt, was getan, was als Nächstes.

    Bis hierher stand in der Fortsetzung nur `grund` plus der letzte Satz des Agenten. Das
    reichte nicht einmal, um zu wissen, welche Dateien schon gelesen waren: UNI-12 begann
    am 2026-08-07 drei Läufe hintereinander mit `open_tasks` und derselben Suchanfrage und
    schrieb in anderthalb Stunden keine Zeile Code. Der Lauf endet an einer Grenze — der
    Faden muss deshalb aus dem Verlauf gerettet werden, nicht aus seinem letzten Satz.

    Fällt das Aux-Modell aus, bleibt die alte, ehrliche Notlösung.
    """
    from .aux import aux_chat

    notloesung = f"{grund}\n\nLetzter Stand:\n{letzter_text or '(kein Text)'}"
    von = _kopf_ende(messages)
    if len(messages) - von < MINDEST_BLOCK:
        return notloesung
    stuecke = _haeppchen(messages, von, len(messages))[:MAX_STUECKE]
    roh = await _zusammenfassen(db, messages, stuecke, owner_id=owner_id, agent=agent,
                                tokens=tokens, base_urls=base_urls)
    if not roh.strip():
        return notloesung
    # Zweiter Durchgang: aus den Stück-Zusammenfassungen wird die eigentliche Übergabe.
    # Bei einem einzigen Stück wäre das eine Zusammenfassung der Zusammenfassung — dann
    # lieber direkt am Verlauf arbeiten.
    quelle = roh if len(stuecke) > 1 else _als_text(messages[von:])[:MAX_AUX_ZEICHEN]
    text = await aux_chat(
        db, owner_id=owner_id, task="compression",
        messages=[{"role": "user", "content": UEBERGABE_AUFTRAG + quelle}],
        agent=agent, tokens=tokens, base_urls=base_urls, max_tokens=1500)
    if not text:
        log.warning("Übergabe ohne Aux-Modell — es bleibt beim letzten Stand")
        return f"{grund}\n\nStand aus dem Verlauf:\n{roh}"
    return f"{grund}\n\n{text.strip()}"


def _haeppchen(messages: list[dict], von: int, bis: int) -> list[tuple[int, int]]:
    """Den Block in Stücke schneiden, die das Aux-Modell fassen kann.

    Immer an zulässigen Nähten und immer mit Fortschritt: notfalls ist ein Stück eine
    einzige Nachricht (deren Text `_als_text` ohnehin bei 4000 Zeichen kappt).
    """
    stuecke: list[tuple[int, int]] = []
    start = von
    while start < bis:
        ende = bis
        while ende > start + 1 and len(_als_text(messages[start:ende])) > MAX_AUX_ZEICHEN:
            kleiner = _sichere_grenze_rueckwaerts(messages, start + (ende - start) // 2)
            if kleiner is None or kleiner <= start or kleiner >= ende:
                ende = start + 1        # keine Naht mehr → eine Nachricht, aber Fortschritt
                break
            ende = kleiner
        stuecke.append((start, ende))
        start = ende
    return stuecke


async def _zusammenfassen(db, messages: list[dict], stuecke: list[tuple[int, int]], *,
                          owner_id, agent, tokens, base_urls) -> str:
    """Jedes Stück einzeln zusammenfassen und die Teile aneinanderhängen.

    Früher wurde stattdessen der BLOCK verkleinert, bis er in einen Aux-Auftrag passte —
    fand sich keine Naht, ging der ganze Verlauf in einem Rutsch an ein Modell mit 32k
    Kontext. Das lehnte ab, `aux_chat` lieferte nichts, und der Agent bekam statt einer
    Zusammenfassung den Hinweis, dass er nichts mehr weiß. Stückweise kommt die
    Zusammenfassung auch für einen 500k-Zeichen-Verlauf zustande.
    """
    from .aux import aux_chat
    zaehler = asyncio.Semaphore(AUX_PARALLEL)

    async def _stueck(nr: int, a: int, b: int) -> str:
        von_wo = f"(Teil {nr} von {len(stuecke)})\n\n" if len(stuecke) > 1 else ""
        async with zaehler:
            try:
                text = await aux_chat(
                    db, owner_id=owner_id, task="compression",
                    messages=[{"role": "user",
                               "content": AUFTRAG + von_wo + _als_text(messages[a:b])}],
                    agent=agent, tokens=tokens, base_urls=base_urls, max_tokens=1024)
            except Exception:  # noqa: BLE001 — ein Aussetzer darf nicht den Lauf kosten
                log.exception("Kompaktierung: Stück %d/%d fehlgeschlagen", nr, len(stuecke))
                text = None
        if text:
            return text.strip()
        log.warning("Kompaktierung: Stück %d/%d ohne Zusammenfassung (Aux nicht verfügbar)",
                    nr, len(stuecke))
        return (f"- (Teil {nr}: {b - a} Nachrichten, Zusammenfassung nicht möglich — "
                "dieser Abschnitt ist verloren, im Zweifel nachprüfen.)")

    teile = await asyncio.gather(*[_stueck(nr, a, b)
                                   for nr, (a, b) in enumerate(stuecke, 1)])
    return "\n".join(teile)


async def kompaktiere(db, *, messages: list[dict], grenze_tokens: int, gemessen: int,
                      owner_id: int | None, agent, tokens: dict, base_urls: dict) -> list[dict] | None:
    """Verlauf kürzen. Gibt die neue Nachrichtenliste zurück — oder None, wenn nichts zu tun war."""
    bereich = plan(messages, grenze_tokens, gemessen)
    if bereich is None:
        return None
    von, bis = bereich
    # Der ganze Block wird zusammengefasst — aber in Häppchen, die das (kleine, lokale)
    # Aux-Modell auch annimmt. Am 2026-07-31 stand der Worker 8 Stunden bei 100 % CPU an
    # dieser Stelle, weil die Verkleinerung über die VORWÄRTS-Suche lief und bei einem
    # reinen Werkzeug-Verlauf immer dieselbe Grenze zurückgab; deshalb sucht `_haeppchen`
    # rückwärts und erzwingt in jeder Runde Fortschritt.
    stuecke = _haeppchen(messages, von, bis)
    if len(stuecke) > MAX_STUECKE:
        # Nur den ältesten Teil fassen; der Rest bleibt wörtlich und kommt beim nächsten Mal.
        stuecke = stuecke[:MAX_STUECKE]
        bis = stuecke[-1][1]
    zusammenfassung = await _zusammenfassen(db, messages, stuecke, owner_id=owner_id,
                                            agent=agent, tokens=tokens, base_urls=base_urls)
    log.info("Kompaktierung: %d Nachrichten in %d Stück(en) zusammengefasst",
             bis - von, len(stuecke))

    ersatz = ("# Zusammenfassung des bisherigen Verlaufs\n"
              "(Der ausführliche Verlauf wurde gekürzt, um im Kontextfenster zu bleiben. "
              "Was hier steht, ist alles, was davon bleibt — arbeite damit weiter, statt "
              "noch einmal von vorn zu beginnen.)\n\n" + zusammenfassung)

    return messages[:von] + [{"role": "system", "content": ersatz}] + messages[bis:]
