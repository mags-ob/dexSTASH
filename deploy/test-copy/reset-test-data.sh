#!/bin/sh
# Replaces the test copy's data with a fresh copy of the live data. The live app keeps running:
# its databases are copied with SQLite's own backup inside the live container, and nothing in the
# live data folder is written. Backups, logs, scan photos, API keys and the secret key aren't copied.
# The copy has the nightly price update turned off.
#   sudo ./reset-test-data.sh
set -eu
cd "$(dirname "$0")"
LIVE="${LIVE_CONTAINER:-dexstash}"
TMP=/tmp/test-copy

docker inspect "$LIVE" >/dev/null 2>&1 || { echo "No container named $LIVE (set LIVE_CONTAINER)." >&2; exit 1; }

echo "Stopping the test copy ..."
docker compose stop app

echo "Copying the databases inside $LIVE ..."
docker exec -i "$LIVE" python - "$TMP" <<'PY'
import pathlib, shutil, sqlite3, sys
tmp = pathlib.Path(sys.argv[1])
shutil.rmtree(tmp, ignore_errors=True)
tmp.mkdir()
for src in sorted(pathlib.Path("/srv/data").glob("*.db")):
    with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as a, sqlite3.connect(tmp / src.name) as b:
        a.backup(b)
    print("  ", src.name)
with sqlite3.connect(tmp / "dexstash.db") as c:
    c.execute("INSERT INTO app_meta (key, value) VALUES ('nightly_prices', 'off') "
              "ON CONFLICT(key) DO UPDATE SET value = excluded.value")
PY

echo "Replacing the test data ..."
mkdir -p data
# keep the test copy's own secret key and any API key you entered in it
find data -mindepth 1 -maxdepth 1 ! -name secret_key ! -name api_keys.json -exec rm -rf {} +
docker exec "$LIVE" tar -C /srv/data -cf - --no-wildcards-match-slash \
  --exclude='./*.db' --exclude='./*.db-wal' --exclude='./*.db-shm' --exclude='./*.db-journal' \
  --exclude=./backups --exclude=./logs --exclude=./scans \
  --exclude=./secret_key --exclude=./api_keys.json --exclude=./setup-code.txt . | tar -C data -xf -
docker exec "$LIVE" tar -C "$TMP" -cf - . | tar -C data -xf -
docker exec "$LIVE" rm -rf "$TMP"

echo "Starting the test copy ..."
docker compose up -d app
echo "Done. Sign in with your usual account; the nightly price update is off in the test copy."
