# Deploying Sentinel

Backend on one EC2 instance behind nginx. Console on AWS Amplify Hosting.

```
  browser ──https──▶ Amplify (Next.js SSR)
     │                  the console; NEXT_PUBLIC_API_BASE is baked in at build
     │
     └────https──▶ EC2 :443 ── nginx ── :8000 uvicorn (1 worker)
                                            │
                                            ├──https──▶ TigerGraph Savanna
                                            └──https──▶ OpenAI
```

## The one thing that will bite you

Amplify serves the console over HTTPS. **A page served over HTTPS may not call
`http://<ec2-ip>:8000`.** The browser blocks it as mixed content before the
request leaves the tab, so there is nothing in the network panel to explain it
— the console simply shows a disconnected badge forever.

So the backend needs a hostname and a certificate. Two ways:

- **A domain you control.** Point an A record at the instance's Elastic IP and
  let certbot issue for it. This is what `bootstrap-ec2.sh` does.
- **No domain.** `<public-ip>.sslip.io` resolves to that IP from anywhere, and
  Let's Encrypt will issue for it. `SENTINEL_DOMAIN=12.34.56.78.sslip.io`
  works end to end with no DNS to set up. Use an Elastic IP, or the hostname
  changes every time the instance stops.

The second half of the same problem is CORS: `CORS_ORIGINS` on the server must
contain the Amplify branch URL **verbatim** — scheme included, no trailing
slash. A mismatch there fails every request with a message the browser only
shows in the console, which looks exactly like the API being down.

---

## Backend on EC2

### Instance

