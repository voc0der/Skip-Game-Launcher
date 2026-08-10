#!/usr/bin/env python3
"""Resolve a game request into a games.json entry.

Reads a GitHub issue-form body, works out whether we can actually build the
launcher, and either emits the new manifest entry or explains why not.

A game already in the manifest can still be requested for a second store - that
merges a new store ID into the existing entry rather than creating a duplicate,
and produces an extra executable in that store's folder.

Outputs are written to $GITHUB_OUTPUT when running under Actions:
    ok      - "true" / "false"
    out     - the exe filename        (only when ok)
    path    - Store/Name.exe          (only when ok)
    name    - the game's display name (only when ok)
    store   - the resolved store key  (only when ok)
    entry   - the resulting manifest entry as JSON (only when ok)
    comment - markdown to post back on the issue

Exit status is always 0; check the `ok` output. A non-zero exit means the
script itself broke, which is a different problem.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import unicodedata
import uuid

STORES = {
    "steam": "Steam",
    "battlenet": "Battle.net",
    "epic": "Epic Games",
    "ubisoft": "Ubisoft Connect",
}

STORE_DIRS = {
    "steam": "Steam",
    "battlenet": "BattleNet",
    "epic": "Epic",
    "ubisoft": "Ubisoft",
}

# Every ID is interpolated into a shell command that gets baked into a
# distributed .exe, so the charset is a security boundary, not cosmetics: a
# Battle.net code of `Fen" & calc & rem ` closes the quoted run that `cmd /s /c`
# leaves behind and executes whatever follows. Real IDs are plain identifiers.
ID_PATTERNS = {
    "steam": re.compile(r"\d+"),
    "ubisoft": re.compile(r"\d+"),
    "battlenet": re.compile(r"[A-Za-z0-9_]+"),   # Pro, D3, S2, WoW, VIPR
    "epic": re.compile(r"[A-Za-z0-9_]+"),        # Petunia, Speedwell, Calluna
}

ID_DESCRIPTIONS = {
    "steam": "numeric",
    "ubisoft": "numeric",
    "battlenet": "letters, digits and underscores only",
    "epic": "letters, digits and underscores only",
}

SAFE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.exe")
WINDOWS_DEVICE_NAME = re.compile(
    r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", re.IGNORECASE
)

# Steam and Epic have queryable public catalogues; Battle.net and Ubisoft don't,
# so those requests must carry the ID.
LOOKUP_CAPABLE = {"steam", "epic"}

SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
EPIC_CONTENT_URL = "https://store-content.ak.epicgames.com/api/en-US/content/products/{slug}"
UA = "Skip-Game-Launcher-CI (+https://github.com/voc0der/Skip-Game-Launcher)"

EPIC_MANUAL_HINT = (
    "If you own it, the app name is in the launcher's own manifests — open "
    "`C:\\ProgramData\\Epic\\EpicGamesLauncher\\Data\\Manifests\\`, open the `.item` "
    "files in a text editor and read `AppName` from the one whose `DisplayName` "
    "matches. Put that in the request and re-apply the label."
)


class Rejected(Exception):
    """We understood the request but can't build it."""


def parse_issue_form(body: str) -> dict[str, str]:
    """Turn a GitHub issue-form body into {heading: value}."""
    fields: dict[str, str] = {}
    # Issue forms render as '### Heading\n\nvalue'; unanswered optional fields
    # come through as the literal '_No response_'.
    for block in re.split(r"^###\s+", body.replace("\r\n", "\n"), flags=re.M)[1:]:
        heading, _, value = block.partition("\n")
        value = value.strip()
        if value == "_No response_":
            value = ""
        fields[heading.strip().lower()] = value
    return fields


