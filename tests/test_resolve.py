"""Tests for scripts/resolve.py - the gate deciding whether CI can build a request."""
from __future__ import annotations

import json
import re
import urllib.parse

import pytest
from helpers import (
    NOT_FOUND,
    epic_product,
    fake_urlopen,
    steam_catalogue,
    steam_results,
    urlopen_raises,
)


# --- issue form parsing -----------------------------------------------------

def test_parses_headings_and_values(resolve_mod):
    body = "### Game name\n\nPortal 2\n\n### Store\n\nSteam\n"
    assert resolve_mod.parse_issue_form(body) == {"game name": "Portal 2", "store": "Steam"}


def test_treats_no_response_as_blank(resolve_mod):
    fields = resolve_mod.parse_issue_form("### App ID / product code\n\n_No response_\n")
    assert fields["app id / product code"] == ""


def test_handles_crlf(resolve_mod):
    """GitHub delivers issue bodies with CRLF; splitting on \\n alone strands \\r."""
    fields = resolve_mod.parse_issue_form("### Game name\r\n\r\nPortal 2\r\n")
    assert fields["game name"] == "Portal 2"


def test_ignores_preamble_before_first_heading(resolve_mod):
    fields = resolve_mod.parse_issue_form("chatter\n\n### Game name\n\nPortal 2\n")
    assert fields == {"game name": "Portal 2"}


# --- store normalisation ----------------------------------------------------

@pytest.mark.parametrize(
    "given,expected",
    [
        ("Steam", "steam"),
        ("steam", "steam"),
        ("Battle.net", "battlenet"),
        ("battlenet", "battlenet"),
        ("Epic Games", "epic"),
        ("Ubisoft Connect", "ubisoft"),
    ],
)
def test_accepts_store_labels_and_keys(resolve_mod, form, games, given, expected):
    # Epic treats a lowercase/numeric value as a slug and resolves it, so the
    # lookup is stubbed; the other stores never reach the network here.
    with fake_urlopen(lambda url: epic_product(("home", "Zed"))):
        plan = resolve_mod.resolve(
            resolve_mod.parse_issue_form(form(name="X", store=given, app_id="123")), games
        )
    assert plan["store"] == expected


def test_rejects_unknown_store(resolve_mod, form, games):
    with pytest.raises(resolve_mod.Rejected, match="Unrecognised store"):
        resolve_mod.resolve(resolve_mod.parse_issue_form(form(store="GOG", app_id="1")), games)


def test_rejects_missing_name(resolve_mod, form, games):
    with pytest.raises(resolve_mod.Rejected, match="no game name"):
        resolve_mod.resolve(resolve_mod.parse_issue_form(form(name="", app_id="1")), games)


# --- Steam lookup -----------------------------------------------------------

def test_steam_exact_match(resolve_mod, form, games):
    with fake_urlopen(lambda url: steam_results((1145360, "Hades"), (99, "Hades II"))):
        plan = resolve_mod.resolve(resolve_mod.parse_issue_form(form(name="Hades")), games)
    assert plan["id"] == "1145360"


def test_steam_match_ignores_punctuation_and_case(resolve_mod, form, games):
    with fake_urlopen(lambda url: steam_results((7, "Tom Clancy's Ghost Recon"))):
        plan = resolve_mod.resolve(
            resolve_mod.parse_issue_form(form(name="tom clancys ghost recon")), games
        )
    assert plan["id"] == "7"


def test_steam_ambiguous_is_rejected_with_candidates(resolve_mod, form, games):
    """Steam's search returns DLC alongside base games; guessing would ship a broken exe."""
    payload = steam_results(
        (412020, "Metro Exodus"), (286690, "Metro 2033 Redux"), (287980, "Mini Metro")
    )
    with fake_urlopen(lambda url: payload):
        with pytest.raises(resolve_mod.Rejected) as exc:
            resolve_mod.resolve(resolve_mod.parse_issue_form(form(name="Metro")), games)
    assert "412020" in str(exc.value) and "Mini Metro" in str(exc.value)


def test_steam_query_drops_standalone_dash(resolve_mod):
    """Steam reads a bare "-" as NOT, so an exact title self-excludes (#448)."""
    seen = []
    with fake_urlopen(lambda url: seen.append(url) or steam_results((814380, "S"))):
        resolve_mod.steam_search("Sekiro™: Shadows Die Twice - GOTY Edition")
    assert "+-+" not in seen[0]
    assert "GOTY" in seen[0]


