#!/bin/sh
# Runs as root just long enough to make sure the data folder belongs to the
# app user (PUID/PGID, default 1000), then starts the app as that user.
set -e
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
mkdir -p /srv/data
if [ "$(id -u)" = "0" ]; then
    chown -R "$PUID:$PGID" /srv/data
    exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
fi
exec "$@"
