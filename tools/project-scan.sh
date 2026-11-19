#!/usr/bin/env bash
# dexSTASH's own scan, run by tools/scan.sh with the same flag. Runs the core rules check
# (personal details, files that must never be committed, commit authors and messages).
cd "$(git rev-parse --show-toplevel)" || exit 2
# Windows has no python3, only python, and its python3 can be a shortcut to the Store that
# doesn't run, so use the first one that actually starts.
for py in .venv/bin/python .venv/Scripts/python.exe python3 python; do
  if "$py" -c "" >/dev/null 2>&1; then
    exec "$py" tools/check_rules.py
  fi
done
echo "project scan: Python not found; install Python 3 and run the scan again." >&2
exit 1
