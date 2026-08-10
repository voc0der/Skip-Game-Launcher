#!/usr/bin/env python3
"""Resolve a game request into every matching games.json store entry.

Reads a GitHub issue-form body, works out whether we can actually build the
launcher, and either emits the new manifest entry or explains why not.

The store selected on the issue is a seed, not the scope of the request. Once
that seed is resolved, every supported storefront is searched by title and all
confident matches are added in one pass. A game already in the manifest is
enriched the same way.

Outputs are written to $GITHUB_OUTPUT when running under Actions:
    ok      - "true" / "false"
    out     - the exe filename        (only when ok)
    paths   - newline-separated paths (only when ok)
    name    - the game's display name (only when ok)
    stores  - JSON array of new stores (only when ok)
    entry   - the resulting manifest entry as JSON (only when ok)
    comment - markdown to post back on the issue

Exit status is always 0; check the `ok` output. A non-zero exit means the
script itself broke, which is a different problem.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import unicodedata
import uuid
from html.parser import HTMLParser

STORES = {
    "steam": "Steam",
    "battlenet": "Battle.net",
    "battlenetuid": "Battle.net (game version)",
    "epic": "Epic Games",
    "ubisoft": "Ubisoft Connect",
}

STORE_DIRS = {
    "steam": "Steam",
    "battlenet": "BattleNet",
    # Same folder as `battlenet` - these are Battle.net games, they just need a
    # different launch verb. Entries never collide because a game version is its
    # own manifest entry with its own `out`.
    "battlenetuid": "BattleNet",
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
    "battlenetuid": re.compile(r"[a-z0-9_]+"),   # wow_classic_era, wowt
    "epic": re.compile(r"[A-Za-z0-9_]+"),        # Petunia, Speedwell, Calluna
}

ID_DESCRIPTIONS = {
    "steam": "numeric",
    "ubisoft": "numeric",
    "battlenet": "letters, digits and underscores only",
    "battlenetuid": "lowercase letters, digits and underscores only",
    "epic": "letters, digits and underscores only",
}

SAFE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.exe")
WINDOWS_DEVICE_NAME = re.compile(
    r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", re.IGNORECASE
)

# `resolve()` remains as the single-store compatibility API used by older
# callers. The workflow uses `resolve_all()`, whose catalog adapters cover all
# four stores.
LOOKUP_CAPABLE = {"steam", "epic"}

SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
EPIC_CONTENT_URL = "https://store-content.ak.epicgames.com/api/en-US/content/products/{slug}"
EPIC_SEARCH_URL = "https://api.egdata.app/search/v2/search?country=US"
EPIC_HYDRATE_URL = "https://api.egdata.app/catalog/hydrate"
UBISOFT_SEARCH_URL = "https://store.ubisoft.com/us/search"
BATTLE_NET_CATALOG_URL = (
    "https://raw.githubusercontent.com/playlite-app/playlite/main/"
    "src-tauri/src/data/battle_net_games.json"
)
UA = "Skip-Game-Launcher-CI (+https://github.com/voc0der/Skip-Game-Launcher)"

# A network fallback for the Battle.net titles already shipped by this repo.
# Battle.net's own web catalogue doesn't expose launcher product codes. The
# live catalogue above covers new releases; this keeps existing/common titles
# resolvable if GitHub is briefly unavailable.
BATTLE_NET_FALLBACK = {
    "Call of Duty: Black Ops 4": "VIPR",
    "Diablo II: Resurrected": "OSI",
    "Diablo III": "D3",
    "Diablo IV": "Fen",
    "Hearthstone": "WTCG",
    "Heroes of the Storm": "Hero",
    "Overwatch": "Pro",
    "Overwatch 2": "Pro",
    "StarCraft": "S1",
    "StarCraft II": "S2",
    "Warcraft III": "W3",
    "Warcraft III: Reforged": "W3",
    "World of Warcraft": "WoW",
}

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


def catalogue_title_key(s: str) -> str:
    """Normalise a store title while ignoring base-edition boilerplate."""
    value = html.unescape(s).replace("®", "").replace("™", "").strip()
    value = re.sub(r"\s*[-–—:]?\s*standard edition\s*$", "", value, flags=re.I)
    return normalise(value)


def read_json_url(url: str) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def read_text_url(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def post_json(url: str, payload: object) -> bytes:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"User-Agent": UA, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


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
        # App names are not always pretty codenames: current records can use a
        # lowercase word ("nonagon") or an opaque 32-character artifact ID.
        # Treat only URLs and unmistakably hyphenated product slugs as values
        # that still need resolving.
        looks_like_slug = "/" in given or "-" in given
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


def epic_catalog_app_name(name: str) -> tuple[str, str] | None:
    """Resolve Epic's Windows launch artifact from its current catalogue.

    Epic's public product-page endpoint often omits ``appName``. EGData mirrors
    the Epic catalogue graph, including the Windows asset/release-app records
    that contain the value accepted by Epic's launch URI. Only an exact base
    game title and an exact executable item title are accepted.
    """
    search = json.loads(
        post_json(EPIC_SEARCH_URL, {"title": name, "page": 1, "limit": 30})
    )
    target = catalogue_title_key(name)
    offers = [
        offer
        for offer in search.get("offers", [])
        if offer.get("offerType") == "BASE_GAME"
        and catalogue_title_key(str(offer.get("title", ""))) == target
    ]
    if not offers:
        return None

    identifiers: list[dict[str, str]] = []
    seen_items: set[tuple[str, str]] = set()
    for offer in offers:
        for item in offer.get("items") or []:
            if not isinstance(item, dict):
                continue
            key = (str(item.get("namespace", "")), str(item.get("id", "")))
            if not all(key) or key in seen_items:
                continue
            seen_items.add(key)
            identifiers.append({"type": "item", "namespace": key[0], "id": key[1]})

    if not identifiers:
        return None
    # The hydrate endpoint accepts at most 25 roots per request. Exact base-game
    # results are normally one or two roots, but cap explicitly rather than
    # letting a polluted search result make the request fail.
    raw = post_json(
        EPIC_HYDRATE_URL,
        {
            "schemaVersion": 2,
            "identifiers": identifiers[:25],
            "knownRoots": [],
            "knownRecords": [],
        },
    ).decode("utf-8", errors="replace")
    records: list[dict] = []
    for line in raw.splitlines():
        if line.strip():
            records.extend(
                row.get("record", {})
                for row in json.loads(line).get("records", [])
                if isinstance(row, dict)
            )

    items = {
        (str(record.get("namespace", "")), str(record.get("id", ""))): record
        for record in records
        if record.get("type") == "item"
    }
    offer_ids = {str(offer.get("id", "")) for offer in offers}
    matches: set[str] = set()
    for record in records:
        if record.get("type") not in {"asset", "release-app"}:
            continue
        if str(record.get("platform", "")).lower() != "windows":
            continue
        if str(record.get("primaryOfferId", "")) not in offer_ids:
            continue
        item = items.get(
            (str(record.get("itemNamespace", "")), str(record.get("itemId", ""))),
            {},
        )
        categories = {str(c).lower() for c in item.get("categories") or []}
        if item.get("entitlementType") != "EXECUTABLE" or "games" not in categories:
            continue
        if "digitalextras" in categories or "addons" in categories:
            continue
        if catalogue_title_key(str(item.get("title", ""))) != target:
            continue
        app = str(record.get("artifactId") or record.get("appId") or "")
        if app:
            matches.add(app)

    if len(matches) == 1:
        return matches.pop(), "exact Windows game match in Epic's catalogue"
    return None


class _UbisoftSearchParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.products: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        values = {key.lower(): value or "" for key, value in attrs}
        if "thumb-link" not in values.get("class", "").split():
            return
        prefix = "Go to product:"
        title = values.get("title", "")
        href = values.get("href", "")
        if title.startswith(prefix) and href:
            self.products.append((html.unescape(title[len(prefix):].strip()), href))


def resolve_ubisoft_id(name: str) -> tuple[str, str] | None:
    """Search Ubisoft's own storefront and read uplayGameID from an exact hit."""
    url = UBISOFT_SEARCH_URL + "?" + urllib.parse.urlencode({"q": name})
    parser = _UbisoftSearchParser()
    parser.feed(read_text_url(url))
    target = catalogue_title_key(name)
    links: list[str] = []
    for title, href in parser.products:
        if catalogue_title_key(title) == target:
            absolute = urllib.parse.urljoin("https://store.ubisoft.com", href)
            if absolute not in links:
                links.append(absolute)

    ids: set[str] = set()
    for link in links:
        product = read_text_url(link)
        ids.update(re.findall(r'"uplayGameID"\s*:\s*"(\d+)"', product))
    if len(ids) == 1:
        return ids.pop(), "exact title match on Ubisoft's store"
    return None


