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
    for game in games:
        for store, _, directory in index.STORE_COLUMNS:
            expected = f"[{dict((key, label) for key, label, _ in index.STORE_COLUMNS)[store]}]({directory}/{game['out']})"
            if store in game["stores"]:
                assert expected in rendered
