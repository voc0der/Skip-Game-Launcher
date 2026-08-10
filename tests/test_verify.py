"""Tests for scripts/verify.py - the only thing standing between a mis-packaged
executable and a commit, since IExpress reports success regardless."""
from __future__ import annotations

import pytest
from helpers import fake_exe, write_tree

STEAM_CANON = "explorer steam://rungameid/620"
BNET_CANON = 'cmd /s /c ""C:\\Program Files (x86)\\Battle.net\\Battle.net.exe" --exec="launch Pro""'
EPIC_CANON = 'explorer "com.epicgames.launcher://apps/Petunia?action=launch&silent=true"'


# --- dialect detection ------------------------------------------------------

@pytest.mark.parametrize(
    "command,store,ident",
    [
        (STEAM_CANON, "steam", "620"),
        ("explorer uplay://launch/3539/0", "ubisoft", "3539"),
        (EPIC_CANON, "epic", "Petunia"),
        (BNET_CANON, "battlenet", "Pro"),
        ('cmd /c "C:\\Windows\\explorer.exe" steam://rungameid/8800', "steam", "8800"),
        ('cmd /s /c ""C:\\Program Files (x86)\\Steam\\steam.exe"" -gameidlaunch 220',
         "steam", "220"),
    ],
)
def test_recognises_every_command_dialect(verify_mod, tmp_path, command, store, ident):
    """All six forms found across the hand-built executables must be readable."""
    exe = tmp_path / "x.exe"
    exe.write_bytes(fake_exe(command))
    got_store, got_id, _ = verify_mod.inspect(str(exe))
    assert (got_store, got_id) == (store, ident)


def test_flags_hardcoded_steam_path(verify_mod, tmp_path):
    """These executables only work if Steam sits in the default location."""
    exe = tmp_path / "x.exe"
    exe.write_bytes(fake_exe('cmd /s /c ""C:\\Program Files (x86)\\Steam\\steam.exe"" -gameidlaunch 220'))
    _, _, note = verify_mod.inspect(str(exe))
    assert "hardcoded Steam install path" in note


def test_canonical_command_has_no_note(verify_mod, tmp_path):
    exe = tmp_path / "x.exe"
    exe.write_bytes(fake_exe(STEAM_CANON))
    assert verify_mod.inspect(str(exe))[2] == "canonical"


def test_flags_executed_dummy_payload(verify_mod, tmp_path):
    """The hidden-window flag applies to AppLaunched, so putting the dummy
    command there makes the real post-install launch visible."""
    exe = tmp_path / "x.exe"
    exe.write_bytes(fake_exe(STEAM_CANON) + b"cmd /c dummy.bat\x00")
    _, _, note = verify_mod.inspect(str(exe))
    assert "dummy payload is executed" in note


def test_reports_unrecognisable_payload(verify_mod, tmp_path):
    exe = tmp_path / "x.exe"
    exe.write_bytes(b"MZ" + b"\x00" * 200)
    store, ident, note = verify_mod.inspect(str(exe))
    assert store is None and ident is None and "no recognisable" in note


# --- whole-tree checking ----------------------------------------------------

def _run(verify_mod, tmp_path, monkeypatch, *argv):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["verify.py", "--dir", str(tmp_path),
                                     "--manifest", str(tmp_path / "games.json"), *argv])
    return verify_mod.main()


def test_passes_when_tree_matches_manifest(verify_mod, tmp_path, monkeypatch, capsys):
    write_tree(
        tmp_path,
        [{"name": "Portal 2", "out": "Portal2.exe", "stores": {"steam": "620"}}],
        {"Steam/Portal2.exe": STEAM_CANON},
    )
    assert _run(verify_mod, tmp_path, monkeypatch) == 0
    assert "all good" in capsys.readouterr().out