@pytest.mark.parametrize("dash", ["-", "–", "—"])
def test_steam_resolves_title_containing_dash(resolve_mod, form, games, dash):
    """The catalogue title CI is told to use must not defeat the lookup (#380, #448)."""
    title = f"Sekiro™: Shadows Die Twice {dash} GOTY Edition"
    with fake_urlopen(steam_catalogue((814380, "Sekiro™: Shadows Die Twice - GOTY Edition"))):
        plan = resolve_mod.resolve(resolve_mod.parse_issue_form(form(name=title)), games)
    assert plan["id"] == "814380"


def test_steam_keeps_hyphens_inside_words(resolve_mod):
    """Only the separator token is noise; "Spider-Man" must stay intact."""
    seen = []
    with fake_urlopen(lambda url: seen.append(url) or steam_results((1817070, "S"))):
        resolve_mod.steam_search("Marvel's Spider-Man Remastered")
    assert "Spider-Man" in urllib.parse.unquote_plus(seen[0])


def test_steam_no_results_is_rejected(resolve_mod, form, games):
    with fake_urlopen(lambda url: {"items": []}):
        with pytest.raises(resolve_mod.Rejected, match="No Steam app matched"):
            resolve_mod.resolve(resolve_mod.parse_issue_form(form(name="Nonsuch")), games)


def test_steam_network_failure_is_rejected_not_raised(resolve_mod, form, games):
    """A flaky API should comment on the issue, not crash the workflow."""
    with urlopen_raises(NOT_FOUND):
        with pytest.raises(resolve_mod.Rejected, match="Couldn't reach the Steam store API"):
            resolve_mod.resolve(resolve_mod.parse_issue_form(form(name="Hades")), games)


def test_steam_rejects_non_numeric_id(resolve_mod, form, games):
    with pytest.raises(resolve_mod.Rejected, match="numeric"):
        resolve_mod.resolve(
            resolve_mod.parse_issue_form(form(name="X", store="Steam", app_id="Petunia")), games
        )


def test_steam_skips_lookup_when_id_supplied(resolve_mod, form, games):
    with urlopen_raises(AssertionError("must not hit the network")):
        plan = resolve_mod.resolve(
            resolve_mod.parse_issue_form(form(name="X", app_id="12345")), games
        )
    assert plan["id"] == "12345"


# --- Epic lookup ------------------------------------------------------------

def test_epic_resolves_slug_to_app_name(resolve_mod, form, games):
    """The store slug is not the launch-URI name: metro-2033-redux serves Petunia."""
    with fake_urlopen(lambda url: epic_product(("home", "Boga"))):
        plan = resolve_mod.resolve(
            resolve_mod.parse_issue_form(form(name="Death Stranding", store="Epic Games")), games
        )
    assert plan["id"] == "Boga"


def test_epic_prefers_home_page_over_dlc_pages(resolve_mod, form, games):
    """Regression: page[0] is often a DLC sub-page carrying its own wrong app name."""
    payload = epic_product(("awe", "WrongDlcName"), ("season-pass", "AlsoWrong"), ("home", "Calluna"))
    with fake_urlopen(lambda url: payload):
        plan = resolve_mod.resolve(
            resolve_mod.parse_issue_form(form(name="Control", store="Epic Games")), games
        )
    assert plan["id"] == "Calluna"


def test_epic_falls_back_to_any_page_with_a_name(resolve_mod, form, games):
    with fake_urlopen(lambda url: epic_product(("ultimate-edition", "Onyx"))):
        plan = resolve_mod.resolve(
            resolve_mod.parse_issue_form(form(name="Some Game", store="Epic Games")), games
        )
    assert plan["id"] == "Onyx"


def test_epic_empty_app_name_is_rejected(resolve_mod, form, games):
    """Epic leaves the field blank on many products - that must not become an empty id."""
    with fake_urlopen(lambda url: epic_product(("home", ""))):
        with pytest.raises(resolve_mod.Rejected, match="doesn't publish a launcher app name"):
            resolve_mod.resolve(
                resolve_mod.parse_issue_form(form(name="Alan Wake 2", store="Epic Games")), games
            )


