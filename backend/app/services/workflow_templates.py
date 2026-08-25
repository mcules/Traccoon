"""Ready-made flows to copy.

A fresh flow is a start node and an end node. That is correct, but it does not answer the
question that comes first: how do you build something from it that actually runs? The four
templates here are not examples to look at, they are starting points. They are created as
version 1, they survive a dry run right away, and every node marks the spot where you put
your own case in (tool, destination, recipient).

They cover the four patterns almost every custom flow is made of:

    outside -> check -> report        handle an incoming report
    clock -> fetch -> approve -> act  scheduled check with approval
    fetch -> list -> per item         work through a list
    act -> error -> wait -> retry     call with retry

They live here and not in the database on purpose: a template is a shipped thing like the
default process set, not a user file. Whoever wants to change one creates it and rebuilds
it, and that copy belongs to them.
"""
from __future__ import annotations

from ..models.enums import WorkflowSubjectKind
from .mail_actions import TASK_PARAMS, MAP_PARAMS

_COL, _ROW = 260, 130


def _n(node_id: str, ntype: str, col: int, row: int, config: dict) -> dict:
    return {"id": node_id, "type": ntype,
            "position": {"x": col * _COL, "y": row * _ROW},
            "data": {"config": config}}


def _e(source: str, target: str, handle: str | None = None, label: str = "") -> dict:
    edge = {"id": f"e-{source}-{handle or 'out'}-{target}", "source": source, "target": target}
    if handle:
        edge["sourceHandle"] = handle
    if label:
        edge["label"] = label
    return edge


def _action(name: str, label: str, **cfg) -> dict:
    """auto_action config in the same shape the editor writes."""
    params = {k: v for k, v in cfg.items() if k not in ("wiederholungen", "warte_sek")}
    remainder = {k: v for k, v in cfg.items() if k in ("wiederholungen", "warte_sek")}
    return {"label": label, "action": {"action": name, "params": params}, **remainder}


def _end(node_id: str, col: int, row: int, label: str, outcome: str = "completed") -> dict:
    return _n(node_id, "end", col, row, {"label": label, "outcome": outcome})


# -- 1) incoming report ------------------------------------------------------

def _notice_from_outside() -> dict:
    """Webhook in, decision, message out.

    The sample payload on the start node is more than decoration: the editor builds the
    context fields from it, and those are what the decision offers for selection. Without
    it you face an empty dropdown at the branch.
    """
    nodes = [
        _n("start", "start", 0, 0, {
            "label": "A report from outside",
            "trigger": {"kind": "webhook",
                        "sample": {"title": "Disk almost full", "severity": "high",
                                   "source": "monitoring"}}}),
        _n("weiche", "decision", 0, 1, {
            "label": "Urgent?",
            "branches": [
                {"handle": "dringend", "label": "yes",
                 "guard": {"==": [{"var": "schwere"}, "hoch"]}},
                {"handle": "egal", "label": "no"},
            ],
            "default_handle": "egal"}),
        _n("melden", "auto_action", 0, 2, _action(
            "notify", "Say so",
            title="{{ title }}",
            text="Reported by {{ source | default:external }}: {{ title }}")),
        _end("end_ok", 0, 3, "Reported"),
        _end("end_egal", 1, 2, "Nothing to do"),
    ]
    return {"nodes": nodes, "edges": [
        _e("start", "weiche"),
        _e("weiche", "melden", "dringend", "urgent"),
        _e("weiche", "end_egal", "egal", "can wait"),
        _e("melden", "end_ok"),
    ]}


# -- 2) scheduled check with approval ----------------------------------------

