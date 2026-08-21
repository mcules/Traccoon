"""Mirror known addresses from the Obsidian vault into the database.

The contacts of the house lie in the vault (`03 Bereiche/Personen`, `…/Kontakte`,
`…/Firmen`), no longer in Nextcloud. For the spam detection that is the acquittal list:
whoever stands in the vault is not a stranger.

The vault is **not read per mail**. It comes over Syncthing and is therefore occasionally
half written or briefly gone; a mail assessment depending on it would decide wrongly exactly
then. Instead there is a periodic reconciliation into `assistant_contacts`, and the
assessment looks up in the index.

The frontmatter is read by hand instead of with a YAML tool: what is needed is a couple of
address fields, the notes are partly handwritten and not always valid YAML, and a parser
that discards the whole file on one crooked line would lose real contacts.
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
# Folders with people and companies. Deliberately a fixed list: the rest of the vault
# contains addresses from invoices, error messages and clipboards, and those do not belong
# on an acquittal list.
KONTAKT_FOLDER = (
    "03 Bereiche/Personen",
    "03 Bereiche/Kontakte",
    "03 Bereiche/Firmen",
)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]{2,}")
# Frontmatter keys that carry an address: email, email_privat, email_afu, email_arbeit,
# mail, E-Mail …
_MAIL_KEY_RE = re.compile(r"^\s*(-\s*)?(e-?mail|mail)[a-z_]*\s*:", re.IGNORECASE)
# Addresses from examples and templates lying around in the vault.
_IGNORIEREN = re.compile(
    r"(^|@)(example\.(com|org|net)|test\.|localhost|domain\.tld|deine?-?domain)", re.IGNORECASE)


def _frontmatter_und_body(text: str) -> tuple[list[str], str]:
    """Note to (frontmatter lines, rest). Without frontmatter: ([], the whole text)."""
    if not text.startswith("---"):
        return [], text
    lines = text.splitlines()
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            return lines[1:i], "\n".join(lines[i + 1:])
    return [], text


def adressen_aus_notiz(text: str) -> list[tuple[str, str]]:
    """[(address, origin)]; origin is 'frontmatter' or 'body'.

    Frontmatter addresses are declared contact data and are good for an acquittal. Addresses
    in the running text are weaker: the address of a third party who is being written about
    stands there sometimes. Both are stored but weighted differently.
    """
    fm, body = _frontmatter_und_body(text)
    out: list[tuple[str, str]] = []
    in_mail_block = False
    for line in fm:
        if _MAIL_KEY_RE.match(line):
            in_mail_block = True
            for m in _EMAIL_RE.finditer(line):
                out.append((m.group(0).lower(), "frontmatter"))
            continue
        # Continuation lines of a list (`  - address@…`) still belong to the address field.
        if in_mail_block and re.match(r"^\s+-\s", line):
            for m in _EMAIL_RE.finditer(line):
                out.append((m.group(0).lower(), "frontmatter"))
            continue
        if line.strip() and not line.startswith((" ", "\t")):
            in_mail_block = False
    for m in _EMAIL_RE.finditer(body):
        out.append((m.group(0).lower(), "body"))
    # The frontmatter wins when the same address turns up in both.
    beste: dict[str, str] = {}
    for adresse, herkunft in out:
        if _IGNORIEREN.search(adresse):
            continue
        if beste.get(adresse) != "frontmatter":
            beste[adresse] = herkunft
    return sorted(beste.items())


def _title(path: Path) -> str:
    return path.stem


async def sync_contacts(db: AsyncSession, owner_id: int | None,
                        vault_root: str | None = None) -> tuple[int, int]:
    """Vault to `assistant_contacts`. (created/updated, deleted). Commits itself.

    The reconciliation is a mirror: what disappears in the vault disappears here as well. So
    that a vault that is not mounted or momentarily empty does not clear the whole acquittal
    list, the function aborts when it finds *no* address at all.
    """
    root = Path(vault_root or VAULT_ROOT)
    if not root.is_dir():
        log.warning("Vault not reachable (%s), contact reconciliation skipped", root)
        return 0, 0

    gefunden: dict[str, tuple[str, str, str]] = {}  # adresse → (name, pfad, herkunft)
    for folder in KONTAKT_FOLDER:
        directory = root / folder
        if not directory.is_dir():
            log.info("Contact folder missing in the vault: %s", folder)
            continue
        for file in directory.rglob("*.md"):
            try:
                text = file.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                log.warning("Contact note %s not readable: %s", file, exc)
                continue
            rel = str(file.relative_to(root))
            for adresse, herkunft in adressen_aus_notiz(text):
                vorher = gefunden.get(adresse)
                if vorher is None or (vorher[2] == "body" and herkunft == "frontmatter"):
                    gefunden[adresse] = (_title(file), rel, herkunft)

    if not gefunden:
        log.warning("The vault reconciliation found no address at all, the existing set stays untouched")
        return 0, 0

    # Only the vault part is mirrored: addresses from the sent folder (`source_kind='sent'`)
    # stand in the same table but have a different source, so they must be neither updated
    # nor cleared away by this reconciliation.
    bestand = {
        row.email: row for row in (await db.execute(select(AssistantContact).where(
            AssistantContact.owner_user_id == owner_id,
            AssistantContact.source_kind.in_(("frontmatter", "body"))))).scalars().all()
    }
    changed = 0
    for adresse, (name, path, herkunft) in gefunden.items():
        row = bestand.pop(adresse, None)
        if row is None:
            db.add(AssistantContact(
                owner_user_id=owner_id, email=adresse,
                domain=adresse.split("@", 1)[1] if "@" in adresse else "",
                name=name[:300], source_path=path[:500], source_kind=herkunft))
            changed += 1
        elif (row.name, row.source_path, row.source_kind) != (name[:300], path[:500], herkunft):
            row.name, row.source_path, row.source_kind = name[:300], path[:500], herkunft
            changed += 1
    for row in bestand.values():   # no longer present in the vault
        await db.delete(row)
    entfernt = len(bestand)
    await db.commit()
    log.info("Vault contacts reconciled: %d addresses, %d changed, %d removed",
             len(gefunden), changed, entfernt)
    return changed, entfernt


_TITLE_RE = re.compile(r"^\s*(herr|frau|dr\.?|prof\.?|dipl\.?-?\w*|mr\.?|mrs\.?|ms\.?)\s+",
                       re.IGNORECASE)


def _namensform(name: str) -> str:
    """Bring a display name into a comparable form (salutation and title away, lower case).

    Deliberately without folding umlauts: "Müller" and "Mueller" are different spellings of
    the same person, but also the pattern of an imitation, so better not to equate them here.
    """
    name = (name or "").strip().strip("\"'")
    while True:
        gekuerzt = _TITLE_RE.sub("", name)
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
    """Domains I actually have to do with.

    They turn a foreign brand in the sender into a signal: if `sparkasse.de` stands in my
    contacts, then `sparkasse.de.sicherheit.top` is an imitation; without the stock it would
    only be an arbitrary domain. Only frontmatter addresses, because running text carries the
    domains of third parties as well. Addresses I have written to myself count too: whoever
    got an answer from me is as known as a vault entry.
    """
    rows = (await db.execute(select(AssistantContact.domain).where(
        AssistantContact.owner_user_id == owner_id,
        AssistantContact.source_kind.in_(("frontmatter", "sent"))).distinct())).scalars().all()
    return frozenset(d.lower() for d in rows if d)


async def namens_kollision(db: AsyncSession, owner_id: int | None, anzeigename: str,
                           sender_email: str) -> str:
    """The display name is a known contact but the address is not theirs. Returns the name.

    That is the boss scam (BEC): no link, no attachment, no forgery in the technical sense.
    The sender simply takes on the name of an acquaintance and writes from an address of
    their own. Technically there is nothing wrong with it; only the contact stock gives it
    away, and that is why this check can only stand here.

    Requires at least two name parts: "info" or "support" are not people, and a single part
    name would match by chance constantly.
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


async def kontakt_hits(db: AsyncSession, owner_id: int | None,
                          sender_email: str, sender_domain: str) -> str:
    """'frontmatter' | 'body' | 'domain' | '': how well the sender is known.

    The domain only counts with own or company domains; with freemailers it says nothing
    (everybody hangs off gmx.de), which is why the caller checks that beforehand.
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