def test_epic_accepts_store_url(resolve_mod, form, games):
    seen = {}

    def handler(url):
        seen["url"] = url
        return epic_product(("home", "Wren"))

    with fake_urlopen(handler):
        plan = resolve_mod.resolve(
            resolve_mod.parse_issue_form(
                form(name="Alan Wake 2", store="Epic Games",
                     app_id="https://store.epicgames.com/en-US/p/alan-wake-2")
            ),
            games,
        )
    assert plan["id"] == "Wren"
    assert seen["url"].endswith("alan-wake-2")


def test_epic_strips_query_and_fragment_from_store_url(resolve_mod, form, games):
    seen = {}

    def handler(url):
        seen["url"] = url
        return epic_product(("home", "Wren"))

    with fake_urlopen(handler):
        plan = resolve_mod.resolve(
            resolve_mod.parse_issue_form(
                form(
                    name="Alan Wake 2",
                    store="Epic Games",
                    app_id="https://store.epicgames.com/en-US/p/alan-wake-2?lang=en-US#details",
                )
            ),
            games,
        )
    assert plan["id"] == "Wren"
    assert seen["url"].endswith("alan-wake-2")


def test_epic_passes_through_explicit_codename(resolve_mod, form, games):
    """A capitalised codename is already the answer and must not be re-resolved."""
    with urlopen_raises(AssertionError("must not hit the network")):
        plan = resolve_mod.resolve(
            resolve_mod.parse_issue_form(
                form(name="Alan Wake 2", store="Epic Games", app_id="Wren")
            ),
            games,
        )
    assert plan["id"] == "Wren"


@pytest.mark.parametrize("app_id", ["nonagon", "592c359fb0e0413fb46dee2d24448eb4"])
def test_epic_passes_through_modern_artifact_ids(resolve_mod, form, games, app_id):
    """Epic now uses lowercase and opaque IDs as well as title-cased codenames."""
    with urlopen_raises(AssertionError("must not hit the network")):
        plan = resolve_mod.resolve(
            resolve_mod.parse_issue_form(
                form(name="Fresh Game", store="Epic Games", app_id=app_id)
            ),
            games,
        )
    assert plan["id"] == app_id


def test_epic_network_failure_is_rejected(resolve_mod, form, games):
    with urlopen_raises(NOT_FOUND):
        with pytest.raises(resolve_mod.Rejected, match="Couldn't read the Epic store page"):
            resolve_mod.resolve(
                resolve_mod.parse_issue_form(form(name="Nonsuch", store="Epic Games")), games
            )


@pytest.mark.parametrize(
    "name,slug",
    [
        ("Metro 2033 Redux", "metro-2033-redux"),
        ("Assassin's Creed Valhalla", "assassins-creed-valhalla"),
        ("Tom Clancy's Rainbow Six: Siege", "tom-clancys-rainbow-six-siege"),
        ("Sam & Max", "sam-and-max"),
    ],
)
def test_epic_slugify(resolve_mod, name, slug):
    assert resolve_mod.epic_slugify(name) == slug


def test_epic_catalog_resolves_exact_windows_game_item(resolve_mod, monkeypatch):
    offer = {
        "title": "Cyberpunk 2077",
        "offerType": "BASE_GAME",
        "id": "offer-1",
        "items": [{"namespace": "ns", "id": "root-item"}],
    }
    hydrated = {
        "records": [
            {
                "record": {
                    "type": "item",
                    "namespace": "ns",
                    "id": "game-item",
                    "title": "Cyberpunk 2077",
                    "entitlementType": "EXECUTABLE",
                    "categories": ["applications", "games"],
                }
            },
            {
                "record": {
                    "type": "item",
                    "namespace": "ns",
                    "id": "bonus-item",
                    "title": "Cyberpunk 2077 Bonus Content",
                    "entitlementType": "EXECUTABLE",
                    "categories": ["applications", "games"],
                }
            },
            {
                "record": {
                    "type": "release-app",
                    "platform": "Windows",
                    "primaryOfferId": "offer-1",
                    "itemNamespace": "ns",
                    "itemId": "game-item",
                    "appId": "Ginger",
                }
            },
            {
                "record": {
                    "type": "asset",
                    "platform": "Windows",
                    "primaryOfferId": "offer-1",
                    "itemNamespace": "ns",
                    "itemId": "bonus-item",
                    "artifactId": "WrongBonus",
                }
            },
        ]
    }

    def post(url, payload):
        if url == resolve_mod.EPIC_SEARCH_URL:
            return json.dumps({"offers": [offer]}).encode()
        return (json.dumps(hydrated) + "\n").encode()

    monkeypatch.setattr(resolve_mod, "post_json", post)
    assert resolve_mod.epic_catalog_app_name("Cyberpunk 2077")[0] == "Ginger"