def _check_with_grant() -> dict:
    """Fetch something, look at it, and act only after approval.

    The flow starts by hand or through a job (Settings, Jobs, kind `workflow`), which is
    why it has no trigger event. The approval sits before the effective action on purpose:
    what a machine does alone at night, you do not want to explain in the morning.
    """
    nodes = [
        _n("start", "start", 0, 0, {"label": "Geplanter Lauf"}),
        _n("holen", "auto_action", 0, 1, _action(
            "tool_call", "Daten holen",
            tool="", arguments={}, context_key="tool")),
        _n("auffaellig", "decision", 0, 2, {
            "label": "Anything odd?",
            "branches": [
                {"handle": "ja", "label": "yes", "guard": {"==": [{"var": "tool.ok"}, True]}},
                {"handle": "nein", "label": "no"},
            ],
            "default_handle": "nein"}),
        _n("freigabe", "approval", 0, 3, {
            "label": "Freigabe einholen",
            "gate": "none",
            "reason_required_on_reject": True}),
        _n("handeln", "auto_action", 0, 4, _action(
            "notify", "Handeln",
            title="Approved — carried out",
            text="Ergebnis: {{ tool.text | truncate:300 }}")),
        _end("end_ok", 0, 5, "Erledigt"),
        _end("end_nein", 1, 3, "Nothing odd"),
        _end("end_abgelehnt", 2, 4, "Abgelehnt"),
    ]
    return {"nodes": nodes, "edges": [
        _e("start", "holen"),
        _e("holen", "auffaellig"),
        _e("auffaellig", "freigabe", "ja", "odd"),
        _e("auffaellig", "end_nein", "nein", "all quiet"),
        _e("freigabe", "handeln", "approved", "freigegeben"),
        _e("freigabe", "end_abgelehnt", "rejected", "abgelehnt"),
        _e("handeln", "end_ok"),
    ]}


# -- 3) Liste abarbeiten --------------------

def _listing_process() -> dict:
    """Fetch a list and do something with it item by item.

    The edge from the body back to the loop is the whole trick: it turns a straight flow
    into a pass. `liste` points at the context path the previous step filled, and what
    happens per item sits in the body.
    """
    nodes = [
        _n("start", "start", 0, 0, {
            "label": "Start",
            "trigger": {"kind": "webhook",
                        "sample": {"posten": [{"name": "A"}, {"name": "B"}]}}}),
        _n("holen", "auto_action", 0, 1, _action(
            "tool_call", "Liste holen",
            tool="", arguments={}, context_key="tool")),
        _n("schleife", "loop", 0, 2, {
            "label": "For every element",
            "liste": "posten", "element": "element", "index": "i",
            "sammle": "schritt.action", "ergebnisse": "ergebnisse"}),
        _n("schritt", "auto_action", 1, 3, _action(
            "notify", "Element verarbeiten",
            title="Element {{ i }}",
            text="{{ element }}")),
        _n("bericht", "auto_action", 0, 4, _action(
            "notify", "Zusammenfassung",
            title="Durchlauf fertig",
            text="{{ ergebnisse | count }} Elemente verarbeitet")),
        _end("end_ok", 0, 5, "Done"),
    ]
    return {"nodes": nodes, "edges": [
        _e("start", "holen"),
        _e("holen", "schleife"),
        _e("schleife", "schritt", "element", "je Element"),
        _e("schritt", "schleife", None, "next"),
        _e("schleife", "bericht", "fertig", "durch"),
        _e("bericht", "end_ok"),
    ]}


# -- 4) Call with a retry ---------------------------

