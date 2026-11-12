#!/usr/bin/env bash
# Local scan for secrets and personal words. Free, no GitHub Actions needed.
#   tools/scan.sh            scan staged changes (default; used by the pre-commit hook)
#   tools/scan.sh --branch   scan everything this branch adds compared with main
#   tools/scan.sh --all      scan every tracked file
# Personal words live in rules/private-words.txt (git-ignored): one word or
# phrase per line, matched case-insensitively. Copy rules/private-words.example.txt.
# If an executable tools/project-scan.sh exists, it runs too, with the same flag.
set -u
cd "$(git rev-parse --show-toplevel)" || exit 2
mode="${1:-}"
fail=0

case "$mode" in
  --all) diff_cmd=(git diff --no-color 4b825dc642cb6eb9a060e54bf8d69288fbee4904 --) ;;
  --branch) diff_cmd=(git diff --no-color main...HEAD --) ;;
  *) diff_cmd=(git diff --no-color --cached --) ;;
esac

if command -v gitleaks >/dev/null 2>&1; then
  case "$mode" in
    --all) gitleaks detect --no-banner --redact -v || fail=1 ;;
    --branch) gitleaks detect --no-banner --redact -v --log-opts="main..HEAD" || fail=1 ;;
    *) gitleaks protect --staged --no-banner --redact -v || fail=1 ;;
  esac
else
  echo "note: gitleaks not installed (brew install gitleaks); secret patterns not checked." >&2
fi

words=rules/private-words.txt
if [ -f "$words" ]; then
  added=$("${diff_cmd[@]}" | grep -E '^\+[^+]' || true)
  while IFS= read -r w; do
    [ -z "$w" ] && continue
    case "$w" in \#*) continue ;; esac
    if printf '%s\n' "$added" | grep -qiF -- "$w"; then
      echo "personal word found in added lines: a word from $words" >&2
      fail=1
    fi
  done < "$words"
else
  echo "note: $words not found; personal words not checked." >&2
fi

# Optional project-specific checks (for example extra patterns). Receives the same mode flag.
if [ -x tools/project-scan.sh ]; then
  tools/project-scan.sh ${mode:+"$mode"} || fail=1
fi

[ "$fail" -eq 0 ] && echo "scan: clean" || echo "scan: problems found" >&2
exit "$fail"
