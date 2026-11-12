#!/usr/bin/env bash
# Create or update the labels in .github/labels.yml on the current repo.
# Needs the GitHub CLI (gh), signed in with write access. Run it once.
set -eu
cd "$(git rev-parse --show-toplevel)"
name="" color="" desc=""
flush() {
  [ -z "$name" ] && return
  gh label create "$name" --color "$color" --description "$desc" --force
}
while IFS= read -r line; do
  case "$line" in
    "- name: "*) flush; name="${line#- name: }"; color=""; desc="" ;;
    "  color: "*) color="${line#  color: }" ;;
    "  description: "*) desc="${line#  description: }" ;;
  esac
done < .github/labels.yml
flush