def _call_with_repeat() -> dict:
    """Call outside, and do not give up at the first trouble.

    Two nets on top of each other: the node retries three times on its own (with a delay,
    because retrying at once means the same second and the same error), and only when that
    does not help either does it continue through the red outlet. There it waits and tries
    one last time before anyone gets told.
    """
    nodes = [
        _n("start", "start", 0, 0, {"label": "Start"}),
        _n("rufen", "auto_action", 0, 1, _action(
            "http_request", "Ziel aufrufen",
            destination="", method="POST", path="/", fail_on_error=True,
            repeats=3, wait_sec=60)),
        _end("end_ok", 0, 2, "Durch"),
        _n("warten", "timer", 1, 2, {"label": "Later again", "dauer": 30, "einheit": "m"}),
        _n("nochmal", "auto_action", 1, 3, _action(
            "http_request", "Letzter Versuch",
            destination="", method="POST", path="/", fail_on_error=True)),
        _n("aufgeben", "auto_action", 2, 4, _action(
            "notify", "Say so",
            title="The call failed for good",
            text="The straggler did not get through either.")),
        _end("end_fail", 2, 5, "Fehlgeschlagen", outcome="failed"),
    ]
    return {"nodes": nodes, "edges": [
        _e("start", "rufen"),
        _e("rufen", "end_ok", None, "durch"),
        _e("rufen", "warten", "error", "Fehler"),
        _e("warten", "nochmal"),
        _e("nochmal", "end_ok", None, "after all"),
        _e("nochmal", "aufgeben", "error", "not that either"),
        _e("aufgeben", "end_fail"),
    ]}


