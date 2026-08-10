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
- On Windows, run `Invoke-Pester tests/Launcher.Tests.ps1`
- If launcher binaries changed, run `python scripts/verify.py`
- If a workflow changed, run `actionlint` when available

## Notes

- `games.json` is the source of truth for launcher definitions.
- Building launchers requires Windows because `scripts/build.ps1` uses IExpress.
- Do not hand-edit generated `.exe` files.
