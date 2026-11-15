#!/bin/sh
# Start sebagai root: pastikan /data bisa ditulis user app (folder bind mount dari host
# sering dibuat Docker dengan pemilik root), lalu turunkan hak akses ke UID 1000.
set -e

APP_UID=1000
APP_GID=1000

mkdir -p /data
if [ "$(stat -c %u /data)" != "$APP_UID" ]; then
    echo "entrypoint: memperbaiki kepemilikan /data -> $APP_UID:$APP_GID"
    chown -R "$APP_UID:$APP_GID" /data || echo "entrypoint: PERINGATAN gagal chown /data" >&2
fi

export HOME=/home/appuser
exec setpriv --reuid="$APP_UID" --regid="$APP_GID" --clear-groups "$@"
