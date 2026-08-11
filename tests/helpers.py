"""Shared test scaffolding: import shims, fake HTTP, synthetic executables."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import pathlib
import re
import sys
import urllib.error
import urllib.parse
from unittest import mock

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"


def load_script(name: str):
    """Import scripts/<name>.py, which isn't a package."""
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"sgl_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --- fake HTTP --------------------------------------------------------------

class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


@contextlib.contextmanager
def fake_urlopen(handler):
    """Patch urlopen. `handler` takes the URL and returns a dict, or raises."""

    def _open(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else req
        result = handler(url)
        return FakeResponse(json.dumps(result).encode())

    with mock.patch("urllib.request.urlopen", _open):
        yield


@contextlib.contextmanager
def urlopen_raises(exc):
    def _open(*args, **kwargs):
        raise exc

    with mock.patch("urllib.request.urlopen", _open):
        yield


def steam_results(*pairs):
    """Build a storesearch payload from (id, name) pairs."""
    return {"items": [{"type": "app", "id": i, "name": n} for i, n in pairs]}


def steam_catalogue(*pairs):
    """A storesearch handler that mimics the endpoint's own query semantics.

    The live API treats a standalone "-" as NOT and drops every title matching
    the terms after it, so an exact catalogue title containing " - " excludes
    itself and returns nothing. Tests need that behaviour to be real, otherwise
    a handler that ignores the URL passes with or without the sanitising fix.
    """
    def handler(url):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        term = query.get("term", [""])[0]
        # Verified against the live endpoint: ASCII, en and em dashes all zero
        # out a search that would otherwise hit.
        parts = re.split(r"\s[-–—]\s", term, maxsplit=1)
        include, exclude = parts[0], parts[1] if len(parts) > 1 else ""

        def words(s):
            return [w for w in re.split(r"[^a-z0-9]+", s.lower()) if w]

        def hit(name):
            have = words(name)
            if not all(w in have for w in words(include)):
                return False
            return not (exclude and all(w in have for w in words(exclude)))

        return steam_results(*[(i, n) for i, n in pairs if hit(n)])

    return handler


def epic_product(*pages):
    """Build a content-API payload from (slug, appName) pairs."""
    return {"pages": [{"_slug": s, "item": {"appName": a}} for s, a in pages]}


NOT_FOUND = urllib.error.HTTPError("u", 404, "Not Found", {}, None)


# --- synthetic executables --------------------------------------------------

# Roughly the shape verify.py reads: some PE-ish noise, the SED string table,
# then the CAB. Only the strings matter to the parser.
def fake_exe(launch_command: str, friendly: str = "Test Game") -> bytes:
    return (
        b"MZ\x90\x00" + b"\x00" * 64
        + b"PE\x00\x00" + b"\x00" * 32
        + b"TITLE\x00SHOWWINDOW\x00ADMQCMD\x00USRQCMD\x00POSTRUNPROGRAM\x00FINISHMSG\x00"
        + b"MSCF\x00\x00\x00\x00dummy.bat\x00echo 1\x00"
        + b"<None>\x00P<None>\x00P\x00\x00\x00\x00"
        + launch_command.encode("latin-1") + b"\x00P\x01\x00\x00\x00"
        + friendly.encode("latin-1") + b"\x00PAD<None>\x00P"
    )


def write_tree(root: pathlib.Path, manifest: list[dict], commands: dict[str, str]) -> None:
    """Materialise a fake repo: games.json plus Store/Name.exe files.

    `commands` maps "Store/Name.exe" to the launch command to embed. Paths
    absent from it are simply not created, standing in for a not-yet-built game.
    """
    (root / "games.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for rel, command in commands.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(fake_exe(command))
