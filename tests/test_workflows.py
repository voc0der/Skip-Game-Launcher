"""Tests for the Actions workflows.

The important one is the interpolation rule: issue bodies are written by
strangers, and `${{ }}` is substituted into the shell script *before* the shell
sees it, so a title containing `$(...)` or a backtick runs on the runner.
Everything untrusted has to arrive through `env:` instead.
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((REPO / ".github" / "workflows").glob("*.yml"))
INTERPOLATION = re.compile(r"\$\{\{.*?\}\}", re.S)


def _steps(workflow: dict):
    for job_name, job in (workflow.get("jobs") or {}).items():
        for i, step in enumerate(job.get("steps") or []):
            yield job_name, i, step


def _visible_env(workflow: dict, job_name: str, step: dict) -> dict:
    """Env a step can actually see: workflow, then job, then step level.

    Checking only `step["env"]` misses job-level vars, which is where the
    interesting ones (issue numbers, refs) tend to live.
    """
    job = (workflow.get("jobs") or {})[job_name]
    return {
        **(workflow.get("env") or {}),
        **(job.get("env") or {}),
        **(step.get("env") or {}),
    }


@pytest.fixture(scope="module", params=WORKFLOWS, ids=lambda p: p.name)
def workflow(request):
    return request.param, yaml.safe_load(request.param.read_text())


def test_workflows_were_found():
    """Guards against the glob silently matching nothing and every test vacuously passing."""
    assert {p.name for p in WORKFLOWS} == {
        "build.yml", "game-request.yml", "verify.yml", "test.yml"
    }


def test_workflow_parses(workflow):
    path, doc = workflow
    assert isinstance(doc, dict), f"{path.name} is not a mapping"
    # PyYAML reads a bare `on:` key as the boolean True.
    assert doc.get("on") or doc.get(True), f"{path.name} has no trigger"


def test_no_interpolation_inside_run_blocks(workflow):
    """Regression: `${{ github.event.issue.body }}` in a run: block is RCE."""
    path, doc = workflow
    offenders = [
        f"{path.name}:{job}[{i}] -> {m}"
        for job, i, step in _steps(doc)
        if step.get("run")
        for m in INTERPOLATION.findall(step["run"])
    ]
    assert not offenders, (
        "untrusted-by-default expressions inside run: blocks; pass them via env: instead\n"
        + "\n".join(offenders)
    )


def _inside_double_quotes(line: str, index: int) -> bool:
    """Whether `index` falls inside a double-quoted run of `line`.

    Crude but adequate: count unescaped double quotes to the left. An odd
    number means the position is inside a quoted string.
    """
    quotes = 0
    escaped = False
    for char in line[:index]:
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            quotes += 1
    return quotes % 2 == 1


@pytest.mark.parametrize(
    "line,index,expected",
    [
        ('echo "$NAME"', 7, True),
        ("echo $NAME", 5, False),
        ('git commit -m "Add ${NAME} here"', 20, True),
        ('echo "closed" && echo $NAME', 22, False),
        ('printf "%s" "$A"', 13, True),
        ('echo \\"$NAME', 8, False),  # escaped quote does not open a string
    ],
)
def test_quote_detection_helper(line, index, expected):
    """The quoting check is only as good as this; a broken helper makes it vacuous."""
    assert _inside_double_quotes(line, index) is expected


def test_env_values_are_quoted_when_used_in_shell(workflow):
    """`$VAR` unquoted word-splits on spaces even when it arrives safely via env."""
    path, doc = workflow
    problems = []
    for job, i, step in _steps(doc):
        run, env = step.get("run"), _visible_env(doc, job, step)
        if not run or step.get("shell") == "pwsh":
            continue
        for name in env:
            for match in re.finditer(rf"\$\{{?{re.escape(name)}\b\}}?", run):
                start_of_line = run.rfind("\n", 0, match.start()) + 1
                line = run[start_of_line : run.find("\n", match.start())]
                if _inside_double_quotes(line, match.start() - start_of_line):
                    continue
                problems.append(f"{path.name}:{job}[{i}]: {line.strip()}")
    assert not problems, "unquoted env expansion:\n" + "\n".join(problems)


def test_declares_explicit_permissions(workflow):
    """Without a permissions block the token inherits repo defaults, which are broad."""
    path, doc = workflow
    assert "permissions" in doc, f"{path.name} does not pin its token permissions"


def test_referenced_scripts_exist(workflow):
    path, doc = workflow
    missing = [
        ref
        for _, _, step in _steps(doc)
        if step.get("run")
        for ref in re.findall(r"(?:\./)?(scripts/[\w.-]+)", step["run"])
        if not (REPO / ref).exists()
    ]
    assert not missing, f"{path.name} references missing scripts: {missing}"


def test_actions_are_version_pinned(workflow):
    """A floating ref means CI can start failing without anything here changing."""
    path, doc = workflow
    unpinned = []
    for _, _, step in _steps(doc):
        uses = step.get("uses")
        if not uses:
            continue
        if uses.startswith("docker://"):
            image = uses[len("docker://"):]
            tag = image.rsplit(":", 1)[-1] if ":" in image else ""
            if tag in ("", "latest"):
                unpinned.append(uses)
        elif "@" not in uses:
            unpinned.append(uses)
    assert not unpinned, f"{path.name} has floating action refs: {unpinned}"


def test_game_request_is_gated_on_a_label():
    doc = yaml.safe_load((REPO / ".github" / "workflows" / "game-request.yml").read_text())
    job = doc["jobs"]["fulfil"]
    assert "github.event.label.name ==" in job["if"], "request builds must require a label"


def test_game_request_merges_generated_pr():
    doc = yaml.safe_load((REPO / ".github" / "workflows" / "game-request.yml").read_text())
    steps = doc["jobs"]["fulfil"]["steps"]
    publish = next(step for step in steps if step.get("name") == "Open and merge the PR")
    script = publish["run"]
    assert "gh pr create" in script
    assert "gh pr merge" in script
    assert 'gh issue close "$ISSUE" --reason completed' in script
    assert script.index("gh pr create") < script.index("gh pr merge")
    assert script.index("gh pr merge") < script.index("gh issue close")


def test_build_workflow_verifies_before_committing():
    doc = yaml.safe_load((REPO / ".github" / "workflows" / "build.yml").read_text())
    steps = doc["jobs"]["build"]["steps"]
    verify = next(i for i, step in enumerate(steps) if step.get("name") == "Verify built launchers")
    commit = next(i for i, step in enumerate(steps) if step.get("name") == "Commit built launchers")
    assert verify < commit
    assert "scripts/verify.py --strict" in steps[verify]["run"]


def test_issue_template_is_valid_and_labelled():
    path = REPO / ".github" / "ISSUE_TEMPLATE" / "game-request.yml"
    doc = yaml.safe_load(path.read_text())
    assert doc["name"] and doc["description"]
    assert doc["labels"], "template must apply a label so requests are findable"
    ids = [b.get("id") for b in doc["body"] if b["type"] != "markdown"]
    # resolve.py keys off the rendered headings, so these fields must stay present.
    assert {"name", "store", "appid", "filename"} <= set(ids)


def test_issue_template_headings_match_what_resolve_expects(resolve_mod):
    """The parser looks the fields up by their rendered heading, lowercased."""
    path = REPO / ".github" / "ISSUE_TEMPLATE" / "game-request.yml"
    doc = yaml.safe_load(path.read_text())
    headings = {
        b["attributes"]["label"].lower()
        for b in doc["body"]
        if b["type"] != "markdown" and "label" in b.get("attributes", {})
    }
    assert {"game name", "store", "app id / product code", "output filename"} <= headings


def test_issue_template_store_options_are_all_supported(resolve_mod):
    path = REPO / ".github" / "ISSUE_TEMPLATE" / "game-request.yml"
    doc = yaml.safe_load(path.read_text())
    options = next(b for b in doc["body"] if b.get("id") == "store")["attributes"]["options"]
    labels = {o.lower() for o in options}
    known = {k for k in resolve_mod.STORES} | {v.lower() for v in resolve_mod.STORES.values()}
    assert labels <= known, f"template offers stores resolve.py rejects: {labels - known}"


@pytest.mark.skipif(not shutil.which("actionlint"), reason="actionlint not installed")
def test_actionlint_is_clean():
    result = subprocess.run(
        ["actionlint", *[str(p) for p in WORKFLOWS]],
        capture_output=True, text=True, cwd=REPO,
    )
    assert result.returncode == 0, result.stdout + result.stderr