def _mail_intake() -> dict:
    """What happens to an incoming mail, previously fixed in `mail_intake.py`.

    Triggered by the event `mail.received` (the mail webhook reports it). The context brings
    `mail` (raw payload of the watcher) and `eingang` (settings of the trigger); everything
    else is written by the steps themselves (see `services/mail_actions.py`).

    The order of the branches is the guard rail of the detection:

        detection off        → let through (the emergency stop goes before everything)
        known sender         → let through (acquittal from the contact list)
        learned: spam        → report and move (an error has to be noticeable)
        certain enough       → move, the card carries the way back (off by default);
                               takes hold above the auto threshold or on the server verdict
        learned: wanted      → let through without asking again
        suspicion            → ask and wait; only the answer moves the mail
                               (both spam paths end in the same execution node)
        inconspicuous        → assistant item, as with every normal mail

    The forgery suspicion is already accounted for in the verdict: it lifts both the
    acquittal of the contact list and that of the memory (`spam_review.beurteilen`). A known
    name is the rewarding target, and exactly there the whitelist must not take hold.

    Reporting is a step of its own, not a side effect of the verdict: `spam_card` writes the
    verdict and the text, a notify node sends it, and a branch in front of it decides whether
    it is sent at all (setting `spam_auto_melden`). Whoever does not want to read about every
    automatically cleared mail therefore keeps the sorting, the learning and the overview —
    only the phone stays quiet.

    "Not spam" deliberately leads NOT into nothing but into the assistant branch: a mail
    that was suspected wrongly should be workable completely normally afterwards. Before, it
    stayed lying around as an item without a card.
    """
    nodes = [
        _n("start", "start", 0, 0, {
            "label": "Mail eingegangen",
            "trigger": {"event": "mail.received"},
        }),
        _n("classify", "auto_action", 0, 1,
           _action("mail_classify", "Mail einordnen")),
        _n("evaluate", "auto_action", 0, 2,
           _action("spam_evaluate", "Spam beurteilen")),
        # Every reason gets its own exit although four of them lead to the same step: the
        # history of an instance should show WHY a mail was let through ("sender known" is
        # something other than "inconspicuous"). Two branches with the same exit name would
        # moreover be two exits with the same identifier on the same node, and which edge
        # hangs off it would be a matter of chance.
        _n("weiche", "decision", 0, 3, {
            "label": "Spam?",
            "branches": [
                {"handle": "aus", "label": "detection off",
                 "guard": {"==": [{"var": "spam.aktiv"}, False]}},
                {"handle": "kontakt", "label": "bekannter Absender",
                 "guard": {"==": [{"var": "spam.bekannter_kontakt"}, True]}},
                {"handle": "geklaert_spam", "label": "gelernt: Spam", "guard": {"and": [
                    {"==": [{"var": "spam.settled"}, True]},
                    {"==": [{"var": "spam.settled_verdict"}, "spam"]},
                ]}},
                {"handle": "geklaert_ham", "label": "learned: wanted",
                 "guard": {"==": [{"var": "spam.settled"}, True]}},
                # Stands BEFORE the question: what takes hold here is no longer asked about
                # but moved, with a way back on the card. Two paths there: the score above
                # the auto threshold OR the verdict of one's own mail server. The latter
                # because it goes under in the weighted mixture: 13 spam points from the own
                # server give an overall verdict of ~0.55, and with that every sensible auto
                # threshold would be out of reach. The bracket `auto_ab <= 1` keeps the whole
                # branch out as long as nobody has deliberately set the threshold.
                {"handle": "auto", "label": "sicher genug (Auto)", "guard": {"and": [
                    {"<=": [{"var": "spam.auto_ab"}, 1]},
                    {"or": [
                        {">=": [{"var": "spam.score"}, {"var": "spam.auto_ab"}]},
                        {"==": [{"var": "spam.serverurteil"}, True]},
                    ]},
                ]}},
                {"handle": "frage", "label": "Verdacht",
                 "guard": {">=": [{"var": "spam.score"}, {"var": "spam.frage_ab"}]}},
                {"handle": "sauber", "label": "unremarkable"},
            ],
            "default_handle": "sauber",
        }),

        # ── Settled case: move, but report ───────────────────────────────────
        # Two steps, deliberately: the verdict comes into being here, the message goes out
        # one node further. Whoever does not want to read about every automatically cleared
        # mail switches the message off — and the sorting keeps working, because the verdict
        # is what the moving and the learning hang off.
        _n("karte_gelernt", "auto_action", 3, 4,
           _action("spam_card", "Gelernten Fall festhalten", predecided=True, report=False)),
        _n("melden_gelernt_frage", "decision", 4, 4, {
            "label": "Gelernten Fall melden?",
            "branches": [{"handle": "still", "label": "reporting is off",
                          "guard": {"==": [{"var": "spam.auto_melden"}, False]}},
                         {"handle": "melden", "label": "melden"}],
            "default_handle": "melden",
        }),
        _n("melde_gelernt", "auto_action", 5, 4, _action(
            "notify", "Gelernten Fall melden",
            to={"mode": "context", "path": "intake.owner_id"},
            title="{{ spam.card_title }}", text="{{ spam.card_text }}",
            kind="{{ spam.card_kind }}", ref={"spam_verdict_id": "{{ spam.verdict_id }}"})),

        # ── Sicher genug: verschieben, aber widersprechlich ─────────────────
        _n("karte_auto", "auto_action", 3, 5,
           _action("spam_card", "Aussortierung festhalten", recoverable=True, report=False)),
        _n("melden_auto_frage", "decision", 4, 5, {
            "label": "Aussortierung melden?",
            "branches": [{"handle": "still", "label": "reporting is off",
                          "guard": {"==": [{"var": "spam.auto_melden"}, False]}},
                         {"handle": "melden", "label": "melden"}],
            "default_handle": "melden",
        }),
        _n("melde_auto", "auto_action", 5, 5, _action(
            "notify", "Aussortierung melden",
            to={"mode": "context", "path": "intake.owner_id"},
            title="{{ spam.card_title }}", text="{{ spam.card_text }}",
            kind="{{ spam.card_kind }}", ref={"spam_verdict_id": "{{ spam.verdict_id }}"})),

        # ── Suspicion: ask, wait, execute ────────────────────────────────────
        _n("karte", "auto_action", 1, 4, _action("spam_card", "Ask about it")),
        _n("rueckfrage", "approval", 1, 5, {
            "label": "Is this spam?",
            "instructions": "Approving moves the mail into the spam folder. "
                            "Refusing notes the sender as wanted.",
            "assignee": {"mode": "context", "path": "intake.owner_id"},
            # The question has already been asked, as a card with buttons (the step before
            # respectively the digest card of the scheduler beat). A second report would be noise.
            "notify": False,
        }),
        # ONE execution node for both spam paths: the answer of the human and the verdict of
        # the memory end in the same action. `decided_by` deliberately stays empty; the
        # action takes it from the context (`telegram` when a human answered, otherwise
        # `auto`). With a fixed value here the memory would book every human decision as
        # automatic.
        _n("weg", "auto_action", 2, 6,
           _action("spam_apply", "Into the spam folder", decision="spam")),
        # What the mail was recognised by belongs in the knowledge, not only in the log. The
        # path carries the kind, so a new kind (invoice fraud, sextortion, whatever comes)
        # writes its own note without anybody touching the flow.
        _n("notiz", "auto_action", 2, 7, _action(
            "note_append", "Erkenntnis notieren",
            path="04 Wissen/Erkennung/{{ spam.art }}.md",
            text="- {{ spam.sender_domain }} · {{ spam.subject }}: {{ spam.findings_text }}")),
        _n("end_spam", "end", 2, 8, {"label": "Cleared away as spam", "outcome": "completed"}),
        # Stands on the way back to the middle: from here the mail runs into the assistant.
        _n("kein_spam", "auto_action", 1, 7,
           _action("spam_apply", "Absender merken", decision="ham")),

        # ── Normaler Weg: Assistent ──────────────────────────────────────────
        # No mail special path any more: the intake is created with the general node and
        # starts itself when a learned rule releases it. What makes the mail case are its
        # values — they stand in `mail_actions.ASSIGNMENT_PARAMS` so that the template and the
        # legacy names do not drift apart.
        _n("item", "auto_action", 0, 8,
           _action("assistant_task", "Assistent-Eingang anlegen", **TASK_PARAMS)),
        _n("ist_auto", "decision", 0, 9, {
            "label": "Automatisch freigegeben?",
            "branches": [
                {"handle": "auto", "label": "gelernte Regel",
                 "guard": {"==": [{"var": "policy.auto"}, True]}},
                {"handle": "fragen", "label": "Freigabe einholen"},
            ],
            "default_handle": "fragen",
        }),
        _n("freigabe_karte", "auto_action", 1, 10,
           _action("notify", "Freigabekarte schicken", **MAP_PARAMS)),
        _n("end_item", "end", 0, 11, {"label": "Handed over", "outcome": "completed"}),
    ]

    edges = [
        _e("start", "classify"),
        _e("classify", "evaluate"),
        _e("evaluate", "weiche"),

        _e("weiche", "karte_gelernt", "geklaert_spam"),
        _e("karte_gelernt", "melden_gelernt_frage"),
        _e("melden_gelernt_frage", "melde_gelernt", "melden"),
        _e("melden_gelernt_frage", "weg", "still", "without a message"),
        _e("melde_gelernt", "weg"),
        _e("weiche", "karte_auto", "auto"),
        _e("karte_auto", "melden_auto_frage"),
        _e("melden_auto_frage", "melde_auto", "melden"),
        _e("melden_auto_frage", "weg", "still", "without a message"),
        _e("melde_auto", "weg"),

        _e("weiche", "karte", "frage"),
        _e("karte", "rueckfrage"),
        _e("rueckfrage", "weg", "approved", "is spam"),
        _e("rueckfrage", "kein_spam", "rejected", "not spam"),
        _e("weg", "notiz"),
        _e("notiz", "end_spam"),
        # Suspected wrongly: the sender is remembered, the mail goes its normal way.
        _e("kein_spam", "item"),

        # Four paths, one target: the assistant handles the mail like any other.
        _e("weiche", "item", "sauber"),
        _e("weiche", "item", "aus", "detection off"),
        _e("weiche", "item", "kontakt", "bekannt"),
        _e("weiche", "item", "geklaert_ham", "learned: wanted"),
        _e("item", "ist_auto"),
        # The released path needs no step any more: it is already running.
        _e("ist_auto", "end_item", "auto", "already running"),
        _e("ist_auto", "freigabe_karte", "fragen"),
        _e("freigabe_karte", "end_item"),
    ]
    return {"nodes": nodes, "edges": edges}


