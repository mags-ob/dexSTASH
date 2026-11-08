"""Pull request checks run on a contributor's machine while GitHub Actions is off (issue #51)."""

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_rules  # noqa: E402
import local_checks  # noqa: E402


def _job_names() -> set[str]:
    names = set()
    for flow in ("checks.yml", "tests.yml"):
        jobs = yaml.safe_load((ROOT / ".github" / "workflows" / flow).read_text())["jobs"]
        names |= {job.get("name", key) for key, job in jobs.items()}
    return names


def test_each_check_is_reported_under_its_actions_name():
    assert set(local_checks.CHECKS) <= _job_names()  # a required check accepts either source
    assert set(local_checks.CHECKS) == {"Lint (ruff)", "Core rules and spec", "Secrets (gitleaks)",
                                        "Dependencies (pip-audit)", "tests"}


def test_the_spec_rule_reads_the_pull_request_from_the_event_file(tmp_path, monkeypatch):
    head = local_checks.run(["git", "rev-parse", "HEAD"]).stdout.strip()
    pr = {"baseRefOid": head, "headRefOid": head, "headRefName": "chore/local-checks", "body": "Spec: none"}
    monkeypatch.setenv("GITHUB_EVENT_PATH", local_checks.event_file(pr, str(tmp_path)))
    assert check_rules.pull_request() == ([], "Spec: none", "chore/local-checks")


def test_checks_only_stand_for_the_pull_request_s_own_commit(monkeypatch):
    head = local_checks.run(["git", "rev-parse", "HEAD"]).stdout.strip()
    assert "checked out" in local_checks.problem_before_running({"headRefOid": "0" * 40})
    clean = local_checks.run(["git", "status", "--porcelain", "--untracked-files=no"]).stdout.strip() == ""
    assert (local_checks.problem_before_running({"headRefOid": head}) is None) == clean


def test_a_check_that_cannot_run_is_reported_as_failed(monkeypatch, capsys):
    head = local_checks.run(["git", "rev-parse", "HEAD"]).stdout.strip()
    pr = {"number": 1, "headRefOid": head, "baseRefOid": head, "headRefName": "chore/x", "body": "", "url": "u"}
    reports = []
    monkeypatch.setattr(local_checks, "pull_request", lambda n: pr)
    monkeypatch.setattr(local_checks, "problem_before_running", lambda pr: None)
    monkeypatch.setattr(local_checks, "fetch_base", lambda pr: None)  # no network
    monkeypatch.setattr(local_checks, "gh", lambda *a: json.dumps({"nameWithOwner": "o/r"}))
    monkeypatch.setattr(local_checks, "report", lambda repo, sha, name, state, note: reports.append((name, state)))
    monkeypatch.setattr(local_checks, "CHECKS", {"Secrets (gitleaks)": ["no-such-tool-xyz"],
                                                 "Core rules and spec": [sys.executable, "-c", "pass"]})
    monkeypatch.delenv("DEXSTASH_PRIVATE_WORDS", raising=False)
    assert local_checks.main([]) == 1
    assert ("Secrets (gitleaks)", "failure") in reports and ("Core rules and spec", "failure") in reports
    assert ("Secrets (gitleaks)", "success") not in reports and ("Core rules and spec", "success") not in reports


def test_checks_that_need_the_packages_run_in_docker_where_they_are_missing(monkeypatch):
    real = local_checks.run
    monkeypatch.setattr(local_checks, "run", lambda cmd, **kw: real([sys.executable, "-c", "raise SystemExit(1)"])
                        if "-c" in cmd and "import onnxruntime" in cmd[-1] else
                        real([sys.executable, "-c", "pass"]) if cmd[:2] == ["docker", "info"] else real(cmd, **kw))
    monkeypatch.setattr(local_checks.shutil, "which", lambda name: "/usr/bin/" + name)
    tests = local_checks.command("tests")
    assert tests[:2] == ["docker", "run"] and "python:3.13-slim" in tests  # the Tests workflow's Python
    assert "-r requirements.lock" in tests[-1] and "python -m pytest -q" in tests[-1]
    assert "libgl1 libglib2.0-0" in tests[-1] and "nodejs" in tests[-1]  # as the Tests workflow installs
    audit = local_checks.command("Dependencies (pip-audit)")
    assert audit[:2] == ["docker", "run"] and "pip-audit --requirement requirements.lock" in audit[-1]
    assert local_checks.command("Lint (ruff)") == local_checks.CHECKS["Lint (ruff)"]  # no packages needed
    monkeypatch.setattr(local_checks, "run", real)
    assert local_checks.command("tests") == local_checks.CHECKS["tests"]  # packages installed here


def test_tests_and_pip_audit_are_left_out_without_packages_or_docker(monkeypatch):
    real = local_checks.run
    monkeypatch.setattr(local_checks, "run", lambda cmd, **kw: real([sys.executable, "-c", "raise SystemExit(1)"])
                        if cmd[0] == "docker" or ("-c" in cmd and "import onnxruntime" in cmd[-1]) else real(cmd, **kw))
    assert local_checks.command("tests") is None and local_checks.command("Dependencies (pip-audit)") is None
    assert local_checks.command("Secrets (gitleaks)") == local_checks.CHECKS["Secrets (gitleaks)"]  # never left out