def test_ubisoft_catalog_reads_uplay_id_from_exact_product(resolve_mod, monkeypatch):
    search = '''
      <a class="thumb-link" href="/us/far-cry-5/base.html"
         title="Go to product: Far Cry 5"></a>
      <a class="thumb-link" href="/us/far-cry-5-gold/gold.html"
         title="Go to product: Far Cry 5 Gold Edition"></a>
    '''

    def read(url):
        return search if "search?" in url else '{"uplayGameID":"1803"}'

    monkeypatch.setattr(resolve_mod, "read_text_url", read)
    assert resolve_mod.resolve_ubisoft_id("Far Cry 5")[0] == "1803"


def test_battlenet_catalog_matches_title_not_position(resolve_mod, monkeypatch):
    monkeypatch.setattr(
        resolve_mod,
        "read_json_url",
        lambda url: [
            {"ProductId": "Pro", "Name": "Overwatch 2"},
            {"ProductId": "Fen", "Name": "Diablo IV"},
        ],
    )
    assert resolve_mod.resolve_battlenet_id("Diablo IV")[0] == "Fen"


# --- stores without a catalogue ---------------------------------------------

@pytest.mark.parametrize("store", ["Battle.net", "Ubisoft Connect"])
def test_uncatalogued_stores_require_an_id(resolve_mod, form, games, store):
    with pytest.raises(resolve_mod.Rejected, match="no public catalogue API"):
        resolve_mod.resolve(resolve_mod.parse_issue_form(form(name="X", store=store)), games)


def test_battlenet_accepts_product_code(resolve_mod, form, games):
    plan = resolve_mod.resolve(
        resolve_mod.parse_issue_form(form(name="Diablo IV", store="Battle.net", app_id="Fen")),
        games,
    )
    assert (plan["store"], plan["id"], plan["out"]) == ("battlenet", "Fen", "DiabloIV.exe")


def test_ubisoft_rejects_non_numeric_id(resolve_mod, form, games):
    with pytest.raises(resolve_mod.Rejected, match="numeric"):
        resolve_mod.resolve(
            resolve_mod.parse_issue_form(
                form(name="X", store="Ubisoft Connect", app_id="not-a-number")
            ),
            games,
        )


# --- duplicate and merge handling -------------------------------------------

def test_rejects_game_already_built_for_that_store(resolve_mod, form, games):
    with pytest.raises(resolve_mod.Rejected, match="already built for Steam"):
        resolve_mod.resolve(
            resolve_mod.parse_issue_form(form(name="Portal 2", app_id="620")), games
        )


def test_merges_a_second_store_into_an_existing_game(resolve_mod, form, games):
    plan = resolve_mod.resolve(
        resolve_mod.parse_issue_form(form(name="Overwatch", store="Steam", app_id="2357570")),
        games,
    )
    assert plan["action"] == "merge"
    assert plan["out"] == "Overwatch.exe"


def test_merge_matches_name_ignoring_punctuation(resolve_mod, form, games):
    plan = resolve_mod.resolve(
        resolve_mod.parse_issue_form(form(name="portal 2!", store="Battle.net", app_id="X")),
        games,
    )
    assert plan["action"] == "merge" and plan["out"] == "Portal2.exe"


def test_rejects_filename_collision_with_a_different_game(resolve_mod, form, games):
    with pytest.raises(resolve_mod.Rejected, match="already taken by"):
        resolve_mod.resolve(
            resolve_mod.parse_issue_form(
                form(name="Something Else", app_id="1", filename="Portal2.exe")
            ),
            games,
        )


def test_rejects_id_already_used_by_another_game(resolve_mod, form, games):
    """Two entries pointing at one app would build two identical executables."""
    with pytest.raises(resolve_mod.Rejected, match="already used by"):
        resolve_mod.resolve(
            resolve_mod.parse_issue_form(form(name="Portal Two", app_id="620")), games
        )


