<b>What this does?</b> Launches the respective Battle.net or Steam game its named after directly, without needing to click "Play".



*Note: Your games can be elsewhere, they will be launched from the launcher.*

<b>Side note:</b> If you use <a href="https://forum.xda-developers.com/windows-10/development/win10tile-native-custom-windows-10-t3248677">Win10Tile</a>, you can make game tile shortcuts with these files easily.

**If using a steam game, Steam can be installed anywhere.

## Where the files are

Launchers are grouped by the store they launch through:

```
Steam/       BattleNet/      Epic/       Ubisoft/
```

A game sold on more than one store gets one executable per store, so grab the
one matching where you actually own it — `Steam/Metro2033Redux.exe` and
`Epic/Metro2033Redux.exe` are different files.

## Requesting a game

Open a [game request](../../issues/new?template=game-request.yml). Once a
maintainer applies the **approved** label, CI resolves the store ID, builds the
launcher and opens a PR that closes the request when merged.

Steam titles are looked up from the name automatically. Epic is attempted too,
but the string its launch URI needs is an internal codename — `Petunia` serves
`metro-2033-redux` — and Epic publishes it for only some products, so Epic
requests often have to carry it. Battle.net and Ubisoft have no public
catalogue at all and always need the ID. The form explains where to find each.

Already have the game here on Steam and want the Epic build too? Request it
again with the other store; that adds a second executable rather than a
duplicate entry.

## Source Information

Every launcher is generated from [`games.json`](games.json) by
[`scripts/build.ps1`](scripts/build.ps1), which drives the stock Windows
IExpress tool on a GitHub Actions Windows runner. The source redo intentionally
starts without any inherited executables, so the first build generates every
launcher fresh from the manifest. After that, pushing a new entry is enough —
CI builds anything in the manifest that isn't on disk yet and commits it.

```jsonc
{
  "name": "Metro 2033 Redux",
  "out": "Metro2033Redux.exe",
  "stores": { "epic": "Petunia", "steam": "286690" }
}
```

The executables emulate package installers with a dummy bat file:
```
echo 1
```

Then runs the command to execute the game, thanks to <a href="https://github.com/dafzor/bnetlauncher/issues/22#issuecomment-399788430">Ethan-BB's post</a> , implementation like so:

For Battle.Net: 
**Requirement: Battle.net Launcher Install path must be the default location (below):**<br />
```C:\Program Files (x86)\Battle.net```
```
// launch overwatch
cmd /s /c ""C:\Program Files (x86)\Battle.net\Battle.net.exe" --exec="launch Pro""
```

For Steam, it's suffice to do something like
```launch x
explorer steam://rungameid/2760
```

For EpicGames, here's how you can launch a desktop shortcut from cmd (quotes are neccessary)
```
explorer "com.epicgames.launcher://apps/Albacore?action=launch&silent=true"
```

For Ubisoft Connect, it's similar to steam:
```
explorer uplay://launch/3539/0
```

The executable does not need to be run as administrator, however, windows may flag it as a potential threat due to unknown source. To prevent windows blocking it, simply right click properties, and there's an unblock button at the bottom right of that dialog prompt.

## Building locally

Needs Windows — IExpress is a Windows component.

```powershell
./scripts/build.ps1                          # build whatever's missing
./scripts/build.ps1 -Force                   # rebuild everything
./scripts/build.ps1 -Only Portal2.exe        # one game, every store it's on
./scripts/build.ps1 -Only Portal2.exe -Store steam
```

## Tests

```
python -m pytest                          # resolver, verifier, manifest, workflows
Invoke-Pester tests/Launcher.Tests.ps1    # launch commands, .sed generation (Windows)
```

The Python suite needs `pytest` and `pyyaml` and runs anywhere. The Pester
suite covers everything up to the IExpress call; the call itself is exercised
end to end by the `Verify` workflow, which rebuilds all 67 launchers and reads
the commands back out of the results.

`scripts/verify.py` reads the launch command back out of each executable and
checks it against the manifest — IExpress will happily produce a package
containing the wrong thing, so this is what catches it:

```
python scripts/verify.py            # check any committed executables
python scripts/verify.py --dir out --strict
```

During the migration, `verify.py` identified issues in thirteen of the original
hand-built executables. Ten called `steam.exe -gameidlaunch`, one launched the
game binary by absolute path, and all thirteen hardcoded
`C:\Program Files (x86)\Steam`, including two that otherwise use the modern
URI. Those inherited binaries are not carried into this redo: the fresh build
normalises every Steam launcher to `explorer steam://rungameid/<id>`, which is
what makes the "Steam can be installed anywhere" note above actually true.