def _attachment_to_paperless() -> dict:
    """A button on every attachment: an invoice into the archive, without a detour via the machine.

    It shows the whole path of a mail action (fetch the attachment, call the tool, say
    something) and is at the same time the answer to how one builds further buttons like it.
    """
    nodes = [
        _n("start", "start", 0, 0, {
            "label": "A button on the attachment",
            "trigger": {"kind": "mail_action", "scope": "attachment"},
        }),
        _n("holen", "auto_action", 0, 1,
           _action("mail_attachment", "Fetch the attachment", context_key="attachment")),
        _n("ablegen", "auto_action", 0, 2, _action(
            "tool_call", "File it in the archive",
            tool="paperless__post_document",
            arguments={"file": "{{ attachment.base64 }}",
                       "filename": "{{ attachment.filename }}",
                       "title": "{{ mail.subject }}"},
            context_key="paperless")),
        _n("melden", "auto_action", 0, 3, _action(
            "notify", "Say so",
            to={"mode": "context", "path": "mail.owner_id"},
            title="📄 {{ attachment.filename }} is in the archive",
            text="From the mail \"{{ mail.subject }}\" by {{ mail.from }}.")),
        _n("fertig", "end", 0, 4, {"label": "Filed", "outcome": "completed"}),
    ]
    edges = [_e("start", "holen"), _e("holen", "ablegen"), _e("ablegen", "melden"),
             _e("melden", "fertig")]
    return {"nodes": nodes, "edges": edges}


