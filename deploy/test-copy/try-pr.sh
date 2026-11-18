#!/bin/sh
# Builds a pull request (or main, or one commit) into the test image and restarts only the test copy.
#   sudo ./try-pr.sh 70           pull request 70, its newest commit
#   sudo ./try-pr.sh 70 1367e55   pull request 70 at an earlier commit
#   sudo ./try-pr.sh main         the newest main
set -eu
cd "$(dirname "$0")"
REPO="${DEXSTASH_REPO:-mags-ob/dexSTASH}"
usage() { echo "Usage: $0 <pull request number> [commit] | main" >&2; exit 1; }

case "${1:-}" in
  main)          url="https://github.com/$REPO/archive/refs/heads/main.tar.gz"; label="main" ;;
  ""|*[!0-9]*)   usage ;;
  *)             url="https://github.com/$REPO/archive/refs/pull/$1/head.tar.gz"; label="PR $1" ;;
esac
if [ -n "${2:-}" ]; then
  case "$2" in *[!0-9a-f]*) usage ;; esac
  [ ${#2} -ge 7 ] || usage
  url="https://github.com/$REPO/archive/$2.tar.gz"
fi

echo "Downloading $label ..."
rm -rf src.new && mkdir src.new
if command -v curl >/dev/null 2>&1; then
  curl -fsSL -o src.tar.gz "$url" || true
else
  wget -qO src.tar.gz "$url" || true
fi
if ! tar -xzf src.tar.gz -C src.new --strip-components=1 2>/dev/null || [ ! -f src.new/Dockerfile ]; then
  echo "Couldn't download $label${2:+ at commit $2}. Check the pull request number (not an issue) and the commit." >&2
  rm -rf src.new src.tar.gz; exit 1
fi
# git archive stores the commit in the file's header: shows which commit the test copy runs
commit=$(gzip -dc src.tar.gz | head -c 2048 | grep -ao 'comment=[0-9a-f]\{40\}' | cut -c9-15 || true)
rm -f src.tar.gz
rm -rf src && mv src.new src

version=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/app/__init__.py)
echo "Building $label (version $version, commit ${commit:-unknown}) ..."
docker build -t dexstash:test src

echo "APP_NAME=Test $label ${commit}" > pr.env
mkdir -p data
docker compose up -d --force-recreate app

echo
echo "The test copy now runs $label (version $version, commit ${commit:-unknown})."
echo "If it won't start after a newer one ran (database too new): sudo ./reset-test-data.sh"