# --- id charset -------------------------------------------------------------

@pytest.mark.parametrize(
    "store,evil",
    [
        ("Battle.net", 'Fen" & start calc & rem '),
        ("Battle.net", "Pro`whoami`"),
        ("Battle.net", "Pro&calc"),
        ("Epic Games", 'Petunia" onerror="x'),
        ("Epic Games", "Petunia&calc"),
    ],
)
def test_rejects_ids_that_would_inject_into_the_launch_command(
    resolve_mod, form, games, store, evil
):
    """The ID is interpolated into a shell command baked into a shipped .exe.

    `cmd /s /c` strips the outer quote pair, so a Battle.net code carrying a
    quote plus `&` escapes the quoted run and executes arbitrary commands on
    every machine that runs the launcher.
    """
    with pytest.raises(resolve_mod.Rejected, match="isn't a valid"):
        resolve_mod.resolve(
            resolve_mod.parse_issue_form(form(name="Evil", store=store, app_id=evil)), games
        )


@pytest.mark.parametrize(
    "store,app_id",
    [("Battle.net", "VIPR"), ("Battle.net", "D3"), ("Battle.net", "WoW"),
     ("Epic Games", "Calluna"), ("Epic Games", "Boga")],
)
def test_accepts_real_world_ids(resolve_mod, form, games, store, app_id):
    plan = resolve_mod.resolve(
        resolve_mod.parse_issue_form(form(name="Fresh Game", store=store, app_id=app_id)), games
    )
    assert plan["id"] == app_id


def test_validates_ids_that_came_from_the_api_too(resolve_mod, form, games):
    """Defence in depth: a hostile or confused API response is still untrusted."""
    with fake_urlopen(lambda url: epic_product(("home", 'Evil" & calc'))):
        with pytest.raises(resolve_mod.Rejected, match="isn't a valid"):
            resolve_mod.resolve(
                resolve_mod.parse_issue_form(form(name="Fresh Game", store="Epic Games")), games
            )


# --- filenames --------------------------------------------------------------

@pytest.mark.parametrize(
    "name,out",
    [
        ("Portal 2", "Portal2.exe"),
        ("Assassin's Creed", "AssassinsCreed.exe"),
        ("It Takes Two", "ItTakesTwo.exe"),
        ("Warcraft III", "WarcraftIII.exe"),
        ("Tools Up!", "ToolsUp.exe"),
        ("portal 2", "Portal2.exe"),
    ],
)
def test_derives_output_filename(resolve_mod, name, out):
    assert resolve_mod.derive_out_name(name) == out


@pytest.mark.parametrize("name", ["巨影都市", "!!!", "———"])
def test_rejects_names_that_yield_no_filename(resolve_mod, form, games, name):
    """A name with no latin characters used to derive the filename ".exe",
    which passes the charset check and produces an unnameable file."""
    with pytest.raises(resolve_mod.Rejected, match="Couldn't work out a filename"):
        resolve_mod.resolve(
            resolve_mod.parse_issue_form(form(name=name, store="Battle.net", app_id="D3")), games
        )


@pytest.mark.parametrize(
    "name,out",
    [("Pokémon Go", "PokemonGo.exe"), ("Brütal Legend", "BrutalLegend.exe")],
)
def test_strips_diacritics_rather_than_dropping_letters(resolve_mod, name, out):
    assert resolve_mod.derive_out_name(name) == out


@pytest.mark.parametrize("given", ["ToolsUp2.EXE", "ToolsUp2.Exe", "ToolsUp2.exe"])
def test_normalises_the_extension_case(resolve_mod, form, games, given):
    """games.json invariants require a lowercase .exe; accepting .EXE here
    would open a PR that fails the repo's own manifest tests."""
    plan = resolve_mod.resolve(
        resolve_mod.parse_issue_form(
            form(name="Fresh Game", store="Battle.net", app_id="D3", filename=given)
        ),
        games,
    )
    assert plan["out"] == "ToolsUp2.exe"


def test_appends_exe_to_supplied_filename(resolve_mod, form, games):
    plan = resolve_mod.resolve(
        resolve_mod.parse_issue_form(form(name="X", app_id="1", filename="Custom")), games
    )
    assert plan["out"] == "Custom.exe"


