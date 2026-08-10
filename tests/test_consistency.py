"""Cross-language invariants.

The Python resolver, the Python verifier and the PowerShell builder each carry
their own copy of the store mapping and the launch-command formats. Nothing at
runtime forces them to agree, and a silent divergence is nasty: the builder
would write Ubisoft/X.exe while the workflow committed Uplay/X.exe, or the
verifier would stop recognising what the builder emits.
"""
from __future__ import annotations

import pathlib
import re

import pytest
from helpers import fake_exe

REPO = pathlib.Path(__file__).resolve().parent.parent
PSM1 = (REPO / "scripts" / "Launcher.psm1").read_text(encoding="utf-8")

SENTINEL = "SENTINELID"


def powershell_store_dirs() -> dict[str, str]:
    block = re.search(r"\$script:StoreDirs\s*=\s*\[ordered\]@\{(.*?)\}", PSM1, re.S)
    assert block, "could not find $script:StoreDirs in Launcher.psm1"
    return dict(re.findall(r"(\w+)\s*=\s*'([^']+)'", block.group(1)))


def test_store_directories_agree_across_languages(resolve_mod, verify_mod):
    powershell = powershell_store_dirs()
    assert powershell == resolve_mod.STORE_DIRS
    assert powershell == verify_mod.STORE_DIRS


def test_resolve_knows_the_same_stores_it_can_map(resolve_mod):
    assert set(resolve_mod.STORES) == set(resolve_mod.STORE_DIRS)


@pytest.mark.parametrize("store", ["steam", "battlenet", "battlenetuid", "epic", "ubisoft"])
def test_launch_command_templates_match_the_powershell_builder(resolve_mod, store):
    """resolve.py only formats these for the issue comment, but a drifted
    template means the comment advertises a command the build never produces."""
    command = resolve_mod.launch_command(store, SENTINEL)
    prefix, _, suffix = command.partition(SENTINEL)
    assert prefix in PSM1, f"{store}: prefix {prefix!r} not found in Launcher.psm1"
    if suffix:
        assert suffix in PSM1, f"{store}: suffix {suffix!r} not found in Launcher.psm1"


@pytest.mark.parametrize("store", ["steam", "battlenet", "battlenetuid", "epic", "ubisoft"])
def test_verifier_recognises_what_the_builder_emits(resolve_mod, verify_mod, tmp_path, store):
    """The end of the loop: anything built must read back as canonical."""
    app_id = {
        "steam": "620",
        "ubisoft": "620",
        # uids are lowercase; "Petunia" would not survive the dialect.
        "battlenetuid": "wow_classic_era",
    }.get(store, "Petunia")
    exe = tmp_path / "x.exe"
    exe.write_bytes(fake_exe(resolve_mod.launch_command(store, app_id)))

    got_store, got_id, note = verify_mod.inspect(str(exe))
    assert (got_store, got_id) == (store, app_id)
    assert note == "canonical", f"{store} builds are flagged as legacy: {note}"


def test_every_store_has_a_verifier_dialect(resolve_mod, verify_mod):
    covered = {store for _, store, note in verify_mod.DIALECTS if note == "canonical"}
    assert covered == set(resolve_mod.STORE_DIRS)


def test_powershell_module_exports_what_build_uses():
    """build.ps1 calls these by name; a rename that misses one fails only at runtime."""
    exported = re.search(r"Export-ModuleMember\s+-Function\s+(.+?)(?:\n\n|\Z)", PSM1, re.S)
    assert exported
    names = {n.strip() for n in re.split(r"[,\s]+", exported.group(1)) if n.strip()}
    build = (REPO / "scripts" / "build.ps1").read_text(encoding="utf-8")
    for used in ("Get-StoreDirectory", "Get-LaunchCommand", "New-SedContent", "Get-BuildTarget"):
        assert used in names, f"{used} is not exported from Launcher.psm1"
        assert used in build, f"{used} is exported but unused - dead code?"