# -- 5) what used to be webhook modes ----------------------------------------
# A webhook could once create a ticket itself, send a message or assign the assistant — every
# way with columns of its own on the webhook and available only there. The same work is done
# today by nodes EVERY flow can use; these three templates are the short way there and at the
# same time what `webhook_modes` converts existing ones onto.

def _webhook_assistant() -> dict:
    """A trigger from outside, and the assistant works with it."""
    nodes = [
        _n("start", "start", 0, 0, {
            "label": "A trigger from outside",
            "trigger": {"kind": "webhook",
                        "sample": {"title": "Battery low", "source": "monitoring"}}}),
        _n("auftrag", "auto_action", 0, 1, _action(
            "assistant_task", "Assistent beauftragen",
            agent="assistent",
            title="{{ title | default:Assignment from outside }}",
            task="Take care of this report:\n\n{{ title }}\n\nSource: "
                    "{{ source | default:Webhook }}",
            approval=False)),
        _end("fertig", 0, 2, "Beauftragt"),
    ]
    return {"nodes": nodes, "edges": [_e("start", "auftrag"), _e("auftrag", "fertig")]}


def _webhook_report() -> dict:
    """A trigger from outside, and a message arrives."""
    nodes = [
        _n("start", "start", 0, 0, {
            "label": "A trigger from outside",
            "trigger": {"kind": "webhook", "sample": {"message": "Something happened"}}}),
        _n("melden", "auto_action", 0, 1, _action(
            "notify", "Say so",
            title="{{ title | default:Report }}",
            text="{{ message }}")),
        _end("fertig", 0, 2, "Gemeldet"),
    ]
    return {"nodes": nodes, "edges": [_e("start", "melden"), _e("melden", "fertig")]}


def _webhook_ticket() -> dict:
    """A trigger from outside, and a ticket comes of it."""
    nodes = [
        _n("start", "start", 0, 0, {
            "label": "A trigger from outside",
            "trigger": {"kind": "webhook",
                        "sample": {"title": "A request from outside", "body": "The text for it"}}}),
        _n("ticket", "auto_action", 0, 1, _action(
            "create_ticket", "Ticket anlegen",
            summary="{{ title }}", description="{{ body }}")),
        _end("fertig", 0, 2, "Angelegt"),
    ]
    return {"nodes": nodes, "edges": [_e("start", "ticket"), _e("ticket", "fertig")]}


