"""Bekannte Adressen aus dem Obsidian-Vault in die DB spiegeln.

Die Kontakte des Hauses liegen im Vault (`03 Bereiche/Personen`, `…/Kontakte`, `…/Firmen`),
nicht mehr in Nextcloud. Für die Spam-Erkennung ist das die Freispruch-Liste: wer im Vault
steht, ist kein Fremder.

Der Vault wird **nicht pro Mail** gelesen. Er kommt über Syncthing und ist damit
gelegentlich halb geschrieben oder kurz weg; eine Mail-Beurteilung, die daran hängt, würde
genau dann falsch entscheiden. Stattdessen periodischer Abgleich in `assistant_contacts`,
und die Beurteilung schlägt im Index nach.

Frontmatter wird von Hand gelesen statt mit einem YAML-Werkzeug: gebraucht werden ein paar
Adressfelder, die Notizen sind teils handgeschrieben und nicht immer gültiges YAML, und ein
Parser, der an einer krummen Zeile die ganze Datei verwirft, verlöre echte Kontakte.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.assistant import AssistantContact

log = logging.getLogger("traccoon.spam")

VAULT_ROOT = os.getenv("VAULT_PATH", "/vault")
# Ordner mit Personen/Firmen. Bewusst eine feste Liste: der übrige Vault enthält Adressen
# aus Rechnungen, Fehlermeldungen und Zwischenablagen — die gehören nicht auf eine
# Freispruch-Liste.
KONTAKT_ORDNER = (
    "03 Bereiche/Personen",
    "03 Bereiche/Kontakte",
    "03 Bereiche/Firmen",
)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]{2,}")
# Frontmatter-Schlüssel, die eine Adresse tragen: email, email_privat, email_afu,
# email_arbeit, mail, E-Mail …
_MAIL_KEY_RE = re.compile(r"^\s*(-\s*)?(e-?mail|mail)[a-z_]*\s*:", re.IGNORECASE)
# Adressen aus Beispielen/Vorlagen, die im Vault herumliegen.
_IGNORIEREN = re.compile(
    r"(^|@)(example\.(com|org|net)|test\.|localhost|domain\.tld|deine?-?domain)", re.IGNORECASE)


def _frontmatter_und_body(text: str) -> tuple[list[str], str]:
    """Notiz → (Frontmatter-Zeilen, Rest). Ohne Frontmatter: ([], ganzer Text)."""
    if not text.startswith("---"):
        return [], text
    zeilen = text.splitlines()
    for i in range(1, len(zeilen)):
        if zeilen[i].strip() in ("---", "..."):
            return zeilen[1:i], "\n".join(zeilen[i + 1:])
    return [], text


def adressen_aus_notiz(text: str) -> list[tuple[str, str]]:
    """[(adresse, herkunft)] — herkunft ist 'frontmatter' oder 'body'.

    Frontmatter-Adressen sind ausgewiesene Kontaktdaten und taugen als Freispruch. Adressen
    im Fließtext sind schwächer: dort steht auch mal die Adresse eines Dritten, über den
    geschrieben wird. Beide werden gespeichert, aber unterschiedlich gewichtet.
    """
    fm, body = _frontmatter_und_body(text)
    out: list[tuple[str, str]] = []
    in_mail_block = False
    for zeile in fm:
        if _MAIL_KEY_RE.match(zeile):
            in_mail_block = True
            for m in _EMAIL_RE.finditer(zeile):
                out.append((m.group(0).lower(), "frontmatter"))
            continue
        # Fortsetzungszeilen einer Liste (`  - adresse@…`) gehören noch zum Adressfeld.
        if in_mail_block and re.match(r"^\s+-\s", zeile):
            for m in _EMAIL_RE.finditer(zeile):
                out.append((m.group(0).lower(), "frontmatter"))
            continue
        if zeile.strip() and not zeile.startswith((" ", "\t")):
            in_mail_block = False
    for m in _EMAIL_RE.finditer(body):
        out.append((m.group(0).lower(), "body"))
    # Frontmatter gewinnt, wenn dieselbe Adresse in beiden auftaucht.
    beste: dict[str, str] = {}
    for adresse, herkunft in out:
        if _IGNORIEREN.search(adresse):
            continue
        if beste.get(adresse) != "frontmatter":
            beste[adresse] = herkunft
    return sorted(beste.items())


def _titel(pfad: Path) -> str:
    return pfad.stem


async def sync_contacts(db: AsyncSession, owner_id: int | None,
                        vault_root: str | None = None) -> tuple[int, int]:
    """Vault → `assistant_contacts`. (angelegt/aktualisiert, gelöscht). Committet selbst.

    Der Abgleich ist ein Spiegel: was im Vault verschwindet, verschwindet auch hier. Damit
    ein nicht gemounteter oder gerade leerer Vault nicht die ganze Freispruch-Liste
    abräumt, bricht die Funktion ab, wenn sie *gar keine* Adresse findet.
    """
    root = Path(vault_root or VAULT_ROOT)
    if not root.is_dir():
        log.warning("Vault nicht erreichbar (%s) — Kontakt-Abgleich übersprungen", root)
        return 0, 0

    gefunden: dict[str, tuple[str, str, str]] = {}  # adresse → (name, pfad, herkunft)
    for ordner in KONTAKT_ORDNER:
        verzeichnis = root / ordner
        if not verzeichnis.is_dir():
            log.info("Kontaktordner fehlt im Vault: %s", ordner)
            continue
        for datei in verzeichnis.rglob("*.md"):
            try:
                text = datei.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                log.warning("Kontaktnotiz %s nicht lesbar: %s", datei, exc)
                continue
            rel = str(datei.relative_to(root))
            for adresse, herkunft in adressen_aus_notiz(text):
                vorher = gefunden.get(adresse)
                if vorher is None or (vorher[2] == "body" and herkunft == "frontmatter"):
                    gefunden[adresse] = (_titel(datei), rel, herkunft)

    if not gefunden:
        log.warning("Vault-Abgleich fand keine einzige Adresse — Bestand bleibt unangetastet")
        return 0, 0

    # Nur der Vault-Anteil wird gespiegelt: Adressen aus dem Gesendet-Ordner
    # (`source_kind='sent'`) stehen in derselben Tabelle, haben aber eine andere Quelle —
    # sie dürfen von diesem Abgleich weder aktualisiert noch abgeräumt werden.
    bestand = {
        row.email: row for row in (await db.execute(select(AssistantContact).where(
            AssistantContact.owner_user_id == owner_id,
            AssistantContact.source_kind.in_(("frontmatter", "body"))))).scalars().all()
    }
    geaendert = 0
    for adresse, (name, pfad, herkunft) in gefunden.items():
        row = bestand.pop(adresse, None)
        if row is None:
            db.add(AssistantContact(
                owner_user_id=owner_id, email=adresse,
                domain=adresse.split("@", 1)[1] if "@" in adresse else "",
                name=name[:300], source_path=pfad[:500], source_kind=herkunft))
            geaendert += 1
        elif (row.name, row.source_path, row.source_kind) != (name[:300], pfad[:500], herkunft):
            row.name, row.source_path, row.source_kind = name[:300], pfad[:500], herkunft
            geaendert += 1
    for row in bestand.values():   # im Vault nicht mehr vorhanden
        await db.delete(row)
    entfernt = len(bestand)
    await db.commit()
    log.info("Vault-Kontakte abgeglichen: %d Adressen, %d geändert, %d entfernt",
             len(gefunden), geaendert, entfernt)
    return geaendert, entfernt


_TITEL_RE = re.compile(r"^\s*(herr|frau|dr\.?|prof\.?|dipl\.?-?\w*|mr\.?|mrs\.?|ms\.?)\s+",
                       re.IGNORECASE)


def _namensform(name: str) -> str:
    """Anzeigename auf eine vergleichbare Form bringen (Anrede/Titel weg, Kleinschreibung).

    Bewusst ohne Umlaut-Faltung: „Müller" und „Mueller" sind verschiedene Schreibweisen
    desselben Menschen, aber auch das Muster eines Nachbaus — hier lieber nicht gleichsetzen.
    """
    name = (name or "").strip().strip("\"'")
    while True:
        gekuerzt = _TITEL_RE.sub("", name)
        if gekuerzt == name:
            break
        name = gekuerzt
    # „Beispiel, Rainer" → „Rainer Beispiel"
    if name.count(",") == 1:
        hinten, vorne = (t.strip() for t in name.split(","))
        if hinten and vorne:
            name = f"{vorne} {hinten}"
    return " ".join(name.lower().split())


async def bekannte_domains(db: AsyncSession, owner_id: int | None) -> frozenset[str]:
    """Domains, mit denen ich tatsächlich zu tun habe.

    Sie machen aus einer fremden Marke im Absender ein Signal: steht `sparkasse.de` in
    meinen Kontakten, ist `sparkasse.de.sicherheit.top` ein Nachbau — ohne den Bestand
    wäre das nur eine beliebige Domain. Nur Frontmatter-Adressen, denn Fließtext trägt
    auch die Domains Dritter. Adressen, denen ich selbst geschrieben habe, zählen ebenso:
    wer eine Antwort von mir bekommen hat, ist so bekannt wie ein Vault-Eintrag.
    """
    rows = (await db.execute(select(AssistantContact.domain).where(
        AssistantContact.owner_user_id == owner_id,
        AssistantContact.source_kind.in_(("frontmatter", "sent"))).distinct())).scalars().all()
    return frozenset(d.lower() for d in rows if d)


async def namens_kollision(db: AsyncSession, owner_id: int | None, anzeigename: str,
                           sender_email: str) -> str:
    """Der Anzeigename ist ein bekannter Kontakt — die Adresse aber nicht seine. → Name.

    Das ist die Chef-Masche (BEC): kein Link, kein Anhang, keine Fälschung im technischen
    Sinn. Der Absender legt sich schlicht den Namen eines Bekannten zu und schreibt von
    einer eigenen Adresse aus. Technisch ist daran nichts auszusetzen — nur der
    Kontaktbestand verrät es, und deshalb kann diese Prüfung nur hier stehen.

    Verlangt mindestens zwei Namensteile: „Info" oder „Support" sind keine Personen, und
    ein einteiliger Name träfe ständig zufällig zu.
    """
    form = _namensform(anzeigename)
    if not form or len(form.split()) < 2:
        return ""
    rows = (await db.execute(select(AssistantContact).where(
        AssistantContact.owner_user_id == owner_id))).scalars().all()
    passende = [r for r in rows if _namensform(r.name) == form]
    if not passende:
        return ""
    if any((r.email or "").lower() == (sender_email or "").lower() for r in passende):
        return ""       # derselbe Mensch, alles in Ordnung
    return passende[0].name


async def kontakt_treffer(db: AsyncSession, owner_id: int | None,
                          sender_email: str, sender_domain: str) -> str:
    """'frontmatter' | 'body' | 'domain' | '' — wie gut der Absender bekannt ist.

    Die Domain zählt nur bei eigenen/firmeneigenen Domains; bei Freemailern sagt sie
    nichts (an gmx.de hängt jeder), deshalb prüft der Aufrufer das vorher.
    """
    if sender_email:
        row = (await db.execute(select(AssistantContact).where(
            AssistantContact.owner_user_id == owner_id,
            AssistantContact.email == sender_email.lower()))).scalar_one_or_none()
        if row is not None:
            return row.source_kind or "frontmatter"
    if sender_domain:
        row = (await db.execute(select(AssistantContact).where(
            AssistantContact.owner_user_id == owner_id,
            AssistantContact.domain == sender_domain.lower(),
            AssistantContact.source_kind == "frontmatter").limit(1))).scalars().first()
        if row is not None:
            return "domain"
    return ""
