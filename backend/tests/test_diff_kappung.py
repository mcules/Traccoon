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
