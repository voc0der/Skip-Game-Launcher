#!/usr/bin/env python3
"""Check that built launchers actually contain the launch command they should.

IExpress gives no useful build-time feedback - it will happily emit an
executable with the wrong payload - so this reads the command back out of each
.exe and compares it against games.json.

    python scripts/verify.py                 # check the committed executables
    python scripts/verify.py --dir out       # check a fresh build
    python scripts/verify.py --strict        # also fail on legacy command forms

Without --strict, executables that launch the right game by an older command
form (there are several in the original hand-built set) pass with a note.
Strict mode also rejects packages that execute the dummy payload before the
real command, because IExpress's hidden-window setting would apply to the dummy
rather than the launcher command.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

STORE_DIRS = {
    "steam": "Steam",
    "battlenet": "BattleNet",
    "battlenetuid": "BattleNet",
    "epic": "Epic",
    "ubisoft": "Ubisoft",
}

# Every command dialect found in the hand-built executables, canonical first.
DIALECTS = [
    (re.compile(rb"steam://rungameid/(\d+)"), "steam", "canonical"),
    (re.compile(rb"-gameidlaunch (\d+)"), "steam", "legacy: steam.exe -gameidlaunch"),
    (re.compile(rb"steamapps\\common\\[^\x00]+"), "steam", "legacy: direct path to game binary"),
    (re.compile(rb"uplay://launch/(\d+)"), "ubisoft", "canonical"),
    (re.compile(rb"com\.epicgames\.launcher://apps/([A-Za-z0-9_]+)"), "epic", "canonical"),
    # Must precede the `launch` dialect only for readability - the two cannot
    # both match, since `launch ` requires the space that `launch_uid` fills.
    (re.compile(rb'--exec="launch_uid ([a-z0-9_]+)"'), "battlenetuid", "canonical"),
    (re.compile(rb'--exec="launch (\w+)"'), "battlenet", "canonical"),
]

HARDCODED_STEAM = re.compile(rb"Program Files \(x86\)\\Steam")
DUMMY_APP_LAUNCH = re.compile(rb"cmd(?:\.exe)? /c dummy\.bat", re.IGNORECASE)


def inspect(path: str) -> tuple[str | None, str | None, str]:
    """Return (store, id, note) for an IExpress launcher."""
    with open(path, "rb") as fh:
        data = fh.read()
    for pattern, store, note in DIALECTS:
        match = pattern.search(data)
        if not match:
            continue
        ident = match.group(1).decode() if match.groups() else None
        if store == "steam" and HARDCODED_STEAM.search(data):
            note += "; hardcoded Steam install path"
        if DUMMY_APP_LAUNCH.search(data):
            note += "; dummy payload is executed instead of using the hidden launch slot"
        return store, ident, note
    return None, None, "no recognisable launch command"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".", help="directory holding the store folders")
    ap.add_argument("--manifest", default="games.json")
    ap.add_argument(
        "--strict",
        action="store_true",
        help="fail on legacy commands or non-canonical package layout",
    )
    args = ap.parse_args()

    with open(args.manifest, encoding="utf-8") as fh:
        games = json.load(fh)

    failures, notes, missing = [], [], []
    expected = 0

    for game in games:
        for store, want_id in game["stores"].items():
            expected += 1
            if store not in STORE_DIRS:
                failures.append(f"{game['out']}: unknown store '{store}' in manifest")
                continue

            rel = os.path.join(STORE_DIRS[store], game["out"])
            path = os.path.join(args.dir, rel)
            if not os.path.exists(path):
                missing.append(rel)
                continue

            got_store, got_id, note = inspect(path)
            if got_store is None:
                failures.append(f"{rel}: {note}")
            elif got_store != store:
                failures.append(f"{rel}: should be {store}, binary launches via {got_store}")
            # The direct-path dialect carries no ID to compare against.
            elif got_id is not None and got_id != want_id:
                failures.append(f"{rel}: manifest says {want_id}, binary has {got_id}")
            elif note != "canonical":
                notes.append(f"{rel}: {note}")

    print(f"checked {expected - len(missing)}/{expected} launchers "
          f"across {len(games)} games in {os.path.abspath(args.dir)}")

    if missing:
        print(f"\nnot built yet ({len(missing)}): {', '.join(missing)}")
    if notes:
        print(f"\nnon-canonical packages ({len(notes)}):")
        for line in notes:
            print(f"  {line}")
    if failures:
        print(f"\nMISMATCHED ({len(failures)}):", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)
        return 1
    if notes and args.strict:
        print("\nnon-canonical packages present and --strict given", file=sys.stderr)
        return 1

    print("\nall good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