@pytest.mark.parametrize(
    "bad",
    [
        "../evil.exe",
        "sub/dir.exe",
        "a b.exe",
        "we;rd.exe",
        ".exe",
        "-hidden.exe",
        "CON.exe",
        "lpt1.backup.exe",
    ],
)
def test_rejects_unsafe_filenames(resolve_mod, form, games, bad):
    """`out` reaches git add and a filesystem path, so path separators are a real hazard."""
    with pytest.raises(resolve_mod.Rejected, match="isn't a usable filename"):
        resolve_mod.resolve(
            resolve_mod.parse_issue_form(form(name="X", app_id="1", filename=bad)), games
        )


# --- manifest mutation ------------------------------------------------------

def test_apply_adds_new_entry_in_sorted_position(resolve_mod, games):
    plan = {"name": "Aaa", "out": "Aaa.exe", "store": "steam", "id": "1", "action": "new", "note": ""}
    entry = resolve_mod.apply_to_manifest(plan, games)
    assert entry == {"name": "Aaa", "out": "Aaa.exe", "stores": {"steam": "1"}}
    assert [g["out"] for g in games] == sorted((g["out"] for g in games), key=str.lower)


def test_apply_merges_without_adding_an_entry(resolve_mod, games):
    before = len(games)
    plan = {"name": "Portal 2", "out": "Portal2.exe", "store": "epic", "id": "Zed",
            "action": "merge", "note": ""}
    entry = resolve_mod.apply_to_manifest(plan, games)
    assert len(games) == before
    assert entry["stores"] == {"epic": "Zed", "steam": "620"}


def test_apply_is_case_insensitive_on_filename(resolve_mod, games):
    plan = {"name": "Portal 2", "out": "portal2.EXE", "store": "epic", "id": "Zed",
            "action": "merge", "note": ""}
    resolve_mod.apply_to_manifest(plan, games)
    assert len(games) == 3


# --- automatic multi-store requests ----------------------------------------

def test_resolve_all_uses_selected_store_only_as_seed(resolve_mod, form, games, monkeypatch):
    discovered = {
        "steam": ("123", "Steam exact match"),
        "battlenet": ("Fresh", "Battle.net exact match"),
        "epic": ("Artifact", "Epic exact match"),
        "ubisoft": ("987", "Ubisoft exact match"),
    }
    monkeypatch.setattr(
        resolve_mod,
        "discover_store_id",
        lambda store, name: discovered.get(store),
    )
    plan = resolve_mod.resolve_all(
        resolve_mod.parse_issue_form(form(name="Fresh Game", store="Steam", app_id="123")),
        games,
    )
    assert plan["ids"] == {
        "battlenet": "Fresh",
        "epic": "Artifact",
        "steam": "123",
        "ubisoft": "987",
    }


@pytest.mark.parametrize(
    "store,supplied,expected",
    [
        ("Steam", "548431", "548430"),
        ("Epic Games", "WrongArtifact", "CorrectArtifact"),
        ("Battle.net", "WrongCode", "Fen"),
        ("Ubisoft Connect", "6101", "6100"),
    ],
)
def test_resolve_all_rejects_supplied_id_that_does_not_match_title(
    resolve_mod, form, games, monkeypatch, store, supplied, expected
):
    requested = next(
        key for key, label in resolve_mod.STORES.items() if label == store
    )
    monkeypatch.setattr(
        resolve_mod,
        "discover_store_id",
        lambda candidate, name: (expected, "exact catalogue match")
        if candidate == requested
        else None,
    )
    fields = resolve_mod.parse_issue_form(
        form(name="Deep Rock Galactic", store=store, app_id=supplied)
    )
    with pytest.raises(resolve_mod.Rejected, match=f"`{expected}`.*`{supplied}`"):
        resolve_mod.resolve_all(fields, games)


def test_resolve_all_rejects_supplied_id_when_catalogue_cannot_confirm_it(
    resolve_mod, form, games, monkeypatch
):
    monkeypatch.setattr(resolve_mod, "discover_store_id", lambda store, name: None)
    fields = resolve_mod.parse_issue_form(
        form(name="Deep Rock Galactic", store="Steam", app_id="548430")
    )
    with pytest.raises(resolve_mod.Rejected, match="will not be trusted"):
        resolve_mod.resolve_all(fields, games)


