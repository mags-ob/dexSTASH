"""GitHub Actions run once per change (issue #49): checks on main and pull requests, older pull request runs
cancelled, a description edit reruns only the spec rule, and spec-only pull requests skip the tests."""

import os
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


@pytest.mark.parametrize("name", ["tests.yml", "checks.yml"])
def test_checks_run_on_main_and_pull_requests_only(name):
    on = _on(name)
    assert on["push"] == {"branches": ["main"]}
    assert "pull_request" in on and "workflow_dispatch" in on


@pytest.mark.parametrize("name", ["tests.yml", "checks.yml"])
def test_a_newer_push_cancels_the_older_pull_request_run(name):
    concurrency = _flow(name)["concurrency"]
    assert "github.event.pull_request.number" in concurrency["group"]
    assert "github.run_id" in concurrency["group"]  # main and release runs each get their own group
    assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"


def test_a_description_edit_reruns_only_the_spec_rule():
    assert "edited" not in (_on("checks.yml")["pull_request"] or {}).get("types", [])
    flow = _flow("description.yml")
    assert flow[True] == {"pull_request": {"types": ["edited"]}}
    assert list(flow["jobs"]) == ["rules"]
    job = flow["jobs"]["rules"]
    assert job["name"] == _flow("checks.yml")["jobs"]["rules"]["name"]  # the same check on the pull request
    assert job["steps"][-1]["run"] == "python tools/check_rules.py --spec"
    assert job["steps"][-1]["env"]["DEXSTASH_PRIVATE_WORDS"] == "${{ secrets.DEXSTASH_PRIVATE_WORDS }}"


def test_spec_only_pull_requests_skip_the_tests():
    ignored = _on("tests.yml")["pull_request"]["paths-ignore"]
    assert "docs/specs/**" in ignored and "rules/**" in ignored
    # Every file a test reads must keep the tests running.
    for path in ["README.md", "DEPLOY.md", "CONTRIBUTING.md", "CHANGELOG.md", "docs/architecture.md",
                 ".github/workflows/tests.yml", "app/main.py", "tests/test_ci.py"]:
        assert not any(path == p or (p.endswith("/**") and path.startswith(p[:-2])) for p in ignored), path
    assert not any(p.startswith("docs/") and p != "docs/specs/**" for p in ignored)


@pytest.mark.skipif(os.name == "nt", reason="shell hook")
def test_the_pre_push_hook_runs_the_quick_checks():
    hook = ROOT / "tools" / "hooks" / "pre-push"
    assert os.access(hook, os.X_OK)
    text = hook.read_text()
    for step in ("ruff check .", "tools/check_rules.py", "pytest -q", "gitleaks git"):
        assert step in text
    subprocess.run(["sh", "-n", str(hook)], check=True)  # it parses
