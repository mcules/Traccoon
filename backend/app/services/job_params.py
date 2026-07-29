"""Platzhalter in Prompt-Jobs — damit ein Job eine Vorlage sein kann statt einer Kopie.

Der KI-&-Tech-News-Job trug sein Wissen (Quellen, Aufbau, Zeitfenster) fest im Prompt. Ein
zweiter Digest — Security, Funk, was auch immer — hieß darum: Prompt kopieren und an drei
Stellen editieren. Jetzt trägt `jobs.args` die Parameter, der Prompt nur noch `{{platzhalter}}`.

Bewusst KEINE Template-Engine: reine Textersetzung, kein Ausdruck, kein Code. Ein Prompt ist
Nutzereingabe, die anschließend an ein Modell geht — dort hat Auswertung nichts zu suchen.

`args` ist historisch die Argumentliste der script-Jobs. Nur ein **Objekt** gilt als
Parametersatz; eine Liste bleibt unangetastet Script-Argument.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Berlin")

_PLATZHALTER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def _als_text(wert) -> str:
    """Parameterwert → Prompt-Text. Listen als Aufzählung in einer Zeile, Objekte als JSON."""
    if wert is None:
        return ""
    if isinstance(wert, bool):
        return "ja" if wert else "nein"
    if isinstance(wert, (list, tuple)):
        return ", ".join(_als_text(x) for x in wert if x is not None and x != "")
    if isinstance(wert, dict):
        return json.dumps(wert, ensure_ascii=False)
    return str(wert)


def eingebaute_werte(*, jetzt: dt.datetime | None = None,
                     letzter_lauf: dt.datetime | None = None) -> dict[str, str]:
    """Zeitangaben, die praktisch jeder wiederkehrende Job braucht.

    `seit` ist der letzte Lauf — ohne ihn (erster Lauf, Job war aus) 24 Stunden zurück. Damit
    fragt ein täglicher Digest nach „seit dem letzten Mal" statt nach einer Zahl, die im Prompt
    steht und beim Umstellen des Zeitplans still falsch wird.
    """
    jetzt = (jetzt or dt.datetime.now(tz=dt.timezone.utc)).astimezone(TZ)
    seit = (letzter_lauf.astimezone(TZ) if letzter_lauf else jetzt - dt.timedelta(days=1))
    return {
        "heute": jetzt.strftime("%Y-%m-%d"),
        "jetzt": jetzt.strftime("%Y-%m-%d %H:%M"),
        "seit": seit.strftime("%Y-%m-%d %H:%M"),
        "zeitfenster": f"{seit.strftime('%Y-%m-%d %H:%M')} bis {jetzt.strftime('%Y-%m-%d %H:%M')} "
                       f"({TZ.key})",
    }


def parameter(args) -> dict:
    """Der Parametersatz eines Jobs — nur ein Objekt zählt, eine Liste ist Script-Argument."""
    return dict(args) if isinstance(args, dict) else {}


def rendere(prompt: str, args=None, *, jetzt: dt.datetime | None = None,
            letzter_lauf: dt.datetime | None = None) -> str:
    """`{{name}}` ersetzen: erst die Parameter des Jobs, dann die eingebauten Zeitwerte.

    Ein unbekannter Platzhalter bleibt WÖRTLICH stehen. Stillschweigend zu leeren wäre der
    schlechtere Ausgang: der Auftrag verlöre lautlos eine Vorgabe, statt dass im Ergebnis
    sichtbar `{{quellen}}` steht und der Fehler auffällt.
    """
    werte = {**eingebaute_werte(jetzt=jetzt, letzter_lauf=letzter_lauf)}
    werte.update({k: _als_text(v) for k, v in parameter(args).items()})

    def ersetze(m: re.Match) -> str:
        name = m.group(1)
        return werte[name] if name in werte else m.group(0)

    return _PLATZHALTER.sub(ersetze, prompt or "")


def offene_platzhalter(prompt: str, args=None) -> list[str]:
    """Platzhalter ohne Wert — für die Vorschau/Prüfung beim Anlegen eines Jobs."""
    bekannt = set(eingebaute_werte()) | set(parameter(args))
    return sorted({m.group(1) for m in _PLATZHALTER.finditer(prompt or "")} - bekannt)