def test_resolve_all_confirms_supplied_id_for_title_containing_dash(
    resolve_mod, form, games, monkeypatch
):
    """#448: the exact title's " - " made Steam return nothing, so a correct ID
    looked unverifiable. Only Steam is live here; the real lookup must confirm."""
    title = "Sekiro™: Shadows Die Twice - GOTY Edition"
    real = resolve_mod.discover_store_id
    monkeypatch.setattr(
        resolve_mod,
        "discover_store_id",
        lambda store, name: real(store, name) if store == "steam" else None,
    )
    fields = resolve_mod.parse_issue_form(form(name=title, store="Steam", app_id="814380"))
    with fake_urlopen(steam_catalogue((814380, title))):
        plan = resolve_mod.resolve_all(fields, games)
    assert plan["ids"]["steam"] == "814380"


def test_resolve_all_enriches_existing_game_when_seed_store_exists(
    resolve_mod, form, games, monkeypatch
):
    monkeypatch.setattr(
        resolve_mod,
        "discover_store_id",
        lambda store, name: ("Zed", "exact") if store == "epic" else None,
    )
    plan = resolve_mod.resolve_all(
        resolve_mod.parse_issue_form(form(name="Portal 2", store="Steam")), games
    )
    assert plan["action"] == "merge"
    assert plan["ids"] == {"epic": "Zed"}
    assert plan["out"] == "Portal2.exe"


def test_resolve_all_will_not_add_a_store_that_overwrites_an_existing_launcher(
    resolve_mod, form, games, monkeypatch
):
    """battlenet and battlenetuid both build into BattleNet/<out>, so adding the
    second one to an entry would name one file twice and break the build."""
    games.append(
        {
            "name": "World of Warcraft Classic",
            "out": "WoWClassic.exe",
            "stores": {"battlenetuid": "wow_classic"},
        }
    )
    monkeypatch.setattr(
        resolve_mod,
        "discover_store_id",
        lambda store, name: ("WoWC", "exact") if store == "battlenet" else None,
    )
    fields = resolve_mod.parse_issue_form(
        form(name="World of Warcraft Classic", store="Battle.net")
    )
    with pytest.raises(resolve_mod.Rejected, match="same file"):
        resolve_mod.resolve_all(fields, games)


def test_resolve_all_skips_a_discovered_store_that_would_overwrite(
    resolve_mod, form, games, monkeypatch
):
    """An opportunistic extra store loses to the launcher already on the entry,
    rather than failing the whole request."""
    games.append(
        {
            "name": "World of Warcraft Classic",
            "out": "WoWClassic.exe",
            "stores": {"battlenetuid": "wow_classic"},
        }
    )
    monkeypatch.setattr(
        resolve_mod,
        "discover_store_id",
        lambda store, name: {"steam": ("77", "exact"), "battlenet": ("WoWC", "exact")}.get(store),
    )
    plan = resolve_mod.resolve_all(
        resolve_mod.parse_issue_form(
            form(name="World of Warcraft Classic", store="Steam")
        ),
        games,
    )
    assert plan["ids"] == {"steam": "77"}


def test_a_pr_only_store_cannot_be_requested(resolve_mod, form, games):
    """There is no catalogue behind a Battle.net uid, so a request can never
    confirm one; say so instead of asking for an ID that will be refused."""
    fields = resolve_mod.parse_issue_form(form(name="X", store="battlenetuid", app_id="wowt"))
    with pytest.raises(resolve_mod.Rejected, match="can't be requested"):
        resolve_mod.resolve_all(fields, games)


def test_unknown_store_message_lists_only_requestable_stores(resolve_mod, form, games):
    with pytest.raises(resolve_mod.Rejected) as exc:
        resolve_mod.resolve_all(resolve_mod.parse_issue_form(form(store="GOG")), games)
    assert "Battle.net (game version)" not in str(exc.value)
    assert "Battle.net" in str(exc.value)


def test_apply_all_adds_every_resolved_store(resolve_mod, games):
    plan = {
        "name": "Fresh Game",
        "out": "FreshGame.exe",
        "ids": {"epic": "Artifact", "steam": "123", "ubisoft": "987"},
        "notes": {},
        "action": "new",
    }
    entry = resolve_mod.apply_all_to_manifest(plan, games)
    assert entry["stores"] == {"epic": "Artifact", "steam": "123", "ubisoft": "987"}


