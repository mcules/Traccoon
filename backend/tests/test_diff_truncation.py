"""A truncated diff has to give itself away as truncated.

ABC-32 on 2026-08-07, review round 2: the reviewer saw a compose block that "breaks off in
the middle (`v` as the last line)" and reported an incomplete service definition. The code
was complete; `diff_text` had truncated it at 20 000 characters, silently and in the middle
of a word. That cost one of the two correction rounds and sent the ticket to the human with
a phantom finding.
"""
from app.worker import gitops


class _Ctx:
    worktree = "/ws"
    workdir = "/ws"
    base_commit = "abc"
    main = "main"


def _diff(files: int, lines_per: int) -> str:
    parts = []
    for i in range(files):
        parts.append(f"diff --git a/datei_{i}.py b/datei_{i}.py\n--- a/datei_{i}.py\n+++ b/datei_{i}.py\n")
        parts += [f"+zeile {n} in datei {i} mit etwas Text\n" for n in range(lines_per)]
    return "".join(parts)


async def _text(monkeypatch, raw: str, max_chars: int) -> str:
    async def fake_is_repo(_wd):
        return True

    async def fake_git(_wd, *args):
        return 0, raw

    monkeypatch.setattr(gitops, "_is_repo", fake_is_repo)
    monkeypatch.setattr(gitops, "_git", fake_git)
    return await gitops.diff_text(_Ctx(), max_chars=max_chars)


async def test_a_short_diff_stays_untouched(monkeypatch):
    raw = _diff(1, 3)
    assert await _text(monkeypatch, raw, 100_000) == raw


async def test_capping_says_so_and_ends_on_a_line_boundary(monkeypatch):
    raw = _diff(4, 60)
    text = await _text(monkeypatch, raw, 1000)

    assert "Diff gekappt" in text
    assert "KEIN unvollständiger Code" in text
    header = text.split("\n[... Diff gekappt")[0]
    assert header.endswith("\n") or header == "", "cut in the middle of the row"


async def test_missing_files_are_named(monkeypatch):
    """So that the reviewer knows where to look itself, instead of guessing."""
    text = await _text(monkeypatch, _diff(4, 60), 1000)

    assert "datei_3.py" in text.split("Diff gekappt")[1]


async def test_the_diff_also_shows_uncommitted_fixes(monkeypatch, tmp_path):
    """The review gate has to see the WORKING STATE, not only the commits.

    Committing happens only AFTER the gate. With `base...HEAD` the reviewer therefore saw the
    state before its own findings, and the standstill detection reported "nothing changed";
    both tickets of 2026-08-07 ended that way after exactly one correction round although the
    correction had long been written.
    """
    commands: list[tuple] = []

    async def fake_is_repo(_wd):
        return True

    async def fake_git(_wd, *args):
        commands.append(args)
        if args[0] == "merge-base":
            return 0, "abc123\n"
        return 0, "--- a\n+++ b\n+neu\n"

    monkeypatch.setattr(gitops, "_is_repo", fake_is_repo)
    monkeypatch.setattr(gitops, "_git", fake_git)

    class _C:
        worktree = "/ws"
        workdir = "/ws"
        base_commit = None
        main = "main"

    await gitops.diff_text(_C())

    diffs = [a for a in commands if a[0] == "diff"]
    assert diffs, "no diff was built at all"
    assert "..." not in " ".join(diffs[0]), "a three dot diff overlooks the uncommitted correction"
    assert diffs[0] == ("diff", "abc123"), "the base has to be the branching point, not today's main"


async def test_the_base_is_the_branch_point_even_with_a_base_commit(monkeypatch):
    """`git_base_sha` is the main state at the last preparation, NOT the branching point.

    `prepare` rewrites it on every reuse of the worktree. A two dot diff against that state
    shows everything main has gained since the real branching as "deleted": with ABC-31 on
    2026-08-07 that was 1993 lines, and the reviewer reported that the agent had removed the
    `may_plan_continue` node, which it had never touched.
    """
    commands: list[tuple] = []

    async def fake_is_repo(_wd):
        return True

    async def fake_git(_wd, *args):
        commands.append(args)
        if args[0] == "merge-base":
            return 0, "abzweig456\n"
        return 0, "--- a\n+++ b\n+neu\n"

    monkeypatch.setattr(gitops, "_is_repo", fake_is_repo)
    monkeypatch.setattr(gitops, "_git", fake_git)

    class _C:
        worktree = "/ws"
        workdir = "/ws"
        base_commit = "c489052"      # the main state, not the branching point
        main = "main"

    await gitops.diff_text(_C())

    assert ("merge-base", "c489052", "HEAD") in commands, "the merge base was skipped"
    assert ("diff", "abzweig456") in commands, "the comparison ran against the wrong state"
    assert ("diff", "c489052") not in commands
