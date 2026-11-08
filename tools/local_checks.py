"""Runs the pull request checks on your own machine and reports each result to GitHub, for when GitHub Actions
is switched off for the repository.

    python tools/local_checks.py          # the pull request for the branch you're on
    python tools/local_checks.py 50       # pull request #50 (check out its branch first)

Each check is reported on the pull request's last commit under the same name as its GitHub Actions job, so a
ruleset that requires those checks (from any source) accepts it. Needs the GitHub CLI signed in (`gh auth
login`), gitleaks, `pip install ruff`, Docker (or the project's packages), and DEXSTASH_PRIVATE_WORDS set the
same as the repository secret. The tests and pip-audit run directly when the project's packages are installed,
otherwise in Docker; where neither works they're left out and not reported (they aren't required for a merge).
Any other check that can't run is reported as failed, never as passed.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

# Name on GitHub (the Actions job's check name) -> the command that runs it.
CHECKS = {
    "Lint (ruff)": [PY, "-m", "ruff", "check", "."],
    "Core rules and spec": [PY, "tools/check_rules.py", "--spec"],
    "Secrets (gitleaks)": ["gitleaks", "git", "--no-banner", "--redact", "."],
    "Dependencies (pip-audit)": [PY, "-m", "pip_audit", "--requirement", "requirements.lock",
                                 "--progress-spinner", "off"],
    "tests": [PY, "-m", "pytest", "-q"],
}

# Where the project's packages can't be installed (onnxruntime has nothing for Intel Macs), the tests and
# pip-audit run in a Linux container instead, set up like the GitHub Actions jobs. Downloads are kept in a volume.
SETUP = ("apt-get update -qq && apt-get install -y -qq --no-install-recommends libgl1 libglib2.0-0 nodejs git"
         " >/dev/null && git config --global --add safe.directory /src && ")
IN_DOCKER = {
    "tests": SETUP + "pip install -q --root-user-action=ignore -r requirements.lock pytest==9.1.1"
                     " && python -m pytest -q -p no:cacheprovider",
    "Dependencies (pip-audit)": "pip install -q --root-user-action=ignore pip-audit==2.10.1"
                                " && pip-audit --requirement requirements.lock --progress-spinner off",
}


def docker(script: str) -> list[str]:
    """A command that runs a shell script in the Python image GitHub Actions uses, with this folder as /src."""
    return ["docker", "run", "--rm", "-v", f"{ROOT}:/src", "-w", "/src", "-v", "dexstash-pip-cache:/root/.cache/pip",
            "python:3.13-slim", "sh", "-c", script]


def command(name: str) -> list[str] | None:
    """How to run a check here: directly, or in Docker when it needs packages this machine can't install. None
    when neither works: those two checks aren't required for a merge, so they're left out rather than failed."""
    if name in IN_DOCKER and run([PY, "-c", "import onnxruntime, fastapi, pytest"]).returncode:
        docker_ok = shutil.which("docker") and run(["docker", "info"]).returncode == 0
        return docker(IN_DOCKER[name]) if docker_ok else None
    return CHECKS[name]


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """A command's result, run from the repository root, with its output captured as text."""
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, **kw)


def gh(*args: str) -> str:
    """What a GitHub CLI command prints; stops the script if it fails."""
    out = run(["gh", *args])
    if out.returncode:
        sys.exit(f"gh {' '.join(args[:2])} failed: {out.stderr.strip()}")
    return out.stdout


def pull_request(number: str | None) -> dict:
    """The pull request's number, commits, branch and description."""
    fields = "number,headRefOid,baseRefOid,headRefName,body,url"
    return json.loads(gh("pr", "view", *([number] if number else []), "--json", fields))


def event_file(pr: dict, folder: str) -> str:
    """A GitHub Actions event file for the spec rule in check_rules.py, which reads the pull request from it."""
    path = Path(folder) / "event.json"
    path.write_text(json.dumps({"pull_request": {
        "base": {"sha": pr["baseRefOid"]}, "head": {"sha": pr["headRefOid"], "ref": pr["headRefName"]},
        "body": pr["body"]}}))
    return str(path)


def report(repo: str, sha: str, name: str, state: str, description: str) -> None:
    """Sets one check's status on a commit."""
    gh("api", "-X", "POST", f"repos/{repo}/statuses/{sha}", "-f", f"state={state}", "-f", f"context={name}",
       "-f", f"description={description[:140]}")


def fetch_base(pr: dict) -> None:
    """Fetches the pull request's base commit, which the spec rule compares with."""
    run(["git", "fetch", "--quiet", "origin", pr["baseRefOid"]])


def problem_before_running(pr: dict) -> str | None:
    """Why the checks can't stand for the pull request's last commit, or None when they can."""
    head = run(["git", "rev-parse", "HEAD"]).stdout.strip()
    if head != pr["headRefOid"]:
        return (f"You have {head[:7]} checked out but the pull request's last commit is {pr['headRefOid'][:7]}. "
                "Push, or pull, so they match.")
    if run(["git", "status", "--porcelain", "--untracked-files=no"]).stdout.strip():
        return "There are changes that aren't committed. Commit or stash them first."
    return None


def main(argv: list[str]) -> int:
    pr = pull_request(argv[0] if argv else None)
    if problem := problem_before_running(pr):
        print(problem)
        return 1
    repo = json.loads(gh("repo", "view", "--json", "nameWithOwner"))["nameWithOwner"]
    fetch_base(pr)
    sha = pr["headRefOid"]
    print(f"Checking {pr['url']} at {sha[:7]}")
    commands = {name: command(name) for name in CHECKS}
    for name, cmd in commands.items():
        if cmd is None:
            print(f"- {name}: not run here (needs the project's packages or Docker), so not reported")
        else:
            report(repo, sha, name, "pending", "Running on a contributor's machine")
    failed = []
    with tempfile.TemporaryDirectory() as folder:
        env = {**os.environ, "GITHUB_EVENT_NAME": "pull_request", "GITHUB_EVENT_PATH": event_file(pr, folder)}
        for name, cmd in commands.items():
            if cmd is None:
                continue
            print(f"- {name} ... ", end="", flush=True)
            if not env.get("DEXSTASH_PRIVATE_WORDS") and name == "Core rules and spec":
                ok, note = False, "DEXSTASH_PRIVATE_WORDS isn't set"
            elif shutil.which(cmd[0]) is None:
                ok, note = False, f"{cmd[0]} isn't installed"
            else:
                out = run(cmd, env=env)
                ok = out.returncode == 0
                note = "Passed" if ok else "Failed"
                if not ok:
                    print("failed\n" + (out.stdout + out.stderr)[-4000:])
            if ok:
                print("passed")
            elif note != "Failed":
                print(note)
            report(repo, sha, name, "success" if ok else "failure", f"{note} (run locally)")
            failed += [] if ok else [name]
    print(f"Failed: {', '.join(failed)}" if failed else "All checks passed and were reported.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
