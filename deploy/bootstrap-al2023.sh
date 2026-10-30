#!/usr/bin/env bash
# Provision a bare Amazon Linux 2023 instance into a running Sentinel API.
# Run once, as root. The Ubuntu twin of this is bootstrap-ec2.sh; the shape is
# identical and only the packaging differs.
#
#   sudo SENTINEL_DOMAIN=15.252.97.176.sslip.io \
#        ACME_EMAIL=you@example.com \
#        bash deploy/bootstrap-al2023.sh
#
# What differs from Ubuntu, and why each line looks the way it does:
#   - dnf, and python3.12 is in the amazonlinux repo, so the pins in
#     backend/requirements.txt resolve to exactly what they were tested on
#   - nginx is configured from /etc/nginx/conf.d/*.conf; there is no
#     sites-available/sites-enabled pair to symlink between
#   - certbot and python3-certbot-nginx are both packaged, so there is no EPEL
#     and no pip-installed certbot to maintain separately
#   - SELinux ships Permissive, so nginx proxying to loopback needs no boolean
#   - the small instance types have no swap and under a gigabyte of RAM, which
#     this script fixes because pip and the vector-index warm are the two
#     places that notice
#
# It is idempotent. It does NOT write credentials: a secret passed through EC2
# user-data lives in the instance metadata, readable by anything that can
# reach 169.254.169.254.

set -euo pipefail

SENTINEL_REPO="${SENTINEL_REPO:-https://github.com/r-rishit27/hacker_house.git}"
SENTINEL_BRANCH="${SENTINEL_BRANCH:-sentinel-v2}"
SENTINEL_DOMAIN="${SENTINEL_DOMAIN:?set SENTINEL_DOMAIN to the hostname the API is reached at, e.g. 15.252.97.176.sslip.io}"
ACME_EMAIL="${ACME_EMAIL:-}"
# Set SKIP_TLS=1 to provision everything and leave the certificate for later —
# which is what you want while the security group still has 443 shut.
SKIP_TLS="${SKIP_TLS:-0}"

APP_DIR=/opt/sentinel/app
VENV_DIR=/opt/sentinel/venv
ENV_FILE=/etc/sentinel/sentinel.env
PY=python3.12

log() { printf '\n\033[1m── %s\033[0m\n' "$*"; }

log "packages"
# Not `curl`: AL2023 ships curl-minimal, which already provides /usr/bin/curl,
# and asking for the full curl package makes dnf try to swap them and abort on
# a wall of "conflicts with curl provided by curl-minimal". ca-certificates is
# preinstalled for the same reason.
dnf install -y -q \
    git \
    "${PY}" "${PY}-pip" \
    nginx certbot python3-certbot-nginx

log "swap"
# 1 GiB of RAM and no swap: pip resolving numpy and the retriever warming
# 5,611 vectors are both fine in steady state and both spike. 2 GiB of swap on
# the root volume costs nothing and turns an OOM kill into a slow minute.
#
# The whole block is best-effort. Swap is an improvement, not a requirement,
# and a machine that will not take a swapfile is not a reason to abandon a
# provision that would otherwise succeed — hence the `|| true` rather than
# letting `set -e` end the script here.
#
# Note the absence of `mkswap -q`: util-linux on AL2023 has no such flag, and
# the only thing it buys elsewhere is silence.
add_swap() {
    if [[ -n "$(swapon --show)" ]]; then
        echo "swap already active; leaving it alone"
        return 0
    fi
    # A /swapfile that exists but is not active is the debris of an earlier
    # run that died between allocating and enabling. Format and enable it
    # rather than skipping, which would leave 2 GiB allocated and unused.
    if [[ ! -f /swapfile ]]; then
        fallocate -l 2G /swapfile 2>/dev/null \
            || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
    fi
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "2 GiB swap active"
}
add_swap || echo "could not add swap; continuing without it" >&2

log "service account"
if ! id sentinel >/dev/null 2>&1; then
    useradd --system --create-home --home-dir /opt/sentinel --shell /sbin/nologin sentinel
fi
install -d -o sentinel -g sentinel /opt/sentinel

log "checkout"
# Every git call runs as `sentinel`: the checkout is owned by it, and git
# refuses to operate on a repository owned by another user.
if [[ -d "${APP_DIR}/.git" ]]; then
    sudo -u sentinel git -C "${APP_DIR}" fetch --depth 1 origin "${SENTINEL_BRANCH}"
    sudo -u sentinel git -C "${APP_DIR}" checkout -q -B "${SENTINEL_BRANCH}" FETCH_HEAD
