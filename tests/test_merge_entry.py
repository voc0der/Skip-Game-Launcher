"""Semantic manifest updates used when concurrent game requests race."""
from __future__ import annotations

import io
import json
import sys

import pytest

from helpers import load_script


def test_merges_different_parallel_requests_without_losing_either():
    module = load_script("merge_entry")
    games = [{"name": "Alpha", "out": "Alpha.exe", "stores": {"steam": "1"}}]
    changed = module.merge_entry(
        games,
        {"name": "Beta", "out": "Beta.exe", "stores": {"epic": "BetaApp"}},
    )
    assert changed is True
    assert [game["name"] for game in games] == ["Alpha", "Beta"]


def test_enriches_a_game_that_another_request_merged_first():
    module = load_script("merge_entry")
    games = [{"name": "Alpha", "out": "Alpha.exe", "stores": {"steam": "1"}}]
    changed = module.merge_entry(
        games,
        {"name": "Alpha", "out": "Alpha.exe", "stores": {"epic": "AlphaApp"}},
    )
    assert changed is True
    assert games[0]["stores"] == {"epic": "AlphaApp", "steam": "1"}


def test_reapplying_the_same_entry_is_idempotent():
    module = load_script("merge_entry")
    entry = {"name": "Alpha", "out": "Alpha.exe", "stores": {"steam": "1"}}
    games = [{"name": "Alpha", "out": "Alpha.exe", "stores": {"steam": "1"}}]
    assert module.merge_entry(games, entry) is False


def test_refuses_to_overwrite_a_conflicting_store_id():
    module = load_script("merge_entry")
    games = [{"name": "Alpha", "out": "Alpha.exe", "stores": {"steam": "1"}}]
    with pytest.raises(ValueError, match="conflicting steam ID"):
        module.merge_entry(
            games,
            {"name": "Alpha", "out": "Alpha.exe", "stores": {"steam": "2"}},
        )


def test_reads_stdin_as_utf8_regardless_of_the_locale(tmp_path, monkeypatch):
    """The workflow pipes this in on a Windows runner, where sys.stdin's text
    wrapper defaults to the ANSI code page and turns a UTF-8 "™" into "â„¢".
    Eight manifest names were corrupted that way before this was pinned."""
    module = load_script("merge_entry")
    manifest = tmp_path / "games.json"
    manifest.write_text("[]", encoding="utf-8")

    payload = json.dumps(
        {"name": "Sekiro™", "out": "SekiroTM.exe", "stores": {"steam": "814380"}},
        ensure_ascii=False,
    ).encode("utf-8")

    class AnsiStdin:
        """Text reads mis-decode, exactly as they would on the runner."""
        buffer = io.BytesIO(payload)

        def read(self):
            return payload.decode("cp1252")

    monkeypatch.setattr(sys, "stdin", AnsiStdin())
    monkeypatch.setattr(sys, "argv", ["merge_entry.py", "--manifest", str(manifest)])
    assert module.main() == 0

    assert json.loads(manifest.read_text(encoding="utf-8"))[0]["name"] == "Sekiro™"