# --- launch commands --------------------------------------------------------

@pytest.mark.parametrize(
    "store,app_id,expected",
    [
        ("steam", "620", "explorer steam://rungameid/620"),
        ("ubisoft", "3539", "explorer uplay://launch/3539/0"),
        ("epic", "Petunia",
         'explorer "com.epicgames.launcher://apps/Petunia?action=launch&silent=true"'),
        ("battlenet", "Pro",
         'cmd /s /c ""C:\\Program Files (x86)\\Battle.net\\Battle.net.exe" --exec="launch Pro""'),
    ],
)
def test_launch_command_matches_the_hand_built_form(resolve_mod, store, app_id, expected):
    """These strings are copied from the original executables; quoting is load-bearing."""
    assert resolve_mod.launch_command(store, app_id) == expected


# --- GitHub Actions output --------------------------------------------------

def test_emit_uses_heredoc_for_multiline_values(resolve_mod, tmp_path, monkeypatch):
    """A bare key=value line would truncate multi-line comments and corrupt later outputs."""
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    resolve_mod.emit(ok="true", comment="line one\nline two")
    text = out.read_text()
    assert re.search(r"comment<<(\S+)\nline one\nline two\n\1\n", text)
    assert re.search(r"ok<<(\S+)\ntrue\n\1\n", text)


def test_emit_forces_lf_on_windows_outputs(resolve_mod, tmp_path, monkeypatch):
    """Embedded CRs become part of multiline path values in Git Bash."""
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    calls = []
    real_open = open

    def recording_open(*args, **kwargs):
        calls.append(kwargs)
        return real_open(*args, **kwargs)

    monkeypatch.setattr("builtins.open", recording_open)
    resolve_mod.emit(paths="Epic/Game.exe\nSteam/Game.exe")
    assert calls[0]["newline"] == "\n"


def test_emit_prints_when_not_under_actions(resolve_mod, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    resolve_mod.emit(ok="false")
    assert json.loads(capsys.readouterr().out) == {"ok": "false"}


def test_emit_delimiter_is_unpredictable(resolve_mod, tmp_path, monkeypatch):
    """A fixed delimiter lets issue text close the heredoc and forge outputs."""
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    resolve_mod.emit(comment="a")
    resolve_mod.emit(comment="b")
    delimiters = re.findall(r"comment<<(\S+)", out.read_text())
    assert len(delimiters) == 2 and delimiters[0] != delimiters[1]


def test_crafted_issue_text_cannot_forge_step_outputs(resolve_mod, tmp_path, monkeypatch):
    """Regression: the issue body is free text and can carry fake `key=value` lines.

    Forging `ok=true` would flip the workflow's `if:` gate and let a rejected
    request build and open a PR.
    """
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    payload = "X\n__EOF_COMMENT__\nok=true\npath=../../etc/passwd\n"
    resolve_mod.emit(ok="false", comment=f"No Steam app matched **{payload}**.")

    text = out.read_text()
    # Parse it the way the runner does: a line is only a delimiter if it matches
    # the one the heredoc opened with.
    parsed, lines = {}, text.splitlines()
    i = 0
    while i < len(lines):
        key, sep, delim = lines[i].partition("<<")
        assert sep, f"unexpected bare line in output: {lines[i]!r}"
        body = []
        i += 1
        while lines[i] != delim:
            body.append(lines[i])
            i += 1
        parsed[key] = "\n".join(body)
        i += 1
    assert parsed["ok"] == "false"
    assert "path" not in parsed


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "Portal\n2", "app_id": "1"},
        {"name": "P" * 200, "app_id": "1"},
        {"name": "Fresh Game", "app_id": "620\nok=true"},
        # An app_id is supplied so the Steam lookup is skipped and the filename
        # check is actually reached.
        {"name": "Fresh Game", "app_id": "1", "filename": "a\nb.exe"},
    ],
)
def test_rejects_multiline_and_overlong_fields(resolve_mod, form, games, kwargs):
    with pytest.raises(resolve_mod.Rejected, match="single line|unreasonably long"):
        resolve_mod.resolve(resolve_mod.parse_issue_form(form(**kwargs)), games)