else
    sudo -u sentinel git clone --depth 1 --no-single-branch \
        --branch "${SENTINEL_BRANCH}" "${SENTINEL_REPO}" "${APP_DIR}"
fi

log "python environment"
if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
    sudo -u sentinel "${PY}" -m venv "${VENV_DIR}"
fi
sudo -u sentinel "${VENV_DIR}/bin/pip" install --quiet --upgrade pip
sudo -u sentinel "${VENV_DIR}/bin/pip" install --quiet -r "${APP_DIR}/backend/requirements.txt"
# Editable, and not for convenience. Settings.ROOT is the settings module's
# path up three directories, and it anchors cases/, case_pack.csv, runs/,
# exploration/rings.json and backend/var/. A regular install moves the module
# into site-packages, where "up three" is a directory in the venv, and all
# five of those paths silently point at nothing.
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

# The unit's MemoryMax is written for a 2 GiB box. On a smaller one it is
# above the machine's own total, which makes it no limit at all — so narrow it
# to something that still catches a leak. A drop-in rather than an edit, so
# the unit in the repository stays the one thing that describes the service.
TOTAL_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if (( TOTAL_MB < 2048 )); then
    install -d /etc/systemd/system/sentinel-api.service.d
    cat > /etc/systemd/system/sentinel-api.service.d/small-instance.conf <<EOF
# Written by bootstrap-al2023.sh: this machine has ${TOTAL_MB} MiB of RAM, so
# the unit's 1G ceiling would never be reached before the kernel's own OOM
# killer got there first. Steady state is 150-400 MiB.
[Service]
MemoryMax=700M
MemoryHigh=550M
EOF
    echo "small instance (${TOTAL_MB} MiB): MemoryMax narrowed to 700M"
fi

systemctl daemon-reload
systemctl enable -q sentinel-api

log "nginx"
# conf.d, not sites-available: that pair is a Debian convention and AL2023's
# nginx.conf has no `include sites-enabled/*`.
sed "s/API_DOMAIN_HERE/${SENTINEL_DOMAIN}/g" \
    "${APP_DIR}/deploy/nginx-sentinel.conf" > /etc/nginx/conf.d/sentinel.conf
nginx -t
systemctl enable -q nginx
systemctl restart nginx

log "tls"
if [[ "${SKIP_TLS}" == "1" ]]; then
    echo "SKIP_TLS=1 — certificate not requested."
    echo "When port 80 is open in the security group, run:"
    echo "  sudo certbot --nginx --redirect -d ${SENTINEL_DOMAIN}"
elif ! timeout 5 bash -c "</dev/tcp/127.0.0.1/80" 2>/dev/null; then
    echo "nginx is not answering on port 80 locally; skipping certbot." >&2
else
    # Let's Encrypt validates by fetching http://${SENTINEL_DOMAIN}/.well-known/...
    # from the outside, so this fails unless port 80 is open to 0.0.0.0/0 in
    # the security group. That is the usual cause when it does fail.
    if [[ -n "${ACME_EMAIL}" ]]; then
        certbot --nginx --non-interactive --agree-tos --redirect \
            -m "${ACME_EMAIL}" -d "${SENTINEL_DOMAIN}" \
            || echo "certbot failed — is port 80 open in the security group, and does ${SENTINEL_DOMAIN} resolve here?" >&2
    else
        certbot --nginx --non-interactive --agree-tos --redirect \
            --register-unsafely-without-email -d "${SENTINEL_DOMAIN}" \
            || echo "certbot failed — is port 80 open in the security group, and does ${SENTINEL_DOMAIN} resolve here?" >&2
    fi
fi

if [[ "${NEEDS_CREDENTIALS}" == "1" ]]; then
    cat <<EOF

────────────────────────────────────────────────────────────────────────────
Provisioned. The service is NOT running yet, on purpose.

  1. Fill in ${ENV_FILE}
       TG_HOST, TG_SECRET
       OPENAI_API_KEY, OPENAI_MODEL, OPENAI_EMBEDDING_MODEL
       CORS_ORIGINS  <- the Amplify branch URL, verbatim
  2. sudo systemctl start sentinel-api
  3. curl http://127.0.0.1:8000/api/health

The service refuses to start while any of the five required settings is
missing. That is deliberate: a process that starts and then fails every run
is worse than one that will not start.
────────────────────────────────────────────────────────────────────────────
EOF
else
    systemctl restart sentinel-api
    log "restarted"
fi
