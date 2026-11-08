"""Checks the core rules (rules/core.md) that a script can check, on every file git tracks.

    python tools/check_rules.py            # the whole repository
    python tools/check_rules.py --spec     # in a pull request on GitHub Actions: the spec rule too

It prints each problem with its file and line and exits with 1 when there is any. The checks:

- Nothing personal: paths from someone's own machine or home server, e-mail addresses and DuckDNS names
  that aren't examples, and any word listed in the DEXSTASH_PRIVATE_WORDS environment variable (comma
  separated). That list lives in a GitHub Actions secret, never in the repository, because this check is
  public too.
- Nothing that must never be committed: .env files, databases, the data folder, models and bundles.
- Every commit's author, committer and message, the same way, plus links to private pages (an AI assistant's
  sessions or projects). A merge made on github.com is written with the merger's profile name and e-mail, so
  this is what notices an account that doesn't keep its e-mail private. Attribution lines (Co-Authored-By,
  "Generated with ...") fail too: rules/project.md, one team.
- With --spec: a pull request that changes the app either changes a spec in docs/specs/ or says why not on a
  line starting "Spec:" in its description, and its branch is named for its change (feature/short-name).

A line that has to break a pattern on purpose (a test of this check, say) ends with the marker
`rules: allow`.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOW = "rules: allow"

PATTERNS = [
    ("a path on someone's home server", re.compile(r"/volume\d+/")),
    ("a path in someone's home folder", re.compile(r"/Users/(?!Shared/)[A-Za-z][\w.-]*/|[A-Za-z]:\\+Users\\+[\w.-]+|/home/(?!user/)[a-z][\w.-]*/")),
    ("an e-mail address", re.compile(r"[\w.%+-]+@(?!(?:[\w-]+\.)*example\.(?:com|org|net)\b|users\.noreply\.github\.com\b|github\.com\b|anthropic\.com\b)[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}\b")),
    ("a DuckDNS name", re.compile(r"(?<![\w.-])(?!your-name\.|example\.|your-subdomain\.)[a-z0-9-]+\.duckdns\.org\b", re.I)),
]

# Pages only their owner can open: links to them say nothing to a reader and point at someone's account.
PRIVATE_LINK = re.compile(r"claude\.ai/code/(?:session|project)|claude\.ai/project/")
# Attribution lines in a commit message (rules/project.md: one team; the author field says who wrote it).
ATTRIBUTION = re.compile(r"^\s*(?:Co-Authored-By|Signed-off-by|Claude-Session)\s*:|Generated (?:with|by) \[?Claude", re.I)

NEVER_COMMITTED = [
    ("an .env file", re.compile(r"(^|/)\.env$")),
    ("a database", re.compile(r"\.(db|sqlite|sqlite3)(-wal|-shm)?$")),
    ("the data folder", re.compile(r"^data/")),
    ("a model", re.compile(r"\.(onnx|pt|safetensors)$")),
    ("a bundle", re.compile(r"\.(tar\.gz|tgz|bundle)$")),
]

SKIP = {"LICENSE"}  # the licence text names its authors' addresses
BINARY = {".png", ".jpg", ".jpeg", ".ico", ".woff", ".woff2", ".gif", ".webp"}


def tracked_files() -> list[str]:
    """Every file git tracks, as paths from the repository root."""
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [p for p in out.decode().split("\0") if p]


def private_words() -> list[re.Pattern]:
    """The words in DEXSTASH_PRIVATE_WORDS, each matched as a whole word in any case."""
    words = [w.strip() for w in os.environ.get("DEXSTASH_PRIVATE_WORDS", "").split(",") if w.strip()]
    return [re.compile(r"(?<![\w-])" + re.escape(w) + r"(?![\w-])", re.I) for w in words]


def check_text(path: str, text: str, words: list[re.Pattern]) -> list[str]:
    """The problems in one file's text, one line each. A private word is never printed, only its place."""
    problems = []
    for n, line in enumerate(text.splitlines(), 1):
        if ALLOW in line:
            continue
        for what, pattern in PATTERNS:
            m = pattern.search(line)
            if m:
                problems.append(f"{path}:{n}: {what} ({m.group(0)})")
        if any(w.search(line) for w in words):
            problems.append(f"{path}:{n}: a word from the private list")
    return problems


