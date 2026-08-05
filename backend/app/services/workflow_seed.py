"""Der ausgelieferte Standard-Satz: das bisherige Verhalten, gezeichnet als Graph.

Diese Graphen sind die *Startaufstellung*, nicht die Wahrheit — jeder Nutzer darf sie
kopieren und umbauen. Sie bilden 1:1 ab, was vorher fest im Dispatcher, in `lifecycle.py`
und im Beschaffungsmodul verdrahtet war:

    Planung → Plan-Freigabe → Umsetzung → (Abnahme) → Merge/Deploy,
    Fortsetzung mit Feststecker-Erkennung, Rückfragen als Wartepunkt am Kommentar,
    Aufteilung in Teilaufgaben, Beschaffungskette, Ticket-Eingang.

`ensure_builtin_set` läuft beim Backend-Start und ist idempotent: nur wenn sich ein Graph
tatsächlich geändert hat, wird eine neue Version veröffentlicht. Laufende Instanzen bleiben
auf ihrer Version gepinnt.
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.enums import (
    WorkflowSetScope, WorkflowSlot, WorkflowSubjectKind, WorkflowVersionStatus,
)
from ..models.workflow import WorkflowDefinition, WorkflowSet, WorkflowVersion
from .workflow_sets import BUILTIN_SET_KEY, SLOT_META

log = logging.getLogger("workflow_seed")

# Steigt, sobald sich ein ausgelieferter Graph ändert (nur zur Nachvollziehbarkeit —
# veröffentlicht wird ohnehin nur bei echtem Graph-Unterschied).
BUILTIN_REVISION = 7

_COL = 260   # Spaltenabstand für Zweige
_ROW = 130   # Zeilenabstand


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


def _action(name: str, label: str, **params) -> dict:
    """auto_action-Konfiguration in der Form, die auch der Editor schreibt."""
    return {"label": label, "action": {"action": name, "params": params}}


# ── Slot: KI-Ticket-Lebenszyklus ─────────────────────────────────────────────

def build_ticket_lifecycle() -> dict:
    """Der Ablauf, der bisher im Dispatcher steckte.

    Drei Einstiege über `context.entry`: `plan` (Normalfall), `exec` (Teilaufgabe mit
    fertigem Plan) und `accept` (Sammelticket, dessen Teile alle fertig sind).

    Bewusst knapp gehalten: je Phase EIN Wartepunkt für alle Störungen (Rückfrage, Fehler,
    Feststecker, abgelehnte Freigabe) statt eines Paares je Fall. Der Zustand, den das
    Ticket dabei annimmt, kommt aus dem Lauf selbst (`agent.hold_status`/`hold_reason`) —
    dieselben Meldungen wie vorher, nur ohne fünf fast gleiche Knotenpaare. Jeder Knoten
    trägt seine `group`, damit die Oberfläche Phasen als Bänder zeigen kann.
    """
    def _p(node_id, ntype, col, row, config, group):
        return _n(node_id, ntype, col, row, {**config, "group": group})

    nodes = [
        _p("start", "start", 0, 0, {"label": "Ticket zugewiesen"}, "start"),
        _p("entry", "decision", 0, 1, {
            "label": "Einstieg",
            "branches": [
                {"handle": "exec", "label": "Plan liegt vor",
                 "guard": {"==": [{"var": "entry"}, "exec"]}},
                {"handle": "accept", "label": "nur Abnahme",
                 "guard": {"==": [{"var": "entry"}, "accept"]}},
                {"handle": "plan", "label": "planen"},     # ohne Bedingung = Auffang
            ],
            "default_handle": "plan",
        }, "start"),

        # ── Planung ──────────────────────────────────────────────────────────
        _p("st_planning", "auto_action", 0, 2,
           _action("set_status", "Status: Planung", status="planning"), "planung"),
        _p("plan", "agent_task", 0, 3, {
            "label": "Planung durch den Architekten",
            "agent_role": "plan_agent", "phase": "planning",
        }, "planung"),
        _p("st_plan_review", "auto_action", 0, 4,
           _action("set_status", "Status: Plan-Freigabe", status="plan_review",
                   reason="{{agent.hold_hint}}"), "planung"),
        _p("approve_plan", "approval", 0, 5, {
            "label": "Plan freigeben", "gate": "ai_assign",
            "instructions": "Plan prüfen und freigeben — danach arbeitet der Agent los.",
        }, "planung"),
        _p("is_split", "decision", 0, 6, {
            "label": "Aufteilung vorgeschlagen?",
            "branches": [
                {"handle": "split", "label": "Teilaufgaben",
                 "guard": {"==": [{"var": "agent.has_subtickets"}, True]}},
                {"handle": "single", "label": "am Stück"},
            ],
            "default_handle": "single",
        }, "planung"),
        _p("do_split", "auto_action", -1, 7,
           _action("split_tickets", "Teilaufgaben anlegen"), "aufteilung"),
        _p("end_split", "end", -1, 8, {"label": "Aufgeteilt", "outcome": "completed"},
           "aufteilung"),

        # Störungen der Planung: ein Zustand, ein Wartepunkt.
        _p("st_plan_stop", "auto_action", -2, 4,
           _action("set_status", "Planung angehalten",
                   status="{{agent.hold_status}}", reason="{{agent.hold_reason}}"),
           "stoerung"),
        _p("wait_plan", "wait_event", -2, 5, {
            "label": "Rückmeldung zur Planung", "events": ["comment", "manual", "answer"],
        }, "stoerung"),

        # ── Umsetzung ────────────────────────────────────────────────────────
        _p("cap_baseline", "auto_action", 0, 7,
           _action("set_cap_baseline", "Kostenfenster zurücksetzen"), "umsetzung"),
        # Einmal je Freigabe-Runde gelesen — beide Ausgänge der Umsetzung greifen darauf zu.
        _p("facts", "auto_action", 0, 8,
           _action("refresh_facts", "Projekt-Einstellungen lesen"), "umsetzung"),
        # Sitzt bewusst direkt hinter der Freigabe und NICHT vor `exec`: dorthin führt auch
        # der Abnahme-Zweig (`entry --abnehmen--> facts`), und ein abgenommenes Ticket darf
        # nicht wieder auf „freigegeben" zurückfallen.
        #
        # Zwischen Freigabe und Start kann Zeit vergehen — der Lauf wartet am Torwächter
        # (Nutzer-Limit, Nachtfenster, Cap). Ohne diesen Knoten bliebe das Ticket derweil auf
        # `plan_review` stehen: die Oberfläche böte weiter „Plan freigeben" an, und der
        # Endpunkt antwortete zu Recht „es wartet gerade keine Freigabe". Auf `in_progress`
        # schaltet erst der tatsächliche Start (`workflow_engine._start_agent_task`).
        _p("st_approved", "auto_action", 0, 7,
           _action("set_status", "Status: freigegeben", status="approved"), "umsetzung"),
        _p("exec", "agent_task", 0, 9, {
            "label": "Umsetzung", "agent_role": "exec_agent", "phase": "execution",
        }, "umsetzung"),
        _p("may_continue", "decision", 1, 10, {
            "label": "Weiterarbeiten?",
            "branches": [
                {"handle": "stop", "label": "anhalten", "guard": {"or": [
                    {"==": [{"var": "agent.stalled"}, True]},
                    {"==": [{"var": "project.auto_continue"}, False]},
                    {">=": [{"var": "continuation"}, 30]},
                ]}},
                {"handle": "continue", "label": "weiter"},
            ],
            "default_handle": "continue",
        }, "umsetzung"),

        # Störungen der Umsetzung: ein Zustand, ein Wartepunkt.
        _p("st_exec_stop", "auto_action", 2, 10,
           _action("set_status", "Umsetzung angehalten",
                   status="{{agent.hold_status}}", reason="{{agent.hold_reason}}"),
           "stoerung"),
        _p("wait_exec", "wait_event", 2, 11, {
            "label": "Rückmeldung zur Umsetzung", "events": ["comment", "manual", "answer"],
        }, "stoerung"),

        # ── Abnahme ──────────────────────────────────────────────────────────
        _p("needs_test", "decision", 0, 10, {
            "label": "Abnahme nötig?",
            "branches": [
                {"handle": "review", "label": "ja",
                 "guard": {"==": [{"var": "project.needs_acceptance"}, True]}},
                {"handle": "direct", "label": "nein, direkt liefern"},
            ],
            "default_handle": "direct",
        }, "abnahme"),
        _p("st_to_test", "auto_action", 0, 11,
           _action("set_status", "Status: zu testen", status="to_test"), "abnahme"),
        _p("testenv", "auto_action", 0, 12,
           _action("start_testenv", "Testumgebung starten"), "abnahme"),
        _p("approve_result", "approval", 0, 13, {
            "label": "Abnahme", "gate": "ai_assign",
            "instructions": "Ergebnis prüfen. Freigabe merged den Branch bzw. öffnet den PR.",
        }, "abnahme"),
        _p("accept", "subflow", 0, 14, {
            "label": "Abnahme & Auslieferung", "slot": WorkflowSlot.acceptance.value,
        }, "abnahme"),
        _p("st_done", "auto_action", 0, 15,
           _action("set_status", "Status: fertig", status="done"), "abnahme"),
        _p("end_ok", "end", 0, 16, {"label": "Fertig", "outcome": "completed"}, "abnahme"),
        _p("st_merge_hold", "auto_action", 2, 16,
           _action("set_status", "Merge blockiert", status="hold",
                   reason="merge"), "stoerung"),
        _p("merge_escalate", "decision", 1, 15, {
            "label": "Konflikt eskalieren?",
            "branches": [
                {"handle": "human", "label": "an den Menschen",
                 "guard": {"==": [{"var": "merge.escalate"}, True]}},
                {"handle": "retry", "label": "Agent löst auf"},
            ],
            "default_handle": "retry",
        }, "abnahme"),
    ]

    edges = [
        _e("start", "entry"),
        _e("entry", "st_planning", "plan", "planen"),
        _e("entry", "exec", "exec", "umsetzen"),
        _e("entry", "facts", "accept", "abnehmen"),

        _e("st_planning", "plan"),
        _e("plan", "st_plan_review", "planned", "Plan da"),
        _e("plan", "st_planning", "loop_exhausted", "weiter planen"),
        _e("plan", "st_plan_stop", "blocked", "Rückfrage"),
        _e("plan", "st_plan_stop", "failed", "Fehler"),
        # Auffangnetz für unbekannte Lauf-Ergebnisse (Standard-Abbildung → „err").
        _e("plan", "st_plan_stop", "err"),
        _e("st_plan_review", "approve_plan"),
        _e("approve_plan", "is_split", "approved", "freigegeben"),
        _e("approve_plan", "st_plan_stop", "rejected", "abgelehnt"),
        _e("is_split", "do_split", "split"),
        _e("is_split", "st_approved", "single"),
        _e("st_approved", "cap_baseline"),
        _e("do_split", "end_split"),
        _e("st_plan_stop", "wait_plan"),
        _e("wait_plan", "st_planning"),

        _e("cap_baseline", "facts"),
        _e("facts", "exec"),
        _e("exec", "needs_test", "done", "fertig"),
        _e("exec", "may_continue", "loop_exhausted", "Limit erreicht"),
        _e("exec", "st_exec_stop", "blocked", "Rückfrage"),
        _e("exec", "st_exec_stop", "failed", "Fehler"),
        _e("exec", "st_exec_stop", "err"),
        _e("may_continue", "exec", "continue", "weiter"),
        _e("may_continue", "st_exec_stop", "stop", "anhalten"),
        _e("st_exec_stop", "wait_exec"),
        _e("wait_exec", "exec"),

        _e("needs_test", "st_to_test", "review"),
        _e("needs_test", "accept", "direct"),
        _e("st_to_test", "testenv"),
        _e("testenv", "approve_result"),
        _e("approve_result", "accept", "approved", "abnehmen"),
        _e("approve_result", "st_exec_stop", "rejected", "nachbessern"),
        _e("accept", "st_done", "completed", "geliefert"),
        _e("accept", "merge_escalate", "failed", "Merge offen"),
        _e("st_done", "end_ok"),
        _e("merge_escalate", "st_merge_hold", "human"),
        _e("st_merge_hold", "wait_exec"),
        _e("merge_escalate", "exec", "retry", "Agent löst auf"),
    ]
    return {"nodes": nodes, "edges": edges}


# ── Slot: Abnahme & Auslieferung ─────────────────────────────────────────────

def build_acceptance() -> dict:
    """Was bisher `/issues/{key}/complete` tat: Testumgebung abräumen, mergen, deployen.

    Die Reihenfolge ist bindend (ABC-18): erst Testumgebung weg (Container, Volumes,
    Worktree, Port), dann mergen — und nur bei sauberem Merge weiter.
    """
    nodes = [
        _n("start", "start", 0, 0, {"label": "Abnahme"}),
        _n("stop_testenv", "auto_action", 0, 1,
           _action("stop_testenv", "Testumgebung abräumen")),
        _n("merge", "auto_action", 0, 2,
           _action("accept_merge", "Branch mergen / PR öffnen")),
        _n("deploy", "auto_action", 0, 3, _action("deploy", "Deployment einreihen")),
        _n("end_ok", "end", 0, 4, {"label": "Geliefert", "outcome": "completed"}),
        _n("end_fail", "end", 1, 4, {"label": "Merge offen", "outcome": "failed"}),
    ]
    edges = [
        _e("start", "stop_testenv"),
        _e("stop_testenv", "merge"),
        _e("merge", "deploy", "merged", "gemerged"),
        _e("merge", "end_ok", "pr_open", "PR offen"),
        _e("merge", "end_ok", "no_git", "kein Git"),
        _e("merge", "end_ok", "out"),
        _e("merge", "end_fail", "conflict", "Konflikt"),
        _e("merge", "end_fail", "push_failed", "Push fehlgeschlagen"),
        _e("merge", "end_fail", "pr_failed"),
        _e("merge", "end_fail", "gone"),
        _e("deploy", "end_ok"),
    ]
    return {"nodes": nodes, "edges": edges}


# ── Slot: Ticket-Eingang ─────────────────────────────────────────────────────

def build_ticket_intake() -> dict:
    """Eingehende Meldung (Webhook/Mail) → Ticket. Der Kontext kommt aus dem Auslöser:
    `title`, `body`, optional `agent` (dann wird gleich zugewiesen) und `ignore`."""
    nodes = [
        _n("start", "start", 0, 0, {"label": "Meldung eingegangen"}),
        _n("relevant", "decision", 0, 1, {
            "label": "Relevant?",
            "branches": [
                {"handle": "skip", "label": "verwerfen",
                 "guard": {"==": [{"var": "ignore"}, True]}},
                {"handle": "keep", "label": "übernehmen"},
            ],
            "default_handle": "keep",
        }),
        _n("create", "auto_action", 0, 2, _action(
            "create_ticket", "Ticket anlegen",
            summary="{{title}}", description="{{body}}", assigned_agent="{{agent}}")),
        _n("end_ok", "end", 0, 3, {"label": "Angelegt", "outcome": "completed"}),
        _n("end_skip", "end", 1, 2, {"label": "Verworfen", "outcome": "completed"}),
    ]
    edges = [
        _e("start", "relevant"),
        _e("relevant", "create", "keep"),
        _e("relevant", "end_skip", "skip"),
        _e("create", "end_ok"),
    ]
    return {"nodes": nodes, "edges": edges}


# ── Slot: Hardware-Beschaffung ───────────────────────────────────────────────

def build_hardware_procurement() -> dict:
    from .hardware_workflow import DEFAULT_STEPS, build_hardware_graph
    return build_hardware_graph(list(DEFAULT_STEPS))


BUILDERS = {
    WorkflowSlot.ticket_lifecycle.value: build_ticket_lifecycle,
    WorkflowSlot.acceptance.value: build_acceptance,
    WorkflowSlot.hardware_procurement.value: build_hardware_procurement,
    WorkflowSlot.ticket_intake.value: build_ticket_intake,
}


# ── Seed ─────────────────────────────────────────────────────────────────────

async def ensure_builtin_set(db: AsyncSession) -> WorkflowSet:
    """Globalen Standard-Satz anlegen bzw. auf Stand bringen (idempotent).

    Neue Versionen werden NUR bei echtem Graph-Unterschied veröffentlicht — sonst würde
    jeder Backend-Start die Versionshistorie aufblähen.
    """
    s = (await db.execute(select(WorkflowSet).where(WorkflowSet.is_builtin.is_(True))
                          .order_by(WorkflowSet.id))).scalars().first()
    if s is None:
        s = WorkflowSet(
            scope=WorkflowSetScope.global_, user_id=None, key=BUILTIN_SET_KEY,
            name="Traccoon-Standard",
            description="Ausgelieferte Abläufe. Kopierbar als persönlicher oder Projekt-Satz.",
            is_builtin=True, builtin_revision=0,
        )
        db.add(s)
        await db.flush()

    changed = 0
    for slot, build in BUILDERS.items():
        graph = build()
        meta = SLOT_META[slot]
        d = (await db.execute(select(WorkflowDefinition).where(
            WorkflowDefinition.set_id == s.id, WorkflowDefinition.slot == slot,
            WorkflowDefinition.archived_at.is_(None)))).scalar_one_or_none()
        if d is None:
            d = WorkflowDefinition(
                project_id=None, set_id=s.id, slot=slot, key=slot, name=meta["name"],
                description=meta["description"],
                subject_kind=WorkflowSubjectKind(meta["subject_kind"]),
            )
            db.add(d)
            await db.flush()
        current = (await db.get(WorkflowVersion, d.current_version_id)
                   if d.current_version_id else None)
        if current is not None and current.graph == graph:
            continue
        last = (await db.execute(select(WorkflowVersion)
                                 .where(WorkflowVersion.definition_id == d.id)
                                 .order_by(WorkflowVersion.version.desc()))).scalars().first()
        version = WorkflowVersion(
            definition_id=d.id, version=(last.version + 1) if last else 1, graph=graph,
            status=WorkflowVersionStatus.published,
            published_at=dt.datetime.now(tz=dt.timezone.utc),
            notes=f"Ausgelieferter Standard (Revision {BUILTIN_REVISION})",
        )
        db.add(version)
        await db.flush()
        d.current_version_id = version.id
        changed += 1

    if changed:
        s.builtin_revision = BUILTIN_REVISION
        log.info("Standard-Satz aktualisiert: %d Ablauf/Abläufe neu veröffentlicht", changed)
    await db.commit()
    await db.refresh(s)
    return s