def resolve_battlenet_id(name: str) -> tuple[str, str] | None:
    """Resolve a Battle.net title from a maintained launcher product catalogue."""
    target = catalogue_title_key(name)
    catalogue: list[dict] = []
    try:
        payload = read_json_url(BATTLE_NET_CATALOG_URL)
        if isinstance(payload, list):
            catalogue = [row for row in payload if isinstance(row, dict)]
    except Exception:
        # Known titles remain available when the live catalogue is unreachable.
        pass

    matches = {
        str(row.get("ProductId", ""))
        for row in catalogue
        if catalogue_title_key(str(row.get("Name", ""))) == target
    }
    matches.update(
        product
        for title, product in BATTLE_NET_FALLBACK.items()
        if catalogue_title_key(title) == target
    )
    if len(matches) == 1:
        return matches.pop(), "exact title match in the Battle.net product catalogue"
    return None


def discover_store_id(store: str, name: str) -> tuple[str, str] | None:
    """Return a confident exact catalogue match, or None when not sold there."""
    if store == "steam":
        try:
            return resolve_steam_id(name)
        except Rejected:
            return None
    if store == "epic":
        try:
            found = epic_catalog_app_name(name)
        except Exception:
            found = None
        if found:
            return found
        try:
            return resolve_epic_id(name, "")
        except Rejected:
            return None
    if store == "ubisoft":
        try:
            return resolve_ubisoft_id(name)
        except Exception:
            return None
    if store == "battlenet":
        return resolve_battlenet_id(name)
    if store == "battlenetuid":
        # Game versions (WoW Classic, Classic Era, PTR) are not products in any
        # catalogue - they only exist as uids inside an installed client, so
        # there is nothing to search. These are added by hand in a PR.
        return None
    raise AssertionError(f"unknown store {store}")


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
        "battlenetuid": f'cmd /s /c ""C:\\Program Files (x86)\\Battle.net\\Battle.net.exe" --exec="launch_uid {app_id}""',
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