def check_names(paths: list[str]) -> list[str]:
    """Tracked files that must never be committed."""
    return [f"{p}: {what} must never be committed" for p in paths for what, rule in NEVER_COMMITTED if rule.search(p)]


def check_files(paths: list[str], words: list[re.Pattern]) -> list[str]:
    """The problems in every tracked file: its name, then its text."""
    problems = check_names(paths)
    for p in paths:
        if p in SKIP or Path(p).suffix.lower() in BINARY:
            continue
        try:
            text = (ROOT / p).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        problems += check_text(p, text, words)
    return problems


def check_history(words: list[re.Pattern]) -> list[str]:
    """The problems in every commit behind the checked-out one: who wrote and committed it, and its message."""
    out = subprocess.run(["git", "log", "-z", "--format=%h%n%an <%ae>%n%cn <%ce>%n%B", "HEAD"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    problems = []
    for entry in filter(None, (e.strip("\n") for e in out.split("\0"))):
        sha, _, text = entry.partition("\n")
        problems += check_text(f"commit {sha}", text, words)
        for n, line in enumerate(text.splitlines(), 1):
            if ALLOW in line:
                continue
            if PRIVATE_LINK.search(line):
                problems.append(f"commit {sha}:{n}: a link to a private page")
            if n > 2 and ATTRIBUTION.search(line):  # lines 1-2 are the author and committer
                problems.append(f"commit {sha}:{n}: an attribution line (the author field says who wrote it)")
    return problems


def spec_problem(changed: list[str], description: str) -> str | None:
    """Why a pull request breaks the spec rule, or None when it keeps it."""
    app = [p for p in changed if p.startswith("app/")]
    if not app or any(p.startswith("docs/specs/") for p in changed):
        return None
    if re.search(r"^[ \t]*(?:[-*][ \t]*)?\**Spec\**[ \t]*:\**[ \t]*[^\s*]", description or "", re.M | re.I):
        return None
    return ("This pull request changes the app but no spec in docs/specs/. Update the spec, or add a line to "
            "the description starting \"Spec:\" that says why it doesn't change (for example \"Spec: no change, "
            "internal tidy-up\").")


BRANCH = re.compile(r"^(?:feature|fix|docs|chore|refactor|test)/[a-z0-9][a-z0-9-]*$")


def branch_problem(name: str) -> str | None:
    """Why a pull request's branch name breaks rules/project.md, or None when it keeps it."""
    if BRANCH.match(name):
        return None
    return (f"The branch {name} should be named for its change: feature/, fix/, docs/, chore/, refactor/ or "
            "test/, then a short name in lower case with dashes (for example feature/release-image-files).")


def pull_request() -> tuple[list[str], str, str]:
    """The changed files, description and branch name of the pull request GitHub Actions is running for."""
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    pr = event["pull_request"]
    base, head = pr["base"]["sha"], pr["head"]["sha"]
    out = subprocess.run(["git", "diff", "--name-only", f"{base}...{head}"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout
    return out.split(), pr.get("body") or "", pr["head"]["ref"]


def main(argv: list[str]) -> int:
    words = private_words()
    problems = check_files(tracked_files(), words) + check_history(words)
    if "--spec" in argv and os.environ.get("GITHUB_EVENT_NAME") == "pull_request":
        changed, description, branch = pull_request()
        problems += [p for p in (spec_problem(changed, description), branch_problem(branch)) if p]
    for p in problems:
        print(p)
    print(f"{len(problems)} problem(s)" if problems else "Core rules check passed.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
