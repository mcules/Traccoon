"""What the vault decides about itself, and its own look."""
from __future__ import annotations

import json

from app.notes import appearance as ap
from app.notes.settings import options as op


def write(root, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ------------------------------------------------------- the editor's habits

def test_how_the_vault_says_a_tab_works(tmp_path) -> None:
    """A vault written with tabs four columns wide is not one where two spaces
    are a nesting level. Deciding that here rather than reading it makes the
    list not move and Tab look broken."""
    write(tmp_path, ".conf/app.json",
          json.dumps({"useTab": False, "tabSize": 2, "readableLineLength": False,
                      "showInlineTitle": True, "mobileToolbarCommands": ["a", "b"]}))
    out = op.from_user({}, tmp_path, ".conf")
    assert out.use_tab is False and out.tab_size == 2
    assert out.readable_line_length is False and out.show_inline_title is True
    assert out.mobile_toolbar == ["a", "b"]


def test_a_vault_that_says_nothing_gets_the_usual(tmp_path) -> None:
    out = op.from_user({}, tmp_path, ".conf")
    assert out.use_tab is True and out.tab_size == 4


def test_a_width_that_is_not_a_width_is_ignored(tmp_path) -> None:
    """Everything here comes out of a file somebody else writes, and a boolean
    is an integer in Python's eyes."""
    write(tmp_path, ".conf/app.json", json.dumps({"tabSize": True}))
    assert op.from_user({}, tmp_path, ".conf").tab_size == 4
    write(tmp_path, ".conf/app.json", json.dumps({"tabSize": 0}))
    assert op.from_user({}, tmp_path, ".conf").tab_size == 4


def test_the_daily_note_template_comes_from_the_vault(tmp_path) -> None:
    write(tmp_path, ".conf/daily-notes.json",
          json.dumps({"folder": "Tage", "format": "YYYY/MM/YYYY-MM-DD",
                      "template": "Vorlagen/Tag"}))
    out = op.from_user({}, tmp_path, ".conf")
    assert out.daily_folder == "Tage"
    assert out.daily_format == "YYYY/MM/YYYY-MM-DD"
    assert out.daily_template == "Vorlagen/Tag"


# --------------------------------------------------------------- the CSS it has

def test_the_snippets_the_vault_carries(tmp_path) -> None:
    write(tmp_path, ".conf/snippets/eins.css", "a{}")
    write(tmp_path, ".conf/snippets/zwei.css", "b{}")
    write(tmp_path, ".conf/snippets/keine.txt", "nichts")
    write(tmp_path, ".conf/appearance.json",
          json.dumps({"enabledCssSnippets": ["zwei", "laengst geloescht"]}))
    out = ap.info(tmp_path, ".conf", "")
    assert out["snippets"] == ["eins", "zwei"]
    # A name left over from a snippet that is gone would be a stylesheet the
    # page waits for in vain.
    assert out["enabledSnippets"] == ["zwei"]


def test_a_snippet_is_read_by_name_and_only_by_name(tmp_path) -> None:
    write(tmp_path, ".conf/snippets/eins.css", "a{color:red}")
    assert ap.snippet(tmp_path, ".conf", "eins") == "a{color:red}"
    assert ap.snippet(tmp_path, ".conf", "gibt es nicht") is None
    # The name never walks a filesystem.
    assert ap.snippet(tmp_path, ".conf", "../../etc/passwd") is None
    assert ap.snippet(tmp_path, ".conf", "eins/../eins") is None


def test_a_vault_without_a_configuration_folder_has_no_look(tmp_path) -> None:
    out = ap.info(tmp_path, "", "")
    assert out["snippets"] == [] and out["enabledSnippets"] == []
    assert ap.snippet(tmp_path, "", "eins") is None


# ------------------------------------------------------------ coloured folders

def theme(tmp_path, **values) -> None:
    write(tmp_path, ".style/data.json",
          json.dumps({f"{ap.THEME_PREFIX}{k}": v for k, v in values.items()}))


def test_the_tree_takes_its_colours_from_the_vault(tmp_path) -> None:
    theme(tmp_path, **{"anp-alt-rainbow-style": "anp-full-rainbow-color-toggle",
                       "anp-rainbow-folder-bg-opacity": 0.7,
                       "anp-rainbow-file-toggle": True})
    out = ap.colours(tmp_path, ".style")
    assert out.style == "full" and out.opacity == 0.7
    assert out.files is True and out.inherit is False


def test_an_untouched_setting_means_the_theme_as_it_ships(tmp_path) -> None:
    """That plugin writes a key only once somebody has touched it, so an absent
    one is not "off by accident" — it is the default, which is no colours."""
    theme(tmp_path)
    assert ap.colours(tmp_path, ".style").style == "off"
    assert ap.colours(tmp_path, ".style").opacity == 1.0


def test_what_the_person_set_wins_over_the_vault(tmp_path) -> None:
    theme(tmp_path, **{"anp-alt-rainbow-style": "anp-full-rainbow-color-toggle",
                       "anp-rainbow-folder-bg-opacity": 0.7})
    out = ap.colours(tmp_path, ".style", chosen_style="simple", chosen_opacity=0.3)
    assert out.style == "simple" and out.opacity == 0.3


def test_a_missing_theme_file_is_not_a_failure(tmp_path) -> None:
    assert ap.colours(tmp_path, ".style").style == "off"
    assert ap.colours(tmp_path, "").style == "off"
