#!/usr/bin/env python3
"""Merge one resolved game entry into the latest games.json.

The request workflow can build against an older commit while other requests
merge. Before publishing, it checks out the latest master and uses this script
to reapply its resolved entry semantically instead of rebasing JSON text.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys


def normalise(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def merge_entry(games: list[dict], entry: dict) -> bool:
    """Merge ``entry`` in place. Return whether the manifest changed."""
    if set(entry) != {"name", "out", "stores"} or not isinstance(entry["stores"], dict):
        raise ValueError("resolved entry has an invalid shape")

    by_name = next(
        (game for game in games if normalise(game["name"]) == normalise(entry["name"])),
        None,
    )
    by_out = next(
        (game for game in games if game["out"].lower() == entry["out"].lower()),
        None,
    )
    if by_name is not None and by_out is not None and by_name is not by_out:
        raise ValueError("resolved name and filename refer to different manifest entries")
    target = by_name or by_out

    if target is not None:
        if normalise(target["name"]) != normalise(entry["name"]):
            raise ValueError(f"output filename {entry['out']} already belongs to {target['name']}")
        if target["out"].lower() != entry["out"].lower():
            raise ValueError(f"game {entry['name']} already uses output filename {target['out']}")

    for store, app_id in entry["stores"].items():
        for game in games:
            if game is not target and game.get("stores", {}).get(store) == app_id:
                raise ValueError(
                    f"{store} ID {app_id} is already assigned to {game['name']}"
                )
        if target is not None and store in target["stores"]:
            if target["stores"][store] != app_id:
                raise ValueError(
                    f"{entry['name']} already has conflicting {store} ID "
                    f"{target['stores'][store]}"
                )

    if target is None:
        games.append(
            {
                "name": entry["name"],
                "out": entry["out"],
                "stores": dict(sorted(entry["stores"].items())),
            }
        )
        games.sort(key=lambda game: game["out"].lower())
        return True

    before = dict(target["stores"])
    target["stores"].update(entry["stores"])
    target["stores"] = dict(sorted(target["stores"].items()))
    return target["stores"] != before


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="games.json")
    args = parser.parse_args()

    entry = json.load(sys.stdin)
    path = pathlib.Path(args.manifest)
    games = json.loads(path.read_text(encoding="utf-8"))
    changed = merge_entry(games, entry)
    if changed:
        path.write_text(
            json.dumps(games, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    print("updated" if changed else "already current")
    return 0


if __name__ == "__main__":
    sys.exit(main())