def test_fails_on_wrong_app_id(verify_mod, tmp_path, monkeypatch, capsys):
    """The failure mode that matters: a launcher that starts the wrong game."""
    write_tree(
        tmp_path,
        [{"name": "Portal 2", "out": "Portal2.exe", "stores": {"steam": "620"}}],
        {"Steam/Portal2.exe": "explorer steam://rungameid/999"},
    )
    assert _run(verify_mod, tmp_path, monkeypatch) == 1
    assert "manifest says 620, binary has 999" in capsys.readouterr().err


def test_fails_when_binary_uses_a_different_store(verify_mod, tmp_path, monkeypatch, capsys):
    write_tree(
        tmp_path,
        [{"name": "Portal 2", "out": "Portal2.exe", "stores": {"steam": "620"}}],
        {"Steam/Portal2.exe": BNET_CANON},
    )
    assert _run(verify_mod, tmp_path, monkeypatch) == 1
    assert "binary launches via battlenet" in capsys.readouterr().err


def test_missing_file_is_reported_but_not_a_failure(verify_mod, tmp_path, monkeypatch, capsys):
    """A manifest entry with no executable is CI's normal input, not an error."""
    write_tree(
        tmp_path,
        [{"name": "Portal 2", "out": "Portal2.exe", "stores": {"steam": "620"}}],
        {},
    )
    assert _run(verify_mod, tmp_path, monkeypatch) == 0
    assert "not built yet (1): Steam/Portal2.exe" in capsys.readouterr().out


def test_checks_every_store_of_a_multi_store_game(verify_mod, tmp_path, monkeypatch, capsys):
    write_tree(
        tmp_path,
        [{"name": "Metro", "out": "Metro.exe", "stores": {"epic": "Petunia", "steam": "286690"}}],
        {
            "Epic/Metro.exe": EPIC_CANON,
            "Steam/Metro.exe": "explorer steam://rungameid/111",
        },
    )
    assert _run(verify_mod, tmp_path, monkeypatch) == 1
    assert "Steam/Metro.exe" in capsys.readouterr().err


def test_unknown_store_in_manifest_fails(verify_mod, tmp_path, monkeypatch, capsys):
    write_tree(tmp_path, [{"name": "X", "out": "X.exe", "stores": {"gog": "1"}}], {})
    assert _run(verify_mod, tmp_path, monkeypatch) == 1
    assert "unknown store 'gog'" in capsys.readouterr().err


# --- strict mode ------------------------------------------------------------

def test_legacy_form_passes_by_default(verify_mod, tmp_path, monkeypatch):
    write_tree(
        tmp_path,
        [{"name": "Half Life 2", "out": "HalfLife2.exe", "stores": {"steam": "220"}}],
        {"Steam/HalfLife2.exe": 'cmd /s /c ""C:\\Program Files (x86)\\Steam\\steam.exe"" -gameidlaunch 220'},
    )
    assert _run(verify_mod, tmp_path, monkeypatch) == 0


def test_legacy_form_fails_under_strict(verify_mod, tmp_path, monkeypatch):
    """A fresh build has no excuse for emitting anything but the canonical form."""
    write_tree(
        tmp_path,
        [{"name": "Half Life 2", "out": "HalfLife2.exe", "stores": {"steam": "220"}}],
        {"Steam/HalfLife2.exe": 'cmd /s /c ""C:\\Program Files (x86)\\Steam\\steam.exe"" -gameidlaunch 220'},
    )
    assert _run(verify_mod, tmp_path, monkeypatch, "--strict") == 1


def test_executed_dummy_payload_fails_under_strict(
    verify_mod, tmp_path, monkeypatch
):
    write_tree(
        tmp_path,
        [{"name": "Portal 2", "out": "Portal2.exe", "stores": {"steam": "620"}}],
        {"Steam/Portal2.exe": STEAM_CANON + "\x00cmd /c dummy.bat"},
    )
    assert _run(verify_mod, tmp_path, monkeypatch, "--strict") == 1
