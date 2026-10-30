#!/usr/bin/env bash
# Sentinel — every command to run the application, in one place.
#
#   ./run.sh                  start everything: v2 service (8100), console API (8000), console (3000)
#   ./run.sh start            same as above
#   ./run.sh stop             stop the three services
#   ./run.sh status           which services are up
#   ./run.sh logs [name]      follow a service log (v2 | api | web; default: all)
#   ./run.sh warmup           wake the TigerGraph workspace and check it (run before a demo or a pack)
#   ./run.sh pack [FILE]      answer a case pack live on the graph (default: case_pack_final.csv) -> cases/
#   ./run.sh case ID [FILE]   answer one case (the drill command)
#   ./run.sh rehearse         run the frozen code on Round 1's 20 cases -> eval/out/rehearsal/
#   ./run.sh grade [DIR]      self-grade answer files (default: cases/)
#   ./run.sh test             backend tests that need no network, and the frontend typecheck
#   ./run.sh user NAME ROLE   add a sign-in (analyst | team_lead | fraud_manager | regulator); asks for the password
#   ./run.sh snapshot         export the read-only regulator snapshot -> regulator_snapshot.html
#   ./run.sh setup            install frontend packages (backend uses the Python already on this machine)
#
# Open http://localhost:3000 and sign in. Analysts, team leads and fraud managers land on the
# console; regulators on the regulator overview. Ask Sentinel is the logo button, bottom right.
#
# Needs: Python 3.12 with the backend packages, Node 20+, and .env at the repo root
# (TG_HOST, TG_SECRET or TG_KEY, TG_GRAPH, OPENAI_API_KEY, OPENAI_MODEL, SENTINEL_AUTH_SECRET).
# Ports can be moved with V2_PORT, API_PORT, WEB_PORT.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$ROOT/.run"
PY="${PYTHON:-python}"
V2_PORT="${V2_PORT:-8100}"
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-3000}"
mkdir -p "$RUN_DIR"

