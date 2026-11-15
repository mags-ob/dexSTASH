#!/usr/bin/env bash
# dexSTASH's own scan, run by tools/scan.sh with the same flag. Runs the core rules check
# (personal details, files that must never be committed, commit authors and messages).
cd "$(git rev-parse --show-toplevel)" || exit 2
py=python3
[ -x .venv/bin/python ] && py=.venv/bin/python
exec "$py" tools/check_rules.py
