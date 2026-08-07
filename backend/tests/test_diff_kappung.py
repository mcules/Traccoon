"""Ein gekappter Diff muss sich als gekappt zu erkennen geben.

ABC-32 am 2026-08-07, Prüfrunde 2: der Prüfer sah einen compose-Block, der „mittendrin
abbricht (`v` als letzte Zeile)", und meldete eine unvollständige Service-Definition. Der
Code war vollständig — gekappt hatte `diff_text` bei 20 000 Zeichen, stumm und mitten im
Wort. Das kostete eine der zwei Korrektur-Runden und schickte das Ticket mit einem
Phantom-Befund an den Menschen.
"""
from app.worker import gitops


class _Ctx:
    worktree = "/ws"
    workdir = "/ws"
    base_commit = "abc"
    main = "main"


def _diff(dateien: int, zeilen_je: int) -> str:
    teile = []
    for i in range(dateien):
        teile.append(f"diff --git a/datei_{i}.py b/datei_{i}.py\n--- a/datei_{i}.py\n+++ b/datei_{i}.py\n")
        teile += [f"+zeile {n} in datei {i} mit etwas Text\n" for n in range(zeilen_je)]
    return "".join(teile)


async def _text(monkeypatch, roh: str, max_chars: int) -> str:
    async def fake_is_repo(_wd):
        return True

    async def fake_git(_wd, *args):
        return 0, roh

    monkeypatch.setattr(gitops, "_is_repo", fake_is_repo)
    monkeypatch.setattr(gitops, "_git", fake_git)
    return await gitops.diff_text(_Ctx(), max_chars=max_chars)


async def test_kurzer_diff_bleibt_unangetastet(monkeypatch):
    roh = _diff(1, 3)
    assert await _text(monkeypatch, roh, 100_000) == roh


async def test_kappung_sagt_es_und_endet_auf_einer_zeilengrenze(monkeypatch):
    roh = _diff(4, 60)
    text = await _text(monkeypatch, roh, 1000)

    assert "Diff gekappt" in text
    assert "KEIN unvollständiger Code" in text
    kopf = text.split("\n[... Diff gekappt")[0]
    assert kopf.endswith("\n") or kopf == "", "mitten in der Zeile geschnitten"


async def test_fehlende_dateien_werden_benannt(monkeypatch):
    """Damit der Prüfer weiß, wo er selbst nachsehen muss — statt zu raten."""
    text = await _text(monkeypatch, _diff(4, 60), 1000)

    assert "datei_3.py" in text.split("Diff gekappt")[1]


async def test_diff_zeigt_auch_uncommittete_korrekturen(monkeypatch, tmp_path):
    """Das Review-Gate muss den ARBEITSSTAND sehen, nicht nur die Commits.

    Committet wird erst NACH dem Gate. Mit `base...HEAD` sah der Prüfer deshalb den Stand vor
    seinen eigenen Befunden, und die Stillstands-Erkennung meldete „nichts verändert" — beide
    Tickets vom 2026-08-07 endeten so nach genau einer Korrekturrunde, obwohl die Korrektur
    längst geschrieben war.
    """
    befehle: list[tuple] = []

    async def fake_is_repo(_wd):
        return True

    async def fake_git(_wd, *args):
        befehle.append(args)
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

    diffs = [a for a in befehle if a[0] == "diff"]
    assert diffs, "es wurde gar kein Diff gebildet"
    assert "..." not in " ".join(diffs[0]), "Drei-Punkt-Diff übersieht die uncommittete Korrektur"
    assert diffs[0] == ("diff", "abc123"), "Basis muss der Abzweigpunkt sein, nicht der heutige main"


async def test_basis_ist_der_abzweigpunkt_auch_mit_base_commit(monkeypatch):
    """`git_base_sha` ist der main-Stand beim letzten Vorbereiten, NICHT der Abzweigpunkt.

    `prepare` schreibt ihn bei jeder Wiederverwendung des Worktrees neu. Ein Zwei-Punkt-Diff
    gegen diesen Stand zeigt alles, was main seit dem echten Abzweig dazubekommen hat, als
    „gelöscht": bei ABC-31 am 2026-08-07 waren das 1993 Zeilen, und der Prüfer meldete, der
    Agent habe den `may_plan_continue`-Knoten entfernt — den er nie angefasst hatte.
    """
    befehle: list[tuple] = []

    async def fake_is_repo(_wd):
        return True

    async def fake_git(_wd, *args):
        befehle.append(args)
        if args[0] == "merge-base":
            return 0, "abzweig456\n"
        return 0, "--- a\n+++ b\n+neu\n"

    monkeypatch.setattr(gitops, "_is_repo", fake_is_repo)
    monkeypatch.setattr(gitops, "_git", fake_git)

    class _C:
        worktree = "/ws"
        workdir = "/ws"
        base_commit = "c489052"      # main-Stand, nicht der Abzweig
        main = "main"

    await gitops.diff_text(_C())

    assert ("merge-base", "c489052", "HEAD") in befehle, "merge-base wurde übersprungen"
    assert ("diff", "abzweig456") in befehle, "es wurde gegen den falschen Stand verglichen"
    assert ("diff", "c489052") not in befehle