say()  { printf '\033[1;38;5;208m▸\033[0m %s\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; }

# ── preflight ────────────────────────────────────────────────────────────────

preflight() {
  say "Checking prerequisites"
  [[ -f "$ROOT/.env" ]] || { fail ".env is missing at the repo root (copy .env.example and fill it in)"; exit 1; }
  ok ".env present"
  command -v "$PY" >/dev/null || { fail "Python not found (set PYTHON=/path/to/python)"; exit 1; }
  "$PY" - <<'EOF' || { fail "backend packages are missing: pip install -e \"backend[dev]\" (or the three named above)"; exit 1; }
import importlib.util, sys
need = ["fastapi", "uvicorn", "pydantic_settings", "sqlalchemy", "aiosqlite", "dotenv", "openai", "numpy", "pyTigerGraph", "duckdb"]
missing = [m for m in need if importlib.util.find_spec(m) is None]
if missing:
    print("  missing:", ", ".join(missing)); sys.exit(1)
EOF
  ok "Python packages present ($("$PY" --version 2>&1))"
  command -v node >/dev/null || { fail "Node.js not found"; exit 1; }
  ok "Node $(node --version)"
  if [[ ! -d "$ROOT/frontend/node_modules" ]]; then
    say "Installing frontend packages (first run)"
    (cd "$ROOT/frontend" && npm ci --no-audit --no-fund)
  fi
  ok "frontend packages present"
}

# ── process helpers ──────────────────────────────────────────────────────────

port_pid() {  # the pid listening on a port, on Windows (Git Bash) or Unix
  local port="$1"
  if command -v netstat >/dev/null && [[ "$(uname -s)" =~ MINGW|MSYS|CYGWIN ]]; then
    netstat -ano 2>/dev/null | awk -v p=":$port" '$2 ~ p"$" && $4 == "LISTENING" {print $5; exit}'
  elif command -v lsof >/dev/null; then
    lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | head -1
  fi
}

kill_pid() {
  local pid="$1"
  [[ -z "$pid" ]] && return 0
  if [[ "$(uname -s)" =~ MINGW|MSYS|CYGWIN ]]; then
    taskkill //PID "$pid" //T //F >/dev/null 2>&1 || true
  else
    kill "$pid" 2>/dev/null || true
  fi
}

wait_for() {  # wait_for NAME URL [seconds]
  local name="$1" url="$2" secs="${3:-120}"
  for ((i = 0; i < secs; i++)); do
    if curl -s -o /dev/null -m 5 "$url"; then ok "$name is up  ($url)"; return 0; fi
    sleep 1
  done
  fail "$name did not answer within ${secs}s — see $RUN_DIR/$name.log"; return 1
}

start_one() {  # start_one NAME PORT DIR COMMAND...
  local name="$1" port="$2" dir="$3"; shift 3
  local existing; existing="$(port_pid "$port")"
  if [[ -n "$existing" ]]; then
    say "Port $port is in use (pid $existing); restarting $name with the current code"
    kill_pid "$existing"; sleep 1
  fi
  (cd "$dir" && nohup "$@" >"$RUN_DIR/$name.log" 2>&1 &)
}

# ── commands ─────────────────────────────────────────────────────────────────

cmd_start() {
  preflight
  say "Starting services"
  start_one v2  "$V2_PORT"  "$ROOT/backend" "$PY" -m uvicorn api_v2.app:app --host 127.0.0.1 --port "$V2_PORT"
  # The console re-runs cases and rewrites their answers. Give it a working copy, so the committed
  # Round 1 deliverables in cases/ are never touched by a click in the console.
  local console_cases="$ROOT/backend/var/console_cases"
  if [[ ! -d "$console_cases" ]]; then mkdir -p "$console_cases" && cp "$ROOT"/cases/*.json "$console_cases"/; fi
  start_one api "$API_PORT" "$ROOT"         env CASES_DIR="$console_cases" \
    "$PY" -m uvicorn api.main:app --app-dir backend --host 127.0.0.1 --port "$API_PORT"
  start_one web "$WEB_PORT" "$ROOT/frontend" env SENTINEL_API_BASE="http://127.0.0.1:$API_PORT" \
    SENTINEL_V2_BASE="http://127.0.0.1:$V2_PORT" npx next dev -p "$WEB_PORT"
  wait_for v2  "http://127.0.0.1:$V2_PORT/login"
  wait_for api "http://127.0.0.1:$API_PORT/api/health"
  wait_for web "http://127.0.0.1:$WEB_PORT/login" 240
  echo
  say "Sentinel is running"
  echo "    Open        http://localhost:$WEB_PORT   (sign in; your role decides where you land)"
  echo "    Logs        ./run.sh logs        Stop: ./run.sh stop"
  echo "    Before a demo or a pack: ./run.sh warmup"
}

cmd_stop() {
  say "Stopping services"
  for spec in "web:$WEB_PORT" "api:$API_PORT" "v2:$V2_PORT"; do
    local name="${spec%%:*}" port="${spec##*:}" pid
    pid="$(port_pid "$port")"
    if [[ -n "$pid" ]]; then kill_pid "$pid"; ok "$name stopped (port $port, pid $pid)"; else ok "$name was not running"; fi
  done
}

cmd_status() {
  for spec in "v2:$V2_PORT:/login" "api:$API_PORT:/api/health" "web:$WEB_PORT:/login"; do
    IFS=: read -r name port path <<<"$spec"
    if curl -s -o /dev/null -m 5 "http://127.0.0.1:$port$path"; then ok "$name up on $port"; else fail "$name down on $port"; fi
  done
}

cmd_logs() {
  local which="${1:-all}"
  if [[ "$which" == all ]]; then tail -n 40 -f "$RUN_DIR"/v2.log "$RUN_DIR"/api.log "$RUN_DIR"/web.log
  else tail -n 80 -f "$RUN_DIR/$which.log"; fi
}

cmd_warmup()   { "$PY" "$ROOT/scripts/warmup.py"; }

cmd_pack() {
  local pack="${1:-case_pack_final.csv}"
  [[ -f "$ROOT/$pack" ]] || { fail "$pack not found at the repo root (the organisers' pack goes there)"; exit 1; }
  cmd_warmup
  say "Answering $pack from commit $(git -C "$ROOT" rev-parse --short HEAD) -> cases/"
  (cd "$ROOT/backend" && "$PY" -m sentinel.typologies.run --pack "../$pack" --out ../cases)
  cmd_grade cases
}

cmd_case() {
  local id="${1:?usage: ./run.sh case HHF-007 [pack]}" pack="${2:-case_pack_final.csv}"
  (cd "$ROOT/backend" && "$PY" -m sentinel.typologies.run --pack "../$pack" --case "$id" --out ../cases)
}

cmd_rehearse() {
  cmd_warmup
  say "Rehearsal: Round 1's 20 cases -> eval/out/rehearsal/"
  (cd "$ROOT/backend" && "$PY" -m sentinel.typologies.run --pack ../case_pack.csv --out ../eval/out/rehearsal/cases)
  cmd_grade eval/out/rehearsal/cases
}

cmd_grade()    { (cd "$ROOT" && "$PY" -m eval.self_grade --cases "${1:-cases}"); }

cmd_test() {
  say "Backend tests (no network)"
  (cd "$ROOT/backend" && "$PY" -m pytest tests/unit -q)
  say "Frontend typecheck"
  (cd "$ROOT/frontend" && npx tsc --noEmit && echo "  typecheck ok")
}

cmd_user() {
  local name="${1:?usage: ./run.sh user NAME ROLE}" role="${2:?roles: analyst | team_lead | fraud_manager | regulator}"
  (cd "$ROOT/backend" && "$PY" -m api_v2.users add --user "$name" --role "$role")
}

cmd_snapshot() { (cd "$ROOT" && "$PY" scripts/export_regulator_snapshot.py --run eval/out/regulator_demo --out regulator_snapshot.html); }

cmd_setup() {
  (cd "$ROOT/frontend" && npm ci --no-audit --no-fund)
  preflight
}

case "${1:-start}" in
  start)    cmd_start ;;
  stop)     cmd_stop ;;
  restart)  cmd_stop; cmd_start ;;
  status)   cmd_status ;;
  logs)     shift; cmd_logs "$@" ;;
  warmup)   cmd_warmup ;;
  pack)     shift; cmd_pack "$@" ;;
  case)     shift; cmd_case "$@" ;;
  rehearse) cmd_rehearse ;;
  grade)    shift; cmd_grade "$@" ;;
  test)     cmd_test ;;
  user)     shift; cmd_user "$@" ;;
  snapshot) cmd_snapshot ;;
  setup)    cmd_setup ;;
  -h|--help|help) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) fail "unknown command: $1 (try ./run.sh help)"; exit 1 ;;
esac