def _request_identity(fields: dict[str, str], games: list[dict]) -> tuple[str, str, str, dict | None]:
    """Validate the request fields shared by automatic multi-store resolution."""
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

    existing = next(
        (game for game in games if normalise(game["name"]) == normalise(name)),
        None,
    )
    if existing is not None:
        return existing["name"], existing["out"], store, existing

    out = (
        single_line(fields.get("output filename", "").strip(), "Output filename")
        or derive_out_name(name)
    )
    if not out:
        raise Rejected(
            f"Couldn't work out a filename from **{name}** - it has no latin letters "
            "or digits. Put an explicit output filename in the request."
        )
    if out.lower().endswith(".exe"):
        out = out[:-4] + ".exe"
    else:
        out += ".exe"
    if not SAFE_FILENAME.fullmatch(out) or WINDOWS_DEVICE_NAME.match(out):
        raise Rejected(
            f"`{out}` isn't a usable filename on Windows - start with a letter or digit "
            "and stick to letters, digits, dot, dash and underscore."
        )
    clash = next((game for game in games if game["out"].lower() == out.lower()), None)
    if clash is not None:
        raise Rejected(
            f"`{out}` is already taken by **{clash['name']}**. "
            "Pick a different output filename in the request."
        )
    return name, out, store, None


def _validate_store_id(store: str, app_id: str) -> None:
    if not ID_PATTERNS[store].fullmatch(app_id):
        raise Rejected(
            f"`{app_id}` isn't a valid {STORES[store]} ID "
            f"({ID_DESCRIPTIONS[store]})."
        )


def _validate_supplied_id(store: str, name: str, supplied_id: str) -> str:
    """Require a requester-supplied ID to agree with an independent lookup."""
    match = discover_store_id(store, name)
    if match is None:
        raise Rejected(
            f"Couldn't independently verify {STORES[store]} ID `{supplied_id}` against "
            f"an exact catalogue match for **{name}**. The supplied ID will not be trusted."
        )
    expected_id, lookup_note = match
    _validate_store_id(store, expected_id)
    if supplied_id != expected_id:
        raise Rejected(
            f"The exact {STORES[store]} catalogue match for **{name}** uses ID "
            f"`{expected_id}`, not the supplied ID `{supplied_id}`."
        )
    return f"request ID independently confirmed by {lookup_note}"