def steam_search(term: str) -> list[dict]:
    url = SEARCH_URL + "?" + urllib.parse.urlencode(
        {"term": term, "l": "english", "cc": "US"}
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    return [i for i in data.get("items", []) if i.get("type") == "app"]


def normalise(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def resolve_steam_id(name: str) -> tuple[str, str]:
    """Return (appid, note). Raises Rejected when the match isn't confident."""
    try:
        items = steam_search(name)
    except Exception as exc:  # network/API wobble - a human can retry the label
        raise Rejected(
            f"Couldn't reach the Steam store API (`{exc}`). Re-apply the label to retry, "
            "or supply the App ID directly."
        ) from exc

    if not items:
        raise Rejected(
            f"No Steam app matched **{name}**. Double-check the spelling, or find the "
            "App ID on [SteamDB](https://steamdb.info) and put it in the request."
        )

    target = normalise(name)
    exact = [i for i in items if normalise(i["name"]) == target]
    if len(exact) == 1:
        return str(exact[0]["id"]), f"exact title match on **{exact[0]['name']}**"

    # Steam's search is fuzzy and loves returning DLC/soundtracks alongside the
    # base game, so anything short of a clean hit goes back to a human.
    if not exact and normalise(items[0]["name"]) == target:
        return str(items[0]["id"]), f"top match **{items[0]['name']}**"

    listing = "\n".join(f"- `{i['id']}` — {i['name']}" for i in items[:8])
    raise Rejected(
        f"**{name}** didn't match a single Steam app cleanly, so I'm not going to guess.\n\n"
        f"Candidates:\n{listing}\n\n"
        "Edit the issue to add the right App ID, then re-apply the label."
    )


def epic_slugify(name: str) -> str:
    s = name.lower().replace("'", "").replace("&", "and")
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s)).strip("-")


def epic_app_name(slug: str) -> str | None:
    """Read the launcher app name off an Epic store product page.

    The store slug and the launch-URI app name are unrelated strings -
    `metro-2033-redux` is served by `Petunia` - so this has to be looked up
    rather than derived. Epic populates the field inconsistently; plenty of
    products carry an empty one, which is why callers need a fallback.
    """
    url = EPIC_CONTENT_URL.format(slug=urllib.parse.quote(slug, safe=""))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)

    pages = data.get("pages") or []
    # The product's own page is the one slugged "home"; the rest are DLC and
    # edition sub-pages that carry their own (wrong) app names.
    ordered = sorted(pages, key=lambda p: p.get("_slug") != "home")
    for page in ordered:
        app = (page.get("item") or {}).get("appName")
        if app:
            return app
    return None


def resolve_epic_id(name: str, given: str) -> tuple[str, str]:
    """Return (appName, note). `given` may be a slug, a store URL, or empty."""
    if given:
        # A capitalised codename like "Petunia" is already what we want; a
        # lowercase-hyphenated string or a URL is a slug we still have to resolve.
        looks_like_slug = "/" in given or re.fullmatch(r"[a-z0-9][a-z0-9-]*", given)
        if not looks_like_slug:
            return given, "using the app name from the request"
        # Store links commonly carry locale/affiliate query parameters. Those
        # are not part of the product slug and make the content endpoint 404 if
        # they are copied through verbatim.
        slug = urllib.parse.urlsplit(given).path.rstrip("/").split("/")[-1]
        source = f"store slug `{slug}`"
    else:
        slug = epic_slugify(name)
        source = f"guessed store slug `{slug}`"

    try:
        app = epic_app_name(slug)
    except Exception as exc:
        raise Rejected(
            f"Couldn't read the Epic store page for `{slug}` (`{exc}`).\n\n" + EPIC_MANUAL_HINT
        ) from exc

    if not app:
        raise Rejected(
            f"Found the Epic store page for {source}, but it doesn't publish a launcher "
            f"app name — Epic leaves that field empty on a lot of products.\n\n"
            + EPIC_MANUAL_HINT
        )
    return app, f"read from Epic's {source}"


def derive_out_name(name: str) -> str:
    """Turn a display name into a filename, or return "" if nothing survives."""
    # Strip diacritics rather than dropping the letters outright, so
    # "Pokemon" beats "PokMon".
    folded = unicodedata.normalize("NFKD", name.replace("'", ""))
    folded = "".join(c for c in folded if not unicodedata.combining(c))

    # str.title() is wrong here twice over: it capitalises after an apostrophe
    # ("Assassin's" -> "Assassin'S") and it flattens roman numerals ("IV" -> "Iv").
    # Capitalise only words that arrived entirely lowercase.
    words = [w for w in re.split(r"[^A-Za-z0-9]+", folded) if w]
    stem = "".join(w.capitalize() if w.islower() else w for w in words)
    # A fully non-latin name leaves nothing behind; ".exe" would satisfy the
    # filename charset check and produce an unnameable file.
    return f"{stem}.exe" if stem else ""


