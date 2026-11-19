#!/bin/sh
# Fix ownership of the data volume, then drop privileges to the app user.
#
# A bind mount such as ./data:/data is created by Docker as root:root when the
# host directory does not exist yet, which the unprivileged app cannot write to.
# Running the entrypoint as root lets us fix that before the app starts.
set -eu

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
DATA_DIR="${DATA_DIR:-/data}"

# Started with `user:` / `--user`: no privileges to drop, run as given.
if [ "$(id -u)" != "0" ]; then
    exec "$@"
fi

mkdir -p "$DATA_DIR"

# Only walk the tree when something is wrong, so large archives start fast.
needs_chown=0
for p in "$DATA_DIR" "$DATA_DIR"/*; do
    [ -e "$p" ] || continue
    if [ "$(stat -c %u:%g "$p")" != "$PUID:$PGID" ]; then
        needs_chown=1
        break
    fi
done
if [ "$needs_chown" = "1" ]; then
    echo "entrypoint: setting owner of $DATA_DIR to $PUID:$PGID"
    chown -R "$PUID:$PGID" "$DATA_DIR"
fi

# Keep access to mapped GPU device nodes (VAAPI/QSV via /dev/dri).
groups="$PGID"
for dev in /dev/dri/*; do
    [ -e "$dev" ] || continue
    gid="$(stat -c %g "$dev")"
    case ",$groups," in *",$gid,"*) ;; *) groups="$groups,$gid" ;; esac
done

export HOME=/home/app
exec setpriv --reuid="$PUID" --regid="$PGID" --groups="$groups" --inh-caps=-all -- "$@"
