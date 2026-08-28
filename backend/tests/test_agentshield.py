"""The configuration audit: what the comparing has to get right.

Four claims, and every one of them was wrong once in the collector this replaces:

* the key of a finding survives a reworded title (it did not, and every rewording retired a
  finding and opened an identical one beside it),
* a decision to ignore survives a scan (otherwise clicking it away is worth nothing),
* a finding that disappears is `fixed`, not deleted (its history is the point),
* and the very first run is a baseline, not a hundred single events.
"""
import pytest

from app.models.agentshield import ShieldFinding, ShieldRun, ShieldRunConfig
from app.services import agentshield
from sqlalchemy import select


def report(*configs):
    """The answer of the scanner, as the container hands it over."""
    return {"configs": list(configs)}


def config(name, *findings, grade="A"):
    return {"config": name, "grade": grade, "findings": list(findings)}


def finding(id="permissions-no-deny-list", severity="high", file="settings.local.json",
            title="No deny list configured", **rest):
    return {"id": id, "severity": severity, "file": file, "title": title, **rest}


@pytest.fixture
def scanner(monkeypatch):
    """A scanner one can put an answer into, instead of a container."""
    answers = {}

    async def fake(timeout=None):
        return answers["next"]

    monkeypatch.setattr(agentshield, "ask_scanner", fake)
    return answers


# ── The key ──────────────────────────────────────────────────────────────────

def test_key_survives_a_reworded_title():
    """The one that cost thirty findings a day. The tool renames, the matter stays."""
    before = agentshield.finding_key("uniwar", finding(title="No PreToolUse hooks"))
    after = agentshield.finding_key(
        "uniwar", finding(title="No PreToolUse security hooks configured"))
    assert before == after


def test_key_separates_two_rules_in_one_file():
    """Two allow rules in one file are two findings — the rule id is what tells them apart."""
    one = agentshield.finding_key("uniwar", finding(id="permissions-permissive-Bash(docker rm *)"))
    two = agentshield.finding_key("uniwar", finding(id="permissions-permissive-Bash(docker run *)"))
    assert one != two


def test_key_separates_the_same_rule_in_two_configurations():
    assert (agentshield.finding_key("uniwar", finding())
            != agentshield.finding_key("wavelog", finding()))


# ── The comparing ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_first_run_is_a_baseline(db, scanner):
    scanner["next"] = report(config("uniwar", finding(), finding(id="hooks-no-pretooluse")))
    summary = await agentshield.scan(db)
    assert summary["baseline"] is True
    assert summary["findings"] == 2
    # Two findings, but nothing "new": whoever hangs a flow on the event gets one run, not a
    # message per line of the starting stock.
    assert summary["new"] == 0
    assert (await db.execute(select(ShieldFinding))).scalars().all().__len__() == 2


@pytest.mark.asyncio
async def test_a_second_identical_run_changes_nothing(db, scanner):
    scanner["next"] = report(config("uniwar", finding()))
    await agentshield.scan(db)
    summary = await agentshield.scan(db)
    assert (summary["new"], summary["fixed"]) == (0, 0)
    row = (await db.execute(select(ShieldFinding))).scalar_one()
    assert row.seen_count == 2


@pytest.mark.asyncio
async def test_a_reworded_title_is_not_a_new_finding(db, scanner):
    scanner["next"] = report(config("uniwar", finding(title="No deny list")))
    await agentshield.scan(db)
    scanner["next"] = report(config("uniwar", finding(title="No deny list configured")))
    summary = await agentshield.scan(db)
    assert (summary["new"], summary["fixed"]) == (0, 0)
    row = (await db.execute(select(ShieldFinding))).scalar_one()
    # The row follows the new wording without becoming another matter.
    assert row.title == "No deny list configured"


@pytest.mark.asyncio
async def test_what_is_gone_stays_as_fixed(db, scanner):
    scanner["next"] = report(config("uniwar", finding()))
    await agentshield.scan(db)
    scanner["next"] = report(config("uniwar"))
    summary = await agentshield.scan(db)
    assert summary["fixed"] == 1
    row = (await db.execute(select(ShieldFinding))).scalar_one()
    assert row.status == "fixed"      # not deleted: how long it stood is the point


@pytest.mark.asyncio
async def test_a_scan_does_not_reopen_what_was_ignored(db, scanner):
    scanner["next"] = report(config("uniwar", finding()))
    await agentshield.scan(db)
    row = (await db.execute(select(ShieldFinding))).scalar_one()
    row.status = "ignored"
    await db.flush()

    await agentshield.scan(db)
    row = (await db.execute(select(ShieldFinding))).scalar_one()
    assert row.status == "ignored"


@pytest.mark.asyncio
async def test_a_returning_finding_keeps_its_history(db, scanner):
    scanner["next"] = report(config("uniwar", finding()))
    await agentshield.scan(db)
    first_seen = (await db.execute(select(ShieldFinding))).scalar_one().first_seen

    scanner["next"] = report(config("uniwar"))
    await agentshield.scan(db)
    scanner["next"] = report(config("uniwar", finding()))
    summary = await agentshield.scan(db)

    row = (await db.execute(select(ShieldFinding))).scalar_one()
    assert row.status == "open"
    assert row.first_seen == first_seen     # since when this has been a matter at all
    assert summary["new"] == 1              # it IS news that it is back


# ── The history ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_run_records_every_configuration_it_looked_at(db, scanner):
    scanner["next"] = report(
        config("uniwar", finding(severity="critical")),
        config("wavelog", grade="B"),
        {"config": "broken", "error": "the tool fell over"},
    )
    await agentshield.scan(db)
    run = (await db.execute(select(ShieldRun))).scalar_one()
    rows = {r.config: r for r in
            (await db.execute(select(ShieldRunConfig).where(
                ShieldRunConfig.run_id == run.id))).scalars().all()}
    assert set(rows) == {"uniwar", "wavelog", "broken"}
    assert rows["uniwar"].critical == 1
    # A clean configuration and one whose scan broke are not the same answer.
    assert rows["wavelog"].error == "" and rows["wavelog"].high == 0
    assert rows["broken"].error and rows["broken"].grade == "?"


@pytest.mark.asyncio
async def test_the_counts_of_a_run_match_its_findings(db, scanner):
    """A severity the house does not know is dropped — and must not be counted either."""
    scanner["next"] = report(config("uniwar",
                                    finding(severity="critical"),
                                    finding(id="other", severity="nonsense")))
    summary = await agentshield.scan(db)
    assert summary["findings"] == 1
    run = (await db.execute(select(ShieldRun))).scalar_one()
    assert run.findings == 1 and run.critical == 1
    row = (await db.execute(select(ShieldRunConfig))).scalar_one()
    assert sum([row.critical, row.high, row.medium, row.low, row.info]) == 1