def _id_hint(store: str) -> str:
    return {
        "battlenet": "Battle.net uses short product codes — `Pro` (Overwatch), `D3`, `S2`, `WoW`, `Hero`, `VIPR`, `DST2`, `W3`.",
        "epic": "Epic uses the internal app slug from the launcher URL, e.g. `Speedwell` for Metro Last Light Redux.",
        "ubisoft": "Ubisoft uses the numeric ID from `uplay://launch/<id>/0`.",
    }.get(store, "")


def launch_command(store: str, app_id: str) -> str:
    return {
        "steam": f"explorer steam://rungameid/{app_id}",
        "ubisoft": f"explorer uplay://launch/{app_id}/0",
        "epic": f'explorer "com.epicgames.launcher://apps/{app_id}?action=launch&silent=true"',
        "battlenet": f'cmd /s /c ""C:\\Program Files (x86)\\Battle.net\\Battle.net.exe" --exec="launch {app_id}""',
    }[store]


def single_line(value: str, label: str, limit: int = 120) -> str:
    """Reject multi-line or absurd values for fields the form renders as one line.

    The form widget is single-line, but the issue body is free text and can be
    edited afterwards, so nothing upstream guarantees this.
    """
    if "\n" in value or "\r" in value:
        raise Rejected(f"{label} must be a single line.")
    if len(value) > limit:
        raise Rejected(f"{label} is unreasonably long ({len(value)} characters).")
    return value


def resolve(fields: dict[str, str], games: list[dict]) -> dict:
    """Work out what to add. Returns a plan dict, or raises Rejected."""
    name = single_line(fields.get("game name", "").strip(), "Game name")
    if not name:
        raise Rejected("The request has no game name in it.")

    raw_store = single_line(fields.get("store", "").strip(), "Store", 40).lower()
    store = next(
        (key for key, label in STORES.items() if raw_store in (key, label.lower())),
        None,
    )
    if store is None:
        raise Rejected(
            f"Unrecognised store `{raw_store or '(blank)'}`. "
            f"Supported: {', '.join(STORES.values())}."
        )

    given_id = single_line(fields.get("app id / product code", "").strip(), "App ID", 200)
    if store == "epic":
        # Epic is the one store where a supplied value still needs resolving:
        # people naturally paste the store URL, which isn't the launch-URI name.
        app_id, note = resolve_epic_id(name, given_id)
    elif given_id:
        app_id, note = given_id, "using the ID from the request"
    elif store == "steam":
        app_id, note = resolve_steam_id(name)
    else:
        raise Rejected(
            f"{STORES[store]} has no public catalogue API, so the ID can't be looked up "
            "automatically. Edit the issue to supply it, then re-apply the label.\n\n"
            + _id_hint(store)
        )

    # Applied to every route in - supplied by the requester, or read back from
    # the Steam/Epic APIs - because this is what stops a crafted ID becoming a
    # command in the executable we ship.
    if not ID_PATTERNS[store].fullmatch(app_id):
        raise Rejected(
            f"`{app_id}` isn't a valid {STORES[store]} ID "
            f"({ID_DESCRIPTIONS[store]})."
        )

    # An existing game gaining a second store keeps its filename. Identity comes
    # from the name alone - letting a supplied filename select the game would
    # turn "add Foo, call it Portal2.exe" into a silent edit of Portal 2.
    existing = next(
        (g for g in games if normalise(g["name"]) == normalise(name)),
        None,
    )

    if existing is not None:
        out = existing["out"]
        if store in existing["stores"]:
            raise Rejected(
                f"**{existing['name']}** is already built for {STORES[store]} "
                f"(`{STORE_DIRS[store]}/{out}`, ID `{existing['stores'][store]}`)."
            )
        action = "merge"
    else:
        out = single_line(fields.get("output filename", "").strip(), "Output filename") or derive_out_name(name)
        if not out:
            raise Rejected(
                f"Couldn't work out a filename from **{name}** - it has no latin letters "
                "or digits. Put an explicit output filename in the request."
            )
        # Normalise the extension: the manifest checks expect a lowercase .exe,
        # so accepting "Foo.EXE" here would open a PR that fails its own tests.
        if out.lower().endswith(".exe"):
            out = out[: -len(".exe")] + ".exe"
        else:
            out += ".exe"
        if not SAFE_FILENAME.fullmatch(out) or WINDOWS_DEVICE_NAME.match(out):
            raise Rejected(
                f"`{out}` isn't a usable filename on Windows - start with a letter or digit "
                "and stick to letters, digits, dot, dash and underscore."
            )
        clash = next((g for g in games if g["out"].lower() == out.lower()), None)
        if clash is not None:
            raise Rejected(
                f"`{out}` is already taken by **{clash['name']}**. "
                "Pick a different output filename in the request."
            )
        action = "new"

    for game in games:
        if game.get("stores", {}).get(store) == app_id and game is not existing:
            raise Rejected(
                f"{STORES[store]} ID `{app_id}` is already used by **{game['name']}** "
                f"(`{STORE_DIRS[store]}/{game['out']}`)."
            )

    return {
        "name": existing["name"] if existing else name,
        "out": out,
        "store": store,
        "id": app_id,
        "action": action,
        "note": note,
    }


