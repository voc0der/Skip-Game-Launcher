"""The public Markdown index is a generated view of games.json."""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

from helpers import load_script


REPO = pathlib.Path(__file__).resolve().parent.parent


def test_committed_index_is_current():
    result = subprocess.run(
        [sys.executable, "scripts/index.py", "--check"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_index_links_each_declared_store():
    index = load_script("index")
    games = json.loads((REPO / "games.json").read_text(encoding="utf-8"))
    rendered = index.render(games)
    checked = 0
    for game in games:
        for keys, label, directory in index.STORE_COLUMNS:
            if any(key in game["stores"] for key in keys):
                assert f"[{label}]({directory}/{game['out']})" in rendered
                checked += 1
    # A column key that stops matching would otherwise leave this asserting
    # nothing at all and still passing.
    assert checked >= len(games)


def test_index_columns_cover_every_known_store(verify_mod):
    """A store missing from STORE_COLUMNS is silently absent from GAMES.md."""
    index = load_script("index")
    covered = {
        key: directory
        for keys, _, directory in index.STORE_COLUMNS
        for key in keys
    }
    assert covered == verify_mod.STORE_DIRS
