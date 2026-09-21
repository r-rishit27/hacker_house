#!/usr/bin/env bash
# Provision a bare EC2 instance into a running Sentinel API. Run once, as root.
#
#   sudo SENTINEL_REPO=https://github.com/r-rishit27/hacker_house.git \
#        SENTINEL_DOMAIN=api.example.com \
#        ACME_EMAIL=you@example.com \
#        bash deploy/bootstrap-ec2.sh
#
# Targets Ubuntu 24.04 LTS, which ships Python 3.12 — the version the backend
# is pinned against. On Amazon Linux 2023 the package names differ and there
# is no python3.12 in the default repositories; deploy/README.md says what to
# change.
#
# It is idempotent: run it again after changing a template and it re-installs
# the unit and the site without touching the checkout or the database.
#
# What it does NOT do is write credentials. /etc/sentinel/sentinel.env is
# created empty and the service is left stopped until you fill it in, because
# a secret passed through EC2 user-data is a secret in the instance metadata
# and in the console, readable by anything that can reach 169.254.169.254.

set -euo pipefail

SENTINEL_REPO="${SENTINEL_REPO:?set SENTINEL_REPO to the git URL to deploy}"
SENTINEL_BRANCH="${SENTINEL_BRANCH:-main}"
SENTINEL_DOMAIN="${SENTINEL_DOMAIN:?set SENTINEL_DOMAIN to the hostname the API is reached at, e.g. api.example.com or 12.34.56.78.sslip.io}"
ACME_EMAIL="${ACME_EMAIL:-}"

APP_DIR=/opt/sentinel/app
VENV_DIR=/opt/sentinel/venv
ENV_FILE=/etc/sentinel/sentinel.env

log() { printf '\n\033[1m── %s\033[0m\n' "$*"; }

log "packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# python3.12-venv is separate from python3.12 on Ubuntu and `python -m venv`
# fails with a message about ensurepip without it.
apt-get install -y -qq \
    git curl ca-certificates \
    python3.12 python3.12-venv \
    nginx certbot python3-certbot-nginx

log "service account"
# No login shell and no home of its own beyond /opt/sentinel: this account
# exists to own a checkout and run one process.
if ! id sentinel >/dev/null 2>&1; then
    useradd --system --create-home --home-dir /opt/sentinel --shell /usr/sbin/nologin sentinel
fi
install -d -o sentinel -g sentinel /opt/sentinel

log "checkout"
# Every git call runs as `sentinel`, never as root: the checkout is owned by
# `sentinel` and git refuses to operate on a repository owned by another user
# ("detected dubious ownership").
#
# --no-single-branch alongside --depth 1: a shallow clone is otherwise pinned
# to the one branch, and the first time someone deploys a different one the
# fetch succeeds and the checkout fails. Checking out FETCH_HEAD rather than a
# remote-tracking name works whether or not that ref exists locally.
if [[ -d "${APP_DIR}/.git" ]]; then
    sudo -u sentinel git -C "${APP_DIR}" fetch --depth 1 origin "${SENTINEL_BRANCH}"
    sudo -u sentinel git -C "${APP_DIR}" checkout -q -B "${SENTINEL_BRANCH}" FETCH_HEAD
else
    sudo -u sentinel git clone --depth 1 --no-single-branch \
        --branch "${SENTINEL_BRANCH}" "${SENTINEL_REPO}" "${APP_DIR}"
fi

log "python environment"
if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    sudo -u sentinel python3.12 -m venv "${VENV_DIR}"
fi
sudo -u sentinel "${VENV_DIR}/bin/pip" install --quiet --upgrade pip
sudo -u sentinel "${VENV_DIR}/bin/pip" install --quiet -r "${APP_DIR}/backend/requirements.txt"
# Editable, and not for convenience: Settings.ROOT is the settings module's
# path up three directories, and that is what anchors cases/, case_pack.csv,
# runs/, exploration/rings.json and backend/var/. A regular install moves the
# module into site-packages and every one of those paths points at nothing.
sudo -u sentinel "${VENV_DIR}/bin/pip" install --quiet --no-deps -e "${APP_DIR}/backend"

log "writable state"
# Create each directory in its own call and then chown the trees.
#
# `install -d -o sentinel -g sentinel a/b/c` does NOT do what it looks like it
# does: it creates the intermediate directories too, but applies the ownership
# only to the leaf. Asking for .../backend/var/corpus in one call left
# .../backend/var owned by root, and SQLite then failed to open the database
# with "unable to open database file" — a permissions error that reads like a
# path error and sent the first debug in entirely the wrong direction.
install -d -o sentinel -g sentinel "${APP_DIR}/backend/var"
install -d -o sentinel -g sentinel "${APP_DIR}/backend/var/corpus"
install -d -o sentinel -g sentinel "${APP_DIR}/runs"
chown -R sentinel:sentinel "${APP_DIR}/backend/var" "${APP_DIR}/runs"

log "credentials"
install -d -m 0750 -o root -g sentinel /etc/sentinel
if [[ ! -f "${ENV_FILE}" ]]; then
    install -m 0640 -o root -g sentinel "${APP_DIR}/deploy/sentinel.env.example" "${ENV_FILE}"
    NEEDS_CREDENTIALS=1
else
    NEEDS_CREDENTIALS=0
fi

log "systemd unit"
install -m 0644 "${APP_DIR}/deploy/sentinel-api.service" /etc/systemd/system/sentinel-api.service
systemctl daemon-reload
systemctl enable sentinel-api

log "nginx"
sed "s/API_DOMAIN_HERE/${SENTINEL_DOMAIN}/g" \
    "${APP_DIR}/deploy/nginx-sentinel.conf" > /etc/nginx/sites-available/sentinel
ln -sf /etc/nginx/sites-available/sentinel /etc/nginx/sites-enabled/sentinel
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

log "tls"
# Certbot rewrites the site in place: listen 443, the certificate paths, and
# the redirect from 80. It also installs a renewal timer. If this fails, the
# usual cause is that ${SENTINEL_DOMAIN} does not resolve to this instance
# yet, or that port 80 is not open in the security group — Let's Encrypt has
# to reach it to validate.
if [[ -n "${ACME_EMAIL}" ]]; then
    certbot --nginx --non-interactive --agree-tos --redirect \
        -m "${ACME_EMAIL}" -d "${SENTINEL_DOMAIN}"
else
    certbot --nginx --non-interactive --agree-tos --redirect \
        --register-unsafely-without-email -d "${SENTINEL_DOMAIN}"
fi

if [[ "${NEEDS_CREDENTIALS}" == "1" ]]; then
    cat <<EOF

────────────────────────────────────────────────────────────────────────────
The box is provisioned and the service is NOT running yet, on purpose.

  1. Fill in ${ENV_FILE}
       - TG_HOST, TG_SECRET
       - OPENAI_API_KEY, OPENAI_MODEL, OPENAI_EMBEDDING_MODEL
       - CORS_ORIGINS, set to the Amplify branch URL, verbatim
  2. sudo systemctl start sentinel-api
  3. curl https://${SENTINEL_DOMAIN}/api/health
     curl https://${SENTINEL_DOMAIN}/api/ready

The service refuses to start while any of the five required settings is
missing. That is the design: a process that starts and then fails every run
is worse than one that will not start.
────────────────────────────────────────────────────────────────────────────
EOF
else
    systemctl restart sentinel-api
    log "restarted; check: curl https://${SENTINEL_DOMAIN}/api/health"
fi
