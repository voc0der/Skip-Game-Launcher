# Contributing

See [README.md](./README.md) for build and test instructions.

## Rules

- Keep branches, commits, and PRs focused. Do not mix unrelated local changes into the same PR.
- Use semantic names by default.

## Naming

- Branches: `fix/<scope>-<summary>`, `feat/<scope>-<summary>`, `refactor/<scope>-<summary>`
- Commits: `fix(scope): summary`, `feat(scope): summary`, `refactor(scope): summary`
- PR titles: `fix(scope): summary`, `feat(scope): summary`, `refactor(scope): summary`

## Before Opening a PR

- Run `python -m pytest`
- Run `python scripts/index.py` after changing `games.json`
- On Windows, run `Invoke-Pester tests/Launcher.Tests.ps1`
- If launcher binaries changed, run `python scripts/verify.py`
- If a workflow changed, run `actionlint` when available

## Battle.net game versions

Battle.net addresses a game two different ways, and they are not interchangeable:

| Manifest store | `--exec` form | Takes | Example | Starts the game? |
|---|---|---|---|---|
| `battlenet` | `launch <code>` | product code | `WoW`, `D3`, `Pro`, `VIPR` | yes |
| `battlenetuid` | `launch_uid <uid>` | uid | `wow_classic_era`, `wow_classic_anniversary` | no - selects only |

`launch` only addresses top-level products, and for those it starts the game
outright - that is what every `battlenet` entry does. A specific game version -
WoW Classic, Classic Era, a PTR - is not a product and cannot be reached that way
at all; `launch_uid` is the only form that resolves one.

Two things to know before adding one:

- **`launch_uid` selects the version, it does not start it.** The client opens on
  the right game with Play ready, one click short. This limitation is specific to
  game versions; it says nothing about the `battlenet` entries above. Since the
  2021 client rewrite nothing external starts a version outright: the game is handed its
  credentials over IPC by the running client, so `WowClassic.exe -launcherlogin`
  run by hand stops at the login screen, and Blizzard's own generated desktop
  shortcut fails with `BLZBNTAGT00000AF0`. Do not promise a full skip for these.
- **Get the uid from a running client, not from a list.** `.flavor.info` in the
  install folder holds the *flavor* name, which is not the uid - `_anniversary_`
  says `wow_anniversary` while the uid is `wow_classic_anniversary`. Launch the
  version through Battle.net and read the real value off the process:

  ```powershell
  Get-CimInstance Win32_Process -Filter "Name='WowClassic.exe'" |
    Select-Object CommandLine   # ... -launcherlogin -uid wow_classic_anniversary
  ```

There is no catalogue of uids, so `battlenetuid` entries are added by hand in a
PR. Game requests cannot resolve them and the issue form does not offer them.

## Notes

- `games.json` is the source of truth for launcher definitions.
- `GAMES.md` is generated from `games.json`; do not edit it by hand.
- Game requests use one store only as a seed; the resolver must discover and build every exact supported-store match.
- Building launchers requires Windows because `scripts/build.ps1` uses IExpress.
- Do not hand-edit generated `.exe` files.
