"""Shared test scaffolding: import shims, fake HTTP, synthetic executables."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import pathlib
import sys
import urllib.error
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