def apply_to_manifest(plan: dict, games: list[dict]) -> dict:
    """Insert or merge the plan into `games`, returning the affected entry."""
    for game in games:
        if game["out"].lower() == plan["out"].lower():
            game["stores"][plan["store"]] = plan["id"]
            game["stores"] = dict(sorted(game["stores"].items()))
            return game

    entry = {"name": plan["name"], "out": plan["out"], "stores": {plan["store"]: plan["id"]}}
    games.append(entry)
    games.sort(key=lambda g: g["out"].lower())
    return entry


def emit(**outputs: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        print(json.dumps(outputs, indent=2))
        return
    with open(path, "a", encoding="utf-8") as fh:
        for key, value in outputs.items():
            # Multi-line values need heredoc syntax in $GITHUB_OUTPUT. The
            # delimiter is randomised per call because these values quote text
            # from the issue: a fixed one lets a crafted body close the heredoc
            # early and append its own `key=value` lines, forging outputs the
            # workflow then trusts (`ok=true` being the interesting one).
            delim = f"__EOF_{key.upper()}_{uuid.uuid4().hex}__"
            if delim in str(value):  # unreachable in practice; refuse rather than emit
                raise RuntimeError(f"generated delimiter collided with {key} value")
            fh.write(f"{key}<<{delim}\n{value}\n{delim}\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--body", help="issue body; reads stdin when omitted")
    ap.add_argument("--manifest", default="games.json")
    ap.add_argument("--apply", action="store_true",
                    help="write the resolved entry into the manifest")
    args = ap.parse_args()

    body = args.body if args.body is not None else sys.stdin.read()
    with open(args.manifest, encoding="utf-8") as fh:
        games = json.load(fh)
    fields = parse_issue_form(body)

    try:
        plan = resolve(fields, games)
    except Rejected as exc:
        emit(ok="false", comment=f"### Can't build this one automatically\n\n{exc}")
        print(f"REJECTED: {exc}", file=sys.stderr)
        return 0

    entry = apply_to_manifest(plan, games)
    if args.apply:
        with open(args.manifest, "w", encoding="utf-8") as fh:
            json.dump(games, fh, indent=2, ensure_ascii=False)
            fh.write("\n")

    path = f"{STORE_DIRS[plan['store']]}/{plan['out']}"
    headline = (
        f"### Building `{path}`"
        if plan["action"] == "new"
        else f"### Adding a {STORES[plan['store']]} build of **{plan['name']}**"
    )
    extra = (
        ""
        if plan["action"] == "new"
        else f"\n**{plan['name']}** is already here for "
             f"{', '.join(STORES[s] for s in entry['stores'] if s != plan['store'])}; "
             f"this adds `{path}` alongside it.\n"
    )

    emit(
        ok="true",
        out=plan["out"],
        path=path,
        name=plan["name"],
        store=plan["store"],
        entry=json.dumps(entry, ensure_ascii=False),
        comment=(
            f"{headline}\n{extra}\n"
            f"Resolved to {STORES[plan['store']]} ID `{plan['id']}` ({plan['note']}).\n\n"
            f"Launch command:\n```\n{launch_command(plan['store'], plan['id'])}\n```\n\n"
            "Opening a PR with the manifest entry and the built executable."
        ),
    )
    print(f"OK ({plan['action']}): {entry}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
