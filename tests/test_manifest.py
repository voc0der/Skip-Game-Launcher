"""Invariants over the real games.json and the committed executables.

games.json is the source of truth for what gets built, and it is edited by CI as
well as by hand, so its shape is worth pinning down.
"""
from __future__ import annotations

import collections
import json
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
STORE_DIRS = {
    "steam": "Steam",
    "battlenet": "BattleNet",
    "battlenetuid": "BattleNet",
    "epic": "Epic",
    "ubisoft": "Ubisoft",
}
SAFE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.exe")
WINDOWS_DEVICE_NAME = re.compile(
    r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", re.IGNORECASE
)


@pytest.fixture(scope="module")
def manifest():
    return json.loads((REPO / "games.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def targets(manifest):
    """Every (game, store) pair the manifest promises."""
    return [
        (game, store, app_id)
        for game in manifest
        for store, app_id in game["stores"].items()
    ]


# --- shape ------------------------------------------------------------------

def test_is_a_list_of_objects(manifest):
    assert isinstance(manifest, list) and manifest
    assert all(isinstance(g, dict) for g in manifest)


def test_every_entry_has_the_required_fields(manifest):
    for game in manifest:
        assert set(game) == {"name", "out", "stores"}, game
        assert isinstance(game["name"], str) and game["name"].strip()
        assert isinstance(game["out"], str) and game["out"]
        assert isinstance(game["stores"], dict) and game["stores"]


def test_entries_are_sorted_by_filename(manifest):
    """CI rewrites this file; a stable order keeps diffs and merges sane."""
    names = [g["out"] for g in manifest]
    assert names == sorted(names, key=str.lower)


def test_filenames_are_safe(manifest):
    """`out` becomes a filesystem path and a git pathspec."""
    for game in manifest:
        assert SAFE_FILENAME.fullmatch(game["out"]), game["out"]
        assert not WINDOWS_DEVICE_NAME.match(game["out"]), game["out"]


def test_filenames_are_unique(manifest):
    counts = collections.Counter(g["out"].lower() for g in manifest)
    assert [n for n, c in counts.items() if c > 1] == []


def test_names_are_unique(manifest):
    """resolve.py identifies an existing game by name, so duplicates are ambiguous."""
    normalised = [re.sub(r"[^a-z0-9]", "", g["name"].lower()) for g in manifest]
    counts = collections.Counter(normalised)
    assert [n for n, c in counts.items() if c > 1] == []


# --- stores and ids ---------------------------------------------------------

def test_stores_are_known(targets):
    for game, store, _ in targets:
        assert store in STORE_DIRS, f"{game['out']} lists unknown store {store!r}"


def test_ids_are_non_empty_strings(targets):
    for game, store, app_id in targets:
        assert isinstance(app_id, str) and app_id.strip(), f"{game['out']}/{store}"


@pytest.mark.parametrize("store", ["steam", "ubisoft"])
def test_numeric_stores_have_numeric_ids(manifest, store):
    for game in manifest:
        app_id = game["stores"].get(store)
        if app_id is not None:
            assert app_id.isdigit(), f"{game['out']}: {store} id {app_id!r} is not numeric"


def test_epic_ids_are_app_names_not_store_slugs(manifest):
    """`metro-2033-redux` is the store slug; the launcher needs `Petunia`."""
    for game in manifest:
        app_id = game["stores"].get("epic")
        if app_id is not None:
            assert "-" not in app_id and "/" not in app_id, (
                f"{game['out']}: epic id {app_id!r} looks like a store slug"
            )


def test_no_id_is_reused_across_games(targets):
    """Two entries on one app id would build two identical executables."""
    seen = collections.defaultdict(list)
    for game, store, app_id in targets:
        seen[(store, app_id)].append(game["out"])
    clashes = {k: v for k, v in seen.items() if len(v) > 1}
    assert not clashes, f"ids used by more than one game: {clashes}"


# --- the committed tree -----------------------------------------------------

def test_no_executables_left_at_the_repo_root():
    assert list(REPO.glob("*.exe")) == []


def test_store_folders_contain_only_declared_executables(manifest):
    """An orphan .exe is one nothing rebuilds and nothing verifies."""
    declared = {
        f"{STORE_DIRS[store]}/{game['out']}"
        for game in manifest
        for store in game["stores"]
    }
    on_disk = {
        f"{path.parent.name}/{path.name}"
        for folder in STORE_DIRS.values()
        for path in (REPO / folder).glob("*.exe")
        if (REPO / folder).is_dir()
    }
    assert not (on_disk - declared), f"executables missing from games.json: {sorted(on_disk - declared)}"


def test_committed_executables_launch_what_the_manifest_says(verify_mod, targets):
    """The end-to-end check: what shipped matches what's declared."""
    wrong = []
    for game, store, app_id in targets:
        path = REPO / STORE_DIRS[store] / game["out"]
        if not path.exists():
            continue  # not built yet; CI will produce it
        got_store, got_id, _ = verify_mod.inspect(str(path))
        if got_store != store or (got_id is not None and got_id != app_id):
            wrong.append(f"{path.name}: declared {store}/{app_id}, binary has {got_store}/{got_id}")
    assert not wrong, "\n".join(wrong)


def test_folders_present_for_every_store_in_use(manifest):
    used = {STORE_DIRS[s] for g in manifest for s in g["stores"]}
    missing = [f for f in used if not (REPO / f).is_dir()]
    assert not missing, f"manifest uses stores with no folder: {missing}"