| | |
|---|---|
| AMI | Ubuntu 24.04 LTS — it ships Python 3.12, which the pins are built against |
| Type | `t3.small` is enough. The vector index is ~5,600 × 256 floats in memory; the rest is a SQLite file. `t3.micro`'s 1 GiB is tight once numpy is resident |
| Disk | 20 GiB gp3 |
| Security group | inbound 443 and 80 from anywhere (80 is how Let's Encrypt validates), 22 from your IP only. Nothing else — uvicorn binds loopback and is not reachable from outside the box |
| IP | allocate an Elastic IP and associate it, before issuing the certificate |

Outbound needs to reach TigerGraph Savanna and OpenAI on 443, which the
default egress rule already allows.

### Provision

```bash
ssh ubuntu@<elastic-ip>
git clone https://github.com/r-rishit27/hacker_house.git /tmp/sentinel && cd /tmp/sentinel

sudo SENTINEL_REPO=https://github.com/r-rishit27/hacker_house.git \
     SENTINEL_BRANCH=main \
     SENTINEL_DOMAIN=api.example.com \
     ACME_EMAIL=you@example.com \
     bash deploy/bootstrap-ec2.sh
```

That installs the packages, creates the `sentinel` service account, clones to
`/opt/sentinel/app`, builds the venv at `/opt/sentinel/venv` from
`backend/requirements.txt`, installs the unit and the nginx site, and runs
certbot. It deliberately **stops short of starting the service**: credentials
come next, and a secret passed through EC2 user-data is a secret sitting in
the instance metadata, readable by anything that can reach `169.254.169.254`.

### Credentials

```bash
sudo cp /opt/sentinel/app/deploy/sentinel.env.example /etc/sentinel/sentinel.env  # already done by bootstrap
sudo -e /etc/sentinel/sentinel.env
sudo systemctl start sentinel-api
```

Five settings are required and the process refuses to start without them:
`TG_HOST`, `TG_SECRET`, `OPENAI_API_KEY`, `OPENAI_MODEL`,
`OPENAI_EMBEDDING_MODEL`. The two model ids have no defaults on purpose — a
hard-coded model id is how an hour goes to a 404.

The file is `root:sentinel` `0640` and systemd reads it directly, so it is
`KEY=value` and nothing else: no `export`, no `$VAR` expansion, no trailing
comments. Note that the checkout on the server has **no** `.env` —
pydantic-settings would read one from the repository root if it were there,
and two sources of truth for a credential is how a rotated key keeps working
on one path and not the other.

### Check it

```bash
curl https://api.example.com/api/health   # liveness; touches nothing
curl https://api.example.com/api/ready    # probes the graph, the model, the index
curl https://api.example.com/api/meta     # enums, routing, thresholds
```

`/ready` is legitimately 503 for the first minute or so: the TigerGraph
workspace auto-stops when idle and takes about 45 seconds to wake, and the
retriever reads 5,611 vectors out of the graph in the background on first
boot. `/health` answers throughout. Use `/health` for any load-balancer or
uptime check and `/ready` for a human asking whether it works.

```bash
journalctl -u sentinel-api -f       # one JSON line per request, plus the app log
systemctl status sentinel-api
```

### Redeploy

```bash
sudo bash /opt/sentinel/app/deploy/update.sh
```

Fetch, reinstall if the pins moved, import-check the new revision with the old
process still serving, restart, poll `/health`. The SQLite database in
`backend/var` — approvals, executions, the audit log, the event journal —
survives; nothing in the update path touches it.

### What lives where on the box

| Path | What |
|---|---|
| `/opt/sentinel/app` | the checkout, owned by `sentinel`, read-only to the service except for the two directories below |
| `/opt/sentinel/app/backend/var` | SQLite database and the vector-index cache. **This is the state worth backing up** |
| `/opt/sentinel/app/runs` | recorded run traces, if a batch is driven on the box |
| `/opt/sentinel/venv` | the Python environment |
| `/etc/sentinel/sentinel.env` | credentials and settings |
| `/etc/systemd/system/sentinel-api.service` | the unit |
| `/etc/nginx/sites-available/sentinel` | the site, rewritten in place by certbot |

### One worker, and why it stays that way

`--workers 1` in the unit is load-bearing. The run registry that enforces
`max_concurrent_investigations`, the SSE broker every subscriber attaches to,
and the asyncio tasks the investigations run in are all process-local. A
second worker would admit a run the first worker's ceiling had already
refused, and an `EventSource` would attach to a broker that is not the one
emitting its events — a stream that connects and then delivers nothing.
Concurrency inside the one process is what `MAX_CONCURRENT_INVESTIGATIONS` is
for.

### Amazon Linux 2023

Use [`bootstrap-al2023.sh`](bootstrap-al2023.sh) instead. Measured on
`Amazon Linux 2023.12`, the differences from Ubuntu turned out to be smaller
than expected:

- `python3.12` **is** in the `amazonlinux` repo (3.12.14), so the pins resolve
  to exactly what they were tested against
- `certbot` and `python3-certbot-nginx` are packaged too — no EPEL, no
  pip-installed certbot to maintain on the side
- nginx is configured from `/etc/nginx/conf.d/*.conf`; there is no
  `sites-available`/`sites-enabled` pair
- SELinux ships **Permissive**, so nginx proxying to loopback needs no
  `setsebool`
- do **not** `dnf install curl`: AL2023 ships `curl-minimal`, which already
  provides `/usr/bin/curl`, and asking for the full package aborts the
  transaction with a wall of conflicts
- the small instance types have no swap and under a gigabyte of RAM. The
  script adds 2 GiB of swap and, below 2 GiB of RAM, writes a drop-in
  narrowing the unit's `MemoryMax` to something the machine can actually
  reach before the kernel's OOM killer does

---

## Console on Amplify

The build spec is [`amplify.yml`](../amplify.yml) at the repository root. It
is the monorepo form — `appRoot: frontend` — so Amplify runs the build from
there.

1. **Amplify console ▸ Create new app ▸ deploy from Git**, pick the repository
   and the branch.
2. Amplify detects Next.js. Leave the platform as **Next.js SSR**
   (`WEB_COMPUTE`) — the console is client-rendered against the API, but
   `/cases/[caseId]` is a deep link an analyst pastes into a chat, and a
   static export would have to enumerate every case id at build time.
3. Build settings: **use the `amplify.yml` from the repository**.
4. **App settings ▸ Environment variables**, add:

   ```
   NEXT_PUBLIC_API_BASE = https://api.example.com
   ```

   https, no trailing slash, and the same hostname the certificate was issued
   for. `NEXT_PUBLIC_` means Next inlines it into the client bundle at build
   time, so **changing it needs a redeploy, not a restart**. The build fails
   fast if it is missing or not https, rather than shipping a bundle that
   points at localhost.
5. Deploy. Copy the branch URL — `https://main.dxxxxxxxxxxxxx.amplifyapp.com`.
6. Put that URL into `CORS_ORIGINS` in `/etc/sentinel/sentinel.env` on EC2 and
   `sudo systemctl restart sentinel-api`.

Step 6 is the one people skip. Until it is done the console loads, looks
right, and cannot fetch a single thing.

### A custom domain on the console

Amplify ▸ Domain management. Add the new origin to `CORS_ORIGINS` as well —
comma-separated, both entries, because the branch URL keeps working and
somebody will have it bookmarked.

---

## Deployment checklist

- [ ] Elastic IP allocated and associated
- [ ] Security group: 443 and 80 open, 22 from your IP only
- [ ] `SENTINEL_DOMAIN` resolves to the instance (A record, or `<ip>.sslip.io`)
- [ ] `bootstrap-ec2.sh` completed and certbot issued a certificate
- [ ] `/etc/sentinel/sentinel.env` filled in, `0640`, `root:sentinel`
- [ ] `systemctl is-enabled sentinel-api` says `enabled`
- [ ] `curl https://<domain>/api/health` returns `{"status":"ok",...}`
- [ ] `curl https://<domain>/api/ready` returns 200 once the workspace is awake
      (503 with `graph` and `retrieval` false is the expected first minute)
- [ ] Amplify app created, `amplify.yml` in use, `NEXT_PUBLIC_API_BASE` set
- [ ] Amplify branch URL added to `CORS_ORIGINS`, service restarted
- [ ] Console loads, the connection badge is green, a case opens
- [ ] A run streams — the timeline fills step by step, not all at once at the
      end. All at once means something between nginx and the browser is
      buffering the SSE response

---

## Troubleshooting

**The console shows disconnected and the browser console says "Mixed
Content".** `NEXT_PUBLIC_API_BASE` is http. It has to be https, and it has to
be redeployed on Amplify — the value is baked into the bundle.

**Every request fails CORS.** `CORS_ORIGINS` does not match the Amplify origin
exactly. Compare them character for character: scheme, no trailing slash, no
space after a comma. Restart the service after changing it.

**A run connects but the timeline stays empty, then fills all at once when it
finishes.** Something is buffering the SSE response. The app sends
`X-Accel-Buffering: no` and the nginx site turns off `proxy_buffering`, `gzip`
and `chunked_transfer_encoding` for the events route — check that a later
`gzip on` in `nginx.conf` has not reached it, and that nothing else (a CDN, a
corporate proxy) sits in front.

**`/ready` is 503 with the graph unreachable.** Usually the Savanna workspace
is asleep; the first call wakes it and takes about 45 seconds, and the
repository retries the HTML "Starting workspace" page rather than reporting it
as an error. If it persists, check `TG_HOST` and `TG_SECRET` and run the
doctor on the box:

```bash
sudo systemd-run --pty --quiet --collect \
  --uid=sentinel --gid=sentinel \
  --working-directory=/opt/sentinel/app \
  --property=EnvironmentFile=/etc/sentinel/sentinel.env \
  /opt/sentinel/venv/bin/python -m sentinel doctor
```

`systemd-run` rather than `sudo -u sentinel` with the environment splatted on
the command line: it reads the same `EnvironmentFile` the service does, so the
doctor sees exactly what the service sees, and the secret never appears in
your shell history or in `ps`.

**The service will not start and the log says five fields are missing.** That
is the design. Fill in `/etc/sentinel/sentinel.env`.

**A setting fails to parse and the value in the error has a comment glued to
the end of it** — `max_concurrent_investigations ... input_value='4# TigerGraph
Savanna...'`. The env file was appended to without a trailing newline, so the
first pasted line joined the last existing one. Fix that single line; the rest
of the file is fine, and duplicate keys are harmless because systemd lets the
later definition win — which is what makes "paste your real values at the
bottom" a working way to fill the template in.

**`sqlite3.OperationalError: unable to open database file`.** A permissions
error wearing a path error's clothes. Check the owner of
`/opt/sentinel/app/backend/var` — it must be `sentinel`, and the fix is
`sudo chown -R sentinel:sentinel /opt/sentinel/app/backend/var`. The systemd
sandbox is usually blamed first and is usually innocent; to rule it in or out,
run the same `touch` under `systemd-run` with the unit's `ProtectSystem` and
`ReadWritePaths` and see whether it also fails.

**Paths point at nothing — no cases, an empty ring view, a database in a
strange place.** The package was installed without `-e`. `Settings.ROOT` is
the settings module's own path up three directories, and a regular install
moves that module into site-packages. Reinstall editable:
`/opt/sentinel/venv/bin/pip install --no-deps -e /opt/sentinel/app/backend`.
