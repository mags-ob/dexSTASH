"""Checks the core rules (rules/core.md) that a script can check, on every file git tracks.

    python tools/check_rules.py            # the whole repository (tools/scan.sh runs this too)

It prints each problem with its file and line and exits with 1 when there is any. The checks:

- Nothing personal: paths from someone's own machine or home server, e-mail addresses and DuckDNS names
  that aren't examples, and any word listed in rules/private-words.txt (git-ignored, one per line) or in
  the DEXSTASH_PRIVATE_WORDS environment variable (comma separated). The list never goes in the repository,
  because this check is public too.
- Nothing that must never be committed: .env files, databases, the data folder, models and bundles.
- Every commit's author, committer and message, the same way, plus links to private pages (an AI assistant's
  sessions or projects). A merge made on github.com is written with the merger's profile name and e-mail, so
  this is what notices an account that doesn't keep its e-mail private. Attribution lines (Co-Authored-By,
  "Generated with ...") fail too: AGENTS.md, one team.

A line that has to break a pattern on purpose (a test of this check, say) ends with the marker
`rules: allow`.
"""

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
# Attribution lines in a commit message (AGENTS.md: one team; the author field says who wrote it).
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
    """The words in rules/private-words.txt (one per line) and DEXSTASH_PRIVATE_WORDS (comma separated),
    each matched as a whole word in any case."""
    words = [w.strip() for w in os.environ.get("DEXSTASH_PRIVATE_WORDS", "").split(",") if w.strip()]
    listed = ROOT / "rules" / "private-words.txt"
    if listed.is_file():
        words += [w.strip() for w in listed.read_text(encoding="utf-8").splitlines()
                  if w.strip() and not w.lstrip().startswith("#")]
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


def main(argv: list[str]) -> int:
    words = private_words()
    problems = check_files(tracked_files(), words) + check_history(words)
    for p in problems:
        print(p)
    print(f"{len(problems)} problem(s)" if problems else "Core rules check passed.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
