#!/bin/sh
# Runs as root just long enough to make sure the data folder belongs to the
# app user (PUID/PGID, default 1000), then starts the app as that user.
set -e
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
mkdir -p /srv/data
if [ "$(id -u)" = "0" ]; then
    # Only when the folder belongs to someone else (a new folder, or PUID/PGID changed): going through thousands
    # of card pictures on every start makes it slow.
    if [ "$(stat -c %u:%g /srv/data)" != "$PUID:$PGID" ]; then
        chown -R "$PUID:$PGID" /srv/data
    fi
    exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
fi
exec "$@"