TEMPLATES: list[dict] = [
    {"key": "report-from-outside",
     "name": "Process a report from outside",
     "description": "A webhook comes in, a decision by urgency, a message goes out.",
     "subject_kind": WorkflowSubjectKind.standalone,
     "hinweis": "Adjust the example payload on the start node — the context fields come out of it.",
     "build": _notice_from_outside},
    {"key": "check-with-approval",
     "name": "Scheduled check with approval",
     "description": "Fetch data, look at it, and act only after an approval.",
     "subject_kind": WorkflowSubjectKind.standalone,
     "hinweis": "Choose the tool in the step \"Fetch data\"; start it from a job.",
     "build": _check_with_grant},
    {"key": "process-a-list",
     "name": "Work through a list element by element",
     "description": "Fetch a list, walk it, do something per element, report at the end.",
     "subject_kind": WorkflowSubjectKind.standalone,
     "hinweis": "Enter the path to the list in the loop (tool.json.items, say).",
     "build": _listing_process},
    {"key": "attachment-to-paperless",
     "name": "Attachment to Paperless",
     "description": "A button on every attachment: fetch it, file it, say so.",
     "subject_kind": WorkflowSubjectKind.standalone,
     "hinweis": "Appears in the mailbox on every attachment. The tool paperless__post_document "
                "has to be released to the flow; adjust title and tags in the step "
                "\"File in Paperless\".",
     "build": _attachment_to_paperless},
    {"key": "mail-intake",
     "name": "Mail intake",
     "description": "Classify an incoming mail, check it for spam and either clear it away "
                    "or give it to the assistant.",
     "subject_kind": WorkflowSubjectKind.standalone,
     "hinweis": "Listens for the event `mail.received`. Only ONE flow should do that, "
                "otherwise every mail runs several times. Thresholds and switches stand in the "
                "settings (spam_*), the texts in the report node.",
     "build": _mail_intake},
    {"key": "trigger-to-assistant",
     "name": "Trigger → assistant",
     "description": "Something comes from outside, the assistant takes care of it.",
     "subject_kind": WorkflowSubjectKind.standalone,
     "hinweis": "Adjust the assignment text in the node \"Assign the assistant\" to your own "
                "payload ({{ field }}). \"Have it approved first\" on: the assignment waits in "
                "the inbox instead of running at once.",
     "build": _webhook_assistant},
    {"key": "trigger-to-message",
     "name": "Trigger → message",
     "description": "Something comes from outside, and a message arrives.",
     "subject_kind": WorkflowSubjectKind.standalone,
     "hinweis": "Set the recipient and the text in the report node; without a recipient the "
                "message goes to the owner of the flow.",
     "build": _webhook_report},
    {"key": "trigger-to-ticket",
     "name": "Trigger → ticket",
     "description": "Something comes from outside and becomes a ticket.",
     "subject_kind": WorkflowSubjectKind.standalone,
     "hinweis": "Enter the target project in the node \"Create a ticket\"; an agent there sets "
                "the ticket lifecycle going right away.",
     "build": _webhook_ticket},
    {"key": "call-with-retry",
     "name": "Call with retry",
     "description": "Call a destination, retry on an error, once more later, then report.",
     "subject_kind": WorkflowSubjectKind.standalone,
     "hinweis": "Enter the destination (Settings → Destinations) — the base URL and the login sit there.",
     "build": _call_with_repeat},
]

