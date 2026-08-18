import datetime as dt

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import TimestampMixin


class AssistantTask(TimestampMixin, Base):
    """Projektloses Arbeits-Item des persönlichen Assistenten (steht über den Projekten).

    Entsteht z. B. aus einer eingehenden E-Mail: ein LOKALES Modell (qwen via litellm)
    klassifiziert und schwärzt den Inhalt VORHER — nur `redacted_summary` (+ Metadaten)
    verlässt das Haus Richtung Claude. Der Rohtext bleibt lokal; Claude liest den Volltext
    erst nach ausdrücklicher Freigabe (Status `new` → `approved`) über die IMAP-Tools.
    """
    __tablename__ = "assistant_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Eigentümer = der Mensch, dem der Assistent dient; sein Token, seine MCP-Gruppe.
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)

    kind: Mapped[str] = mapped_column(String(30), default="email")  # email | note | …
    source: Mapped[str] = mapped_column(String(120), default="")     # z. B. webhook:new-email
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)  # Idempotenz

    title: Mapped[str] = mapped_column(String(500), default="")
    # Ergebnis der LOKALEN Vorklassifizierung (qwen) — bleibt im Haus erzeugt.
    category: Mapped[str] = mapped_column(String(80), default="")
    priority: Mapped[str] = mapped_column(String(20), default="normal")  # low|normal|high|urgent
    # Bereinigte, an Claude weiterreichbare Zusammenfassung (KEIN Rohtext, keine PII).
    redacted_summary: Mapped[str] = mapped_column(Text, default="")
    # Metadaten für den späteren IMAP-Volltextzugriff (account/uid/from/subject) — kein Inhalt.
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    # Schwärzung dieses Items: 'redacted' = nur Summary an Claude (Default, sicher);
    # 'unredacted' = Volltext direkt (nur wenn eine AssistantPolicy das für die Quelle erlaubt).
    redaction: Mapped[str] = mapped_column(String(20), default="redacted")
    # Rohtext NUR gespeichert, wenn eine Regel 'unredacted' erlaubt (sonst NULL → nie im Haus abgelegt).
    raw_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Gelernter Handlungs-Hinweis aus der greifenden AssistantPolicy (z. B. „in Paperless ablegen").
    action_hint: Mapped[str] = mapped_column(Text, default="")

    # new = wartet auf Freigabe (nichts läuft); approved = freigegeben → Worker;
    # running → done | error.
    status: Mapped[str] = mapped_column(String(20), default="new", index=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Tool-Gate: worauf der Lauf gerade auf Freigabe wartet (status='awaiting').
    pending_tool: Mapped[str | None] = mapped_column(String(150), nullable=True)
    pending_resource: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Einmal-Freigabe für die nächste Wiederaufnahme (wird beim Gate konsumiert).
    grant_tool: Mapped[str | None] = mapped_column(String(150), nullable=True)
    grant_resource: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Hat der Assistent während des Laufs ausdrücklich gemeldet („der Mensch soll das
    # sehen", Werkzeug `notify_human`)? Nur dann verschickt der Abschluss eine Nachricht —
    # sonst steht das Ergebnis still im Posteingang. Ein erledigtes „nichts zu tun" soll
    # niemanden stören.
    notified: Mapped[bool] = mapped_column(Boolean, default=False)


class AssistantPermission(TimestampMixin, Base):
    """Gelernte Tool-Berechtigung des Assistenten (owner-scoped, projektlos): 'immer erlauben'
    / 'nie' pro Tool(+Ressource). Wird vom Freigabe-Gate gelesen (perms.evaluate). Inhalt
    persönlich → DB-only, nicht im git."""
    __tablename__ = "assistant_permissions"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "tool", "resource", name="uq_assistant_perm"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    tool: Mapped[str] = mapped_column(String(150), default="")     # glob
    resource: Mapped[str] = mapped_column(String(500), default="*")  # glob
    action: Mapped[str] = mapped_column(String(10), default="ask")  # allow | ask | deny


class AssistantPolicy(TimestampMixin, Base):
    """Gelernte Regel des persönlichen Assistenten für eingehende Items (v. a. Mail).

    Owner-scoped, projektlos. Inhalt (Absender, Aktionen …) ist persönlich und bleibt in der DB —
    NICHT im git. Wird per Freigabe „immer …" gefüllt (Inbox/Telegram) oder manuell gepflegt.
    Passt eine Regel auf einen Eingang, kann er automatisch (geschwärzt/ungeschwärzt) laufen und
    bekommt den gelernten Handlungs-Hinweis mit.
    """
    __tablename__ = "assistant_policies"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "match_kind", "match_value", name="uq_assistant_policy_match"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)

    # Worauf die Regel matcht: 'sender' (news@verband.de) · 'domain' (verband.de) · 'category' (rechnung).
    match_kind: Mapped[str] = mapped_column(String(20), default="sender")
    match_value: Mapped[str] = mapped_column(String(300), default="")

    auto_approve: Mapped[bool] = mapped_column(Boolean, default=True)   # überspringt Review
    redaction: Mapped[str] = mapped_column(String(20), default="redacted")  # redacted | unredacted
    action_hint: Mapped[str] = mapped_column(Text, default="")         # gelernte Aktion
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AssistantContact(TimestampMixin, Base):
    """Bekannte Adresse aus dem Obsidian-Vault — die Freispruch-Liste der Spam-Erkennung.

    Kontakte stehen im Vault (`03 Bereiche/Personen|Kontakte|Firmen`), nicht mehr in
    Nextcloud. Der Vault wird nicht pro Mail gelesen, sondern periodisch hierher gespiegelt:
    die Prüfung ist damit ein Index-Lookup und hängt nicht an der Erreichbarkeit einer
    Syncthing-Replik.

    Zeilen sind ein Spiegel, kein Besitz — der Abgleich löscht, was im Vault verschwunden
    ist. Nichts hier ist von Hand gepflegt.
    """
    __tablename__ = "assistant_contacts"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "email", name="uq_assistant_contact"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)

    email: Mapped[str] = mapped_column(String(320), default="", index=True)
    domain: Mapped[str] = mapped_column(String(255), default="", index=True)
    name: Mapped[str] = mapped_column(String(300), default="")
    # Woher die Adresse stammt: Vault-Pfad der Notiz (Nachvollziehbarkeit beim Fehlalarm).
    source_path: Mapped[str] = mapped_column(String(500), default="")
    # 'frontmatter' = ausgewiesenes Adressfeld (verlässlich) · 'body' = im Text gefunden
    # (schwächer: dort steht auch mal die Adresse eines Dritten).
    source_kind: Mapped[str] = mapped_column(String(20), default="frontmatter")


class SpamVerdict(TimestampMixin, Base):
    """Ein Spam-Urteil über eine eingegangene Mail — und was der Mensch dazu gesagt hat.

    Diese Tabelle ist zugleich Arbeitsvorrat (offene Rückfragen an Telegram) und Gedächtnis:
    aus den entschiedenen Zeilen lernt die Erkennung (siehe `SpamFeatureStat`). Deshalb wird
    eine entschiedene Zeile nie gelöscht — sie ist der Lehrstoff.

    Merkmale liegen hier bewusst schon zerlegt vor (`features`), nicht nur als Rohmail:
    gelernt wird über Merkmale, und die müssen später ohne die Originalmail rekonstruierbar
    sein (die wandert in den Spam-Ordner oder wird vom Menschen gelöscht).
    """
    __tablename__ = "spam_verdicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    assistant_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("assistant_tasks.id", ondelete="CASCADE"), nullable=True, index=True)
    # Der Ablauf, der diese Frage gestellt hat. Über ihn schaltet die Antwort aus Telegram
    # den Prozess weiter, statt an ihm vorbei selbst zu verschieben (siehe spam_review).
    # NULL = Altbestand aus der Zeit vor dem Mail-Prozess → direkter Weg.
    workflow_instance_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_instances.id", ondelete="SET NULL"), nullable=True, index=True)

    # Für die spätere IMAP-Aktion (verschieben) — Konto/Ordner/UID der Nachricht.
    account: Mapped[str] = mapped_column(String(120), default="")
    folder: Mapped[str] = mapped_column(String(255), default="")
    uid: Mapped[int | None] = mapped_column(Integer, nullable=True)

    sender_email: Mapped[str] = mapped_column(String(320), default="", index=True)
    sender_domain: Mapped[str] = mapped_column(String(255), default="", index=True)
    # An welchen meiner Aliase ging die Mail? Ein Alias, den nur ein Anbieter kennt und der
    # plötzlich Fremdwerbung bekommt, ist verkauft oder geleakt — das ist ein Signal über
    # den einzelnen Vorgang hinaus.
    recipient: Mapped[str] = mapped_column(String(320), default="", index=True)
    subject: Mapped[str] = mapped_column(String(500), default="")

    # Teilurteile, damit hinterher nachvollziehbar ist, WER falsch lag.
    rule_score: Mapped[float] = mapped_column(Float, default=0.0)
    model_score: Mapped[float] = mapped_column(Float, default=0.0)
    learned_score: Mapped[float] = mapped_column(Float, default=0.0)
    score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    # Zerlegte Merkmale für das Lernen (Liste von Merkmal-Schlüsseln, s. spam_learn).
    features: Mapped[list] = mapped_column(JSON, default=list)

    # pending = wartet auf den Menschen · spam / ham = entschieden · skipped = verfallen
    # (Mail nicht mehr auffindbar o. Ä.).
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    # Kennung der Sammel-Karte, in der dieser Fall abgefragt wurde. Ein Knopf „alle
    # bestätigen" braucht eine benennbare Menge, und die Telegram-Rückmeldung trägt nur
    # 64 Zeichen — eine kurze Kennung passt, eine Liste von Nummern nicht.
    digest_batch: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # Wie entschieden wurde: telegram | web | auto — trennt gelernte Wahrheit von Vermutung.
    decided_by: Mapped[str] = mapped_column(String(20), default="")
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Ergebnis der IMAP-Aktion (verschoben/Fehlermeldung) — rein zur Nachschau.
    action_result: Mapped[str] = mapped_column(Text, default="")


class SpamFeatureStat(TimestampMixin, Base):
    """Gelernte Häufigkeit eines Merkmals in Spam vs. Nicht-Spam.

    Das ist das Gedächtnis der Erkennung: jede Entscheidung des Menschen erhöht hier Zähler,
    und jede *künftige* Mail wird gegen diese Zähler gehalten. Ohne diese Tabelle bliebe die
    Erkennung bei jedem Durchgang gleich schlau — der Mensch würde dieselbe Frage ewig
    beantworten.

    Bewusst Zähler statt eines trainierten Modells: nachvollziehbar (man kann nachsehen,
    warum), sofort wirksam (kein Trainingslauf), korrigierbar (eine falsche Entscheidung
    lässt sich zurückzählen).
    """
    __tablename__ = "spam_feature_stats"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "feature", name="uq_spam_feature"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    # Merkmal als Schlüssel, z. B. 'from:werbung@example.com' · 'dom:example.com' ·
    # 'to:shop-alias@meine-domain.de' · 'sig:spf_fail' · 'wort:gewonnen'.
    feature: Mapped[str] = mapped_column(String(400), default="", index=True)
    spam_count: Mapped[int] = mapped_column(Integer, default=0)
    ham_count: Mapped[int] = mapped_column(Integer, default=0)
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChatSummary(TimestampMixin, Base):
    """Fortgeschriebene Zusammenfassung eines Gesprächsfadens (Mensch ↔ ein Agent).

    Der Verlauf war ein reines Zeitfenster: die letzten Wortwechsel wörtlich, alles davor
    ersatzlos weg. Nach zwölf Stunden wusste der Assistent nichts mehr — nicht allmählich
    schwächer, sondern schlagartig nichts. Jetzt wandern ältere Wortwechsel hierher, in eine
    Zusammenfassung, die mitwächst; sie ersetzt nichts Jüngeres, sondern trägt das Ältere.

    Genau EINE Zeile je (Mensch, Agent) — sie wird fortgeschrieben, nicht vermehrt.
    `bis_task_id` merkt sich, bis wohin sie reicht; alles danach ist noch wörtlich im Verlauf.
    """
    __tablename__ = "chat_summaries"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "agent", name="uq_chat_summary_faden"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    agent: Mapped[str] = mapped_column(String(100), default="assistent")
    bis_task_id: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")
