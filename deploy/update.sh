#!/usr/bin/env bash
# Ship a new revision to a box bootstrap-ec2.sh has already provisioned.
#
#   sudo bash /opt/sentinel/app/deploy/update.sh
#   sudo SENTINEL_BRANCH=sentinel-v2 bash /opt/sentinel/app/deploy/update.sh
#
# Pull, reinstall if the pins moved, restart, and prove it came back. It
# stops on the first failure and leaves the old process running if the new
# code will not import — the restart is the last thing it does, and the
# health check after it is what decides whether to say "deployed".
#
# The SQLite database in backend/var survives all of this. It holds the
# approvals, the executions, the audit log and the event journal; nothing
# here touches it, and nothing should without a backup first.

set -euo pipefail

APP_DIR=/opt/sentinel/app
VENV_DIR=/opt/sentinel/venv

log() { printf '\n\033[1m── %s\033[0m\n' "$*"; }

# Every git call runs as `sentinel`, never as root. The checkout is owned by
# `sentinel`, and git refuses to operate on a repository owned by another user
# — "detected dubious ownership" — which would fail this script on its first
# line for a reason that has nothing to do with the deploy.
git_as() { sudo -u sentinel git -C "${APP_DIR}" "$@"; }

SENTINEL_BRANCH="${SENTINEL_BRANCH:-$(git_as rev-parse --abbrev-ref HEAD)}"

log "before: $(git_as rev-parse --short HEAD)"

log "fetch ${SENTINEL_BRANCH}"
git_as fetch --depth 1 origin "${SENTINEL_BRANCH}"
# FETCH_HEAD rather than origin/BRANCH: the bootstrap clone is shallow, and a
# remote-tracking ref for a branch it was not cloned on may simply not exist.
# -B moves the local branch to it whether or not it already pointed anywhere.
git_as checkout -q -B "${SENTINEL_BRANCH}" FETCH_HEAD

log "dependencies"
# Cheap when nothing moved: pip compares the pins against what is installed
# and does nothing. Worth running unconditionally rather than guessing from a
# diff, because a missed pin fails at the first request instead of here.
sudo -u sentinel "${VENV_DIR}/bin/pip" install --quiet -r "${APP_DIR}/backend/requirements.txt"
sudo -u sentinel "${VENV_DIR}/bin/pip" install --quiet --no-deps -e "${APP_DIR}/backend"

log "import check"
# Catch a syntax error or a missing module here, with the old process still
# serving, rather than after the restart with nothing serving.
sudo -u sentinel env -C "${APP_DIR}" "${VENV_DIR}/bin/python" -c "import api.main, sentinel" \
    || { echo "the new revision does not import; nothing was restarted" >&2; exit 1; }

log "unit and site"
# Re-installed every time so a change to either template ships with the code
# that expects it.
install -m 0644 "${APP_DIR}/deploy/sentinel-api.service" /etc/systemd/system/sentinel-api.service
systemctl daemon-reload

log "restart"
systemctl restart sentinel-api

log "health"
# The lifespan builds the graph client and the database before it answers, so
# give it a few seconds rather than asking once and calling it dead.
for attempt in $(seq 1 15); do
    if curl -fsS --max-time 3 http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
        echo "healthy after ${attempt}s: $(git_as rev-parse --short HEAD)"
        # /ready probes the graph and the model. It can legitimately be 503
        # for the forty-five seconds a sleeping Savanna workspace takes to
        # wake, so it is reported rather than enforced.
        curl -fsS --max-time 10 http://127.0.0.1:8000/api/ready \
            || echo "note: /ready is not green yet — usually a cold workspace; check again in a minute"
        exit 0
    fi
    sleep 1
done

echo "did not become healthy in 15s; journalctl -u sentinel-api -n 50" >&2
systemctl --no-pager status sentinel-api || true
exit 1