# The German keys of the templates until 2026-08. They stand in flows that were created from
# them and in the code that converted webhooks; resolving both keeps old callers working.
LEGACY_KEYS = {
    "meldung-von-aussen": "report-from-outside",
    "pruefung-mit-freigabe": "check-with-approval",
    "liste-abarbeiten": "process-a-list",
    "anhang-paperless": "attachment-to-paperless",
    "mail-eingang": "mail-intake",
    "webhook-assistent": "trigger-to-assistant",
    "webhook-melden": "trigger-to-message",
    "webhook-ticket": "trigger-to-ticket",
    "aufruf-mit-wiederholung": "call-with-retry",
}

_BY_KEY = {v["key"]: v for v in TEMPLATES}


def _resolve(key: str) -> str:
    """The English key of a template, whatever it was called when the caller learned it."""
    return key if key in _BY_KEY else LEGACY_KEYS.get(key, key)


def listing() -> list[dict]:
    """What is on offer, without the graphs themselves (the overview does not need them)."""
    return [{k: (v.value if hasattr(v, "value") else v)
             for k, v in template.items() if k != "build"}
            for template in TEMPLATES]


def graph(key: str) -> dict | None:
    """The graph of a template, built fresh so nobody shares it by accident.

    Stamped with the current vocabulary: a template is written in today's words, and without
    the mark the one-off terms conversion would read it as an old graph on the next boot.
    """
    from .workflow_terms import stamp

    template = _BY_KEY.get(_resolve(key))
    return stamp(template["build"]()) if template else None


def template(key: str) -> dict | None:
    return _BY_KEY.get(_resolve(key))


async def free_key(db, wish: str, project_id: int | None = None) -> str:
    """A key that does not exist here yet — made out of a name.

    Names and keys describe the matter, not its trigger: `ki-tech-news`, not `job-3`. Whoever
    gives the same name twice gets a number appended instead of a unique constraint error.
    """
    from sqlalchemy import select

    from ..core.slug import slug
    from ..models.workflow import WorkflowDefinition

    basis = slug(wish, 50) or "ablauf"
    condition = (WorkflowDefinition.project_id.is_(None) if project_id is None
                 else WorkflowDefinition.project_id == project_id)
    grant = set((await db.execute(select(WorkflowDefinition.key).where(
        condition))).scalars().all())
    if basis not in grant:
        return basis
    for n in range(2, 100):
        candidate = f"{basis}-{n}"
        if candidate not in grant:
            return candidate
    return f"{basis}-{len(grant)}"


async def create(db, key: str, *, owner_id: int | None, def_key: str = "",
                  name: str = "", published: bool = True, graph: dict | None = None,
                  project_id: int | None = None):
    """Create a flow FROM a template — as somebody's own, not as a shipped one.

    The difference matters: what stands in a set is maintained by Traccoon and overwritten on
    every start (`ensure_builtin_set`). What comes out of here belongs to the person who
    created it, including every change they make to it afterwards.
    """
    import datetime as dt

    from ..models.enums import WorkflowVersionStatus
    from ..models.workflow import WorkflowDefinition, WorkflowVersion

    v = _BY_KEY.get(_resolve(key))
    if v is None:
        raise ValueError(f"Unbekannte Vorlage '{key}'")
    d = WorkflowDefinition(
        project_id=project_id, key=def_key or key, name=name or v["name"],
        description=v["description"], subject_kind=v["subject_kind"],
        enabled=True, created_by=owner_id)
    db.add(d)
    await db.flush()
    version = WorkflowVersion(
        # `graph` lets the conversion fill the template with the values of the old webhook
        # (agent, assignment text, recipient) instead of patching it afterwards.
        definition_id=d.id, version=1, graph=graph or v["build"](), created_by=owner_id,
        status=(WorkflowVersionStatus.published if published
                else WorkflowVersionStatus.draft),
        published_at=dt.datetime.now(tz=dt.timezone.utc) if published else None,
        notes=f"From the template \"{v['name']}\"")
    db.add(version)
    await db.flush()
    if published:
        d.current_version_id = version.id
    return d