def resolve_all(fields: dict[str, str], games: list[dict]) -> dict:
    """Resolve the request seed, then discover every other supported store."""
    name, out, requested_store, existing = _request_identity(fields, games)
    given_id = single_line(fields.get("app id / product code", "").strip(), "App ID", 200)
    current = dict(existing.get("stores", {})) if existing else {}
    found: dict[str, tuple[str, str]] = {}

    # The issue's selected store anchors a new title. For an existing title its
    # manifest entry is already a trustworthy anchor, so even a request that
    # selects an existing store can trigger discovery of missing stores.
    if given_id:
        if requested_store == "epic":
            seed_id, seed_note = resolve_epic_id(name, given_id)
        else:
            seed_id, seed_note = given_id, "using the ID from the request"
        _validate_store_id(requested_store, seed_id)
        seed_note = _validate_supplied_id(requested_store, name, seed_id)
        if requested_store in current and current[requested_store] != seed_id:
            raise Rejected(
                f"**{name}** already has {STORES[requested_store]} ID "
                f"`{current[requested_store]}`; the request supplied conflicting ID `{seed_id}`."
            )
        found[requested_store] = (seed_id, seed_note)
    elif requested_store not in current:
        seed = discover_store_id(requested_store, name)
        if seed is None:
            raise Rejected(
                f"Couldn't find one exact {STORES[requested_store]} catalogue match for "
                f"**{name}**. Correct the store title and retry."
            )
        found[requested_store] = seed

    for store in STORES:
        if store == requested_store or store in current:
            continue
        match = discover_store_id(store, name)
        if match is not None:
            found[store] = match

    additions: dict[str, str] = {}
    notes: dict[str, str] = {}
    for store, (app_id, note) in found.items():
        _validate_store_id(store, app_id)
        if store in current:
            continue
        for game in games:
            if game is not existing and game.get("stores", {}).get(store) == app_id:
                raise Rejected(
                    f"{STORES[store]} ID `{app_id}` is already used by **{game['name']}** "
                    f"(`{STORE_DIRS[store]}/{game['out']}`)."
                )
        additions[store] = app_id
        notes[store] = note

    if not additions:
        stores = ", ".join(STORES[store] for store in current)
        raise Rejected(
            f"**{name}** is already built for every exact store match found"
            f"{f' ({stores})' if stores else ''}."
        )

    return {
        "name": name,
        "out": out,
        "ids": dict(sorted(additions.items())),
        "notes": notes,
        "action": "merge" if existing else "new",
    }


def apply_all_to_manifest(plan: dict, games: list[dict]) -> dict:
    """Apply every store mapping in a multi-store plan."""
    for game in games:
        if game["out"].lower() == plan["out"].lower():
            game["stores"].update(plan["ids"])
            game["stores"] = dict(sorted(game["stores"].items()))
            return game

    entry = {
        "name": plan["name"],
        "out": plan["out"],
        "stores": dict(sorted(plan["ids"].items())),
    }
    games.append(entry)
    games.sort(key=lambda game: game["out"].lower())
    return entry


def emit(**outputs: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        print(json.dumps(outputs, indent=2))
        return
    # GitHub preserves carriage returns inside multiline output values. Force
    # LF even on the Windows runner, otherwise every path except the last one
    # reaches Bash as `Store/Game.exe\r` and cannot be staged.
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
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
        plan = resolve_all(fields, games)
    except Rejected as exc:
        emit(ok="false", comment=f"### Can't build this one automatically\n\n{exc}")
        print(f"REJECTED: {exc}", file=sys.stderr)
        return 0

    entry = apply_all_to_manifest(plan, games)
    if args.apply:
        with open(args.manifest, "w", encoding="utf-8") as fh:
            json.dump(games, fh, indent=2, ensure_ascii=False)
            fh.write("\n")

    new_stores = list(plan["ids"])
    paths = [f"{STORE_DIRS[store]}/{plan['out']}" for store in new_stores]
    rows = "\n".join(
        f"| {STORES[store]} | `{plan['ids'][store]}` | `{paths[index]}` |"
        for index, store in enumerate(new_stores)
    )
    commands = "\n".join(
        f"{STORES[store]}: `{launch_command(store, plan['ids'][store])}`"
        for store in new_stores
    )
    headline = (
        f"### Building **{plan['name']}** for every matched store"
        if plan["action"] == "new"
        else f"### Adding every missing store build of **{plan['name']}**"
    )

    emit(
        ok="true",
        out=plan["out"],
        paths="\n".join(paths),
        name=plan["name"],
        stores=json.dumps(new_stores),
        entry=json.dumps(entry, ensure_ascii=False),
        comment=(
            f"{headline}\n\n"
            "| Store | Resolved ID | File |\n|---|---|---|\n"
            f"{rows}\n\n"
            f"Launch commands:\n\n{commands}\n\n"
            "Opening one PR with the complete manifest entry and all matched executables."
        ),
    )
    print(f"OK ({plan['action']}): {entry}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
