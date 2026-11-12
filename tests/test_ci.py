"""The tests workflow (it also gates the release image): runs on main and pull requests, older pull request runs
are cancelled, and spec-only pull requests skip the tests. The git hooks run the local checks."""

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


def _on(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))[True]  # yaml reads "on" as True


def _flow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["tests.yml"])
def test_checks_run_on_main_and_pull_requests_only(name):
    on = _on(name)
    assert on["push"] == {"branches": ["main"]}
    assert "pull_request" in on and "workflow_dispatch" in on


@pytest.mark.parametrize("name", ["tests.yml"])
def test_a_newer_push_cancels_the_older_pull_request_run(name):
    concurrency = _flow(name)["concurrency"]
    assert "github.event.pull_request.number" in concurrency["group"]
    assert "github.run_id" in concurrency["group"]  # main and release runs each get their own group
    assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"


def _skips(files: list[str]) -> bool:
    """Whether the tests workflow's own filter skips the suite for a pull request changing these files."""
    script = _flow("tests.yml")["jobs"]["changes"]["steps"][-1]["run"]
    pattern = re.search(r"grep -Ev '(.+?)'", script).group(1)
    out = subprocess.run(["grep", "-Ev", pattern], input="\n".join(files) + "\n", capture_output=True, text=True)
    return not out.stdout.strip()


@pytest.mark.skipif(os.name == "nt", reason="uses grep")
def test_spec_only_pull_requests_skip_the_tests():
    job = _flow("tests.yml")["jobs"]["tests"]
    assert job["needs"] == "changes" and job["if"] == "needs.changes.outputs.code == 'true'"
    assert "pull_request" in _on("tests.yml") and "paths-ignore" not in (_on("tests.yml")["pull_request"] or {})
    assert _skips(["docs/specs/prices.md", "rules/project.md", "AGENTS.md", ".github/ISSUE_TEMPLATE/bug.yml"])
    # Every file a test reads keeps the tests running.
    for path in ["README.md", "DEPLOY.md", "CONTRIBUTING.md", "CHANGELOG.md", "docs/architecture.md",
                 ".github/workflows/tests.yml", "app/main.py", "tests/test_ci.py", "rules.py", "docs/specs.py"]:
        assert not _skips([path]), path
    assert not _skips(["docs/specs/prices.md", "app/prices.py"])


@pytest.mark.skipif(os.name == "nt", reason="shell hook")
def test_the_pre_push_hook_runs_the_quick_checks():
    hook = ROOT / "tools" / "hooks" / "pre-push"
    assert os.access(hook, os.X_OK)
    text = hook.read_text()
    for step in ("ruff check .", "tools/check_rules.py", "pytest -q", "gitleaks git"):
        assert step in text
    subprocess.run(["sh", "-n", str(hook)], check=True)  # it parses


@pytest.mark.skipif(os.name == "nt", reason="shell hook")
def test_the_pre_commit_hook_runs_the_scan():
    hook = ROOT / "tools" / "hooks" / "pre-commit"
    assert os.access(hook, os.X_OK) and "tools/scan.sh" in hook.read_text()
    assert os.access(ROOT / "tools" / "scan.sh", os.X_OK) and os.access(ROOT / "tools" / "project-scan.sh", os.X_OK)
