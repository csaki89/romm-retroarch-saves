#!/bin/sh
# Install the romm-sync watcher into supervisord at container start.
#
# The container's writable layer is lost when the container is recreated, but
# $HOME persists. Keep this script and the .conf template in $HOME (e.g.
# ~/init.d/) and run it from whatever runs your startup scripts. It is
# idempotent: safe to run on every start.
#
# Assumptions (adjust the variables below if your container differs):
#   - supervisord includes /etc/supervisor/conf.d/*.conf (Debian default)
#   - this script runs as root (needed to write /etc and call supervisorctl)
set -eu

SYNC_USER="${SYNC_USER:-}"                       # user that owns the RetroArch data
SUPERVISOR_CONF_DIR="${SUPERVISOR_CONF_DIR:-/etc/supervisor/conf.d}"
HERE="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE="${TEMPLATE:-$HERE/romm-retroarch-watch.conf}"

if [ -z "$SYNC_USER" ]; then
    # default: owner of the directory this script lives in
    SYNC_USER="$(stat -c %U "$HERE")"
fi
SYNC_HOME="$(getent passwd "$SYNC_USER" | cut -d: -f6)"

if [ ! -x "$SYNC_HOME/.venvs/romm-sync/bin/romm-sync" ]; then
    echo "romm-sync not found in $SYNC_HOME/.venvs/romm-sync - create the venv first" >&2
    exit 1
fi
if [ ! -f "$TEMPLATE" ]; then
    echo "template not found: $TEMPLATE (copy packaging/supervisor/romm-retroarch-watch.conf next to this script)" >&2
    exit 1
fi

install -d -o "$SYNC_USER" "$SYNC_HOME/.local/state/romm-retroarch-sync"
sed -e "s|@USER@|$SYNC_USER|g" -e "s|@HOME@|$SYNC_HOME|g" "$TEMPLATE" \
    > "$SUPERVISOR_CONF_DIR/romm-retroarch-watch.conf"

supervisorctl reread
supervisorctl update
echo "romm-retroarch-watch installed; status:"
supervisorctl status romm-retroarch-watch || true
