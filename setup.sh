#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# MR Personal Assistant — one-command setup AND start.
#
#   ./setup.sh            # auto: uses Docker if it is installed and running,
#                         # otherwise sets up and STARTS everything locally
#   ./setup.sh --local    # force local mode (Python venv + npm + local Postgres)
#   ./setup.sh --docker   # force Docker mode
#   ./setup.sh --stop     # stop whatever is running (local servers or Docker)
#
# Both modes end with the app RUNNING — nothing left for you to launch.
# Every step announces itself, so you can see exactly what is happening.
#
# Idempotent: safe to re-run. An existing backend/.env is never overwritten,
# finished steps are skipped, and re-running simply restarts the servers.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

cd "$(dirname "$0")"

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
ok()    { printf '  \033[32m✔\033[0m %s\n' "$*"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$*"; }
fail()  { printf '  \033[31m✘\033[0m %s\n' "$*" >&2; exit 1; }

OS="$(uname -s 2>/dev/null || echo unknown)"
case "$OS" in
  Linux*)  OS_NAME="Linux" ;;
  Darwin*) OS_NAME="macOS" ;;
  MINGW*|MSYS*|CYGWIN*) OS_NAME="Windows (Git Bash)" ;;
  *)       OS_NAME="$OS" ;;
esac
bold "MR Personal Assistant setup — detected: $OS_NAME"

# ── secrets ──────────────────────────────────────────────────────────────────
gen_secret() {
  # Prefer python3 (always present where the backend runs); fall back to openssl.
  if command -v python3 >/dev/null 2>&1; then
    python3 -c "import secrets;print(secrets.token_urlsafe(48))"
  else
    openssl rand -base64 48 | tr '+/' '-_' | tr -d '='
  fi
}

ensure_env() {
  if [ -f backend/.env ]; then
    ok "backend/.env already exists — keeping it"
  else
    cp backend/.env.example backend/.env
    SECRET="$(gen_secret)"
    # Replace the placeholder JWT secret line whatever its current value is.
    if grep -q '^JWT_SECRET=' backend/.env; then
      sed -i.bak "s|^JWT_SECRET=.*|JWT_SECRET=$SECRET|" backend/.env && rm -f backend/.env.bak
    else
      printf '\nJWT_SECRET=%s\n' "$SECRET" >> backend/.env
    fi
    ok "backend/.env created with a fresh JWT_SECRET"
  fi

  # The chat needs an OpenAI key; everything else (data, retrieval eval, tests)
  # works without one. Take it from the shell if it is set.
  if grep -Eq '^OPENAI_API_KEY=.+[^"]' backend/.env && ! grep -q '^OPENAI_API_KEY=$' backend/.env; then
    ok "OPENAI_API_KEY present in backend/.env"
  elif [ -n "${OPENAI_API_KEY:-}" ]; then
    if grep -q '^OPENAI_API_KEY=' backend/.env; then
      sed -i.bak "s|^OPENAI_API_KEY=.*|OPENAI_API_KEY=$OPENAI_API_KEY|" backend/.env && rm -f backend/.env.bak
    else
      printf 'OPENAI_API_KEY=%s\n' "$OPENAI_API_KEY" >> backend/.env
    fi
    ok "OPENAI_API_KEY taken from your shell environment"
  else
    warn "No OPENAI_API_KEY set — the app will run, but chat answers need one."
    warn "Add it to backend/.env later:  OPENAI_API_KEY=sk-..."
  fi
}

# ── docker mode ──────────────────────────────────────────────────────────────
docker_mode() {
  command -v docker >/dev/null 2>&1 || fail "Docker not found. Install Docker Desktop (macOS/Windows) or docker + compose (Linux), or run: ./setup.sh --local"
  docker compose version >/dev/null 2>&1 || fail "'docker compose' v2 not available — update Docker."

  ensure_env

  # Compose reads JWT_SECRET/OPENAI_API_KEY from the calling environment; feed
  # it the values we just ensured in backend/.env.
  set -a
  # shellcheck disable=SC1091
  . backend/.env
  set +a

  bold "1/4 Starting Postgres (schemas auto-apply on first init)…"
  docker compose up -d --build db
  # Wait for health rather than sleeping blind.
  for _ in $(seq 1 30); do
    if docker compose exec -T db pg_isready -U qorvexa -d qorvexa >/dev/null 2>&1; then break; fi
    sleep 2
  done
  ok "Postgres is up"

  # The dataset is applied by Postgres itself, from
  # backend/etl/seed_app.sql mounted into docker-entrypoint-initdb.d — so there
  # is no load step here any more. It runs on FIRST init of the pgdata volume
  # only; `docker compose down -v` is what forces it again.
  bold "2/4 Confirming the dataset…"
  reps=$(docker compose exec -T db psql -U qorvexa -d qorvexa -tAc \
      'select count(*) from app.reps' 2>/dev/null | tr -d '[:space:]')
  if [ "${reps:-0}" -lt 1 ]; then
    echo "  The app schema is empty. If this volume predates the seed file, reset it:"
    echo "    docker compose down -v && ./setup.sh"
    exit 1
  fi
  ok "Dataset present — $reps reps"
  docker compose exec -T db psql -U qorvexa -d qorvexa -tAc \
      'select rep_code from app.reps order by rep_code limit 5' 2>/dev/null \
      | sed 's/^/    sign in as /'
  echo "    (password: qorvexa)"

  bold "3/4 Ingesting the literature corpus (offline, no API key needed)…"
  # MUST run while the backend service is stopped: local-mode Qdrant takes an
  # exclusive folder lock, and both share the qdrantdata volume.
  docker compose stop backend >/dev/null 2>&1 || true
  docker compose run --rm backend \
    python -m etl.ingest_docs data/literature --scope global \
    --embeddings-from evals/rag_corpus_vectors.npz
  ok "Corpus ingested into the shared Qdrant volume"

  bold "4/4 Starting the API and the web app…"
  docker compose up -d --build backend web
  ok "Running"

  echo
  bold "Done — open http://localhost:8080"
  echo "  Sign in with any rep code printed by the loader (password: qorvexa)."
  echo "  API:     http://localhost:8000/api/health"
  echo "  Stop:    docker compose down        (data survives)"
  echo "  Reset:   docker compose down -v     (wipes DB + corpus)"
}

# ── local dev servers: start/stop ───────────────────────────────────────────
#
# PID files under .dev/ record exactly which processes THIS script started, so
# --stop never touches anything else. Logs land beside them, and a failed start
# prints the tail of the relevant log instead of a bare error.
DEV_DIR=".dev"

stop_servers() {
  stopped=0
  for name in backend frontend; do
    pidfile="$DEV_DIR/$name.pid"
    [ -f "$pidfile" ] || continue
    pid=$(cat "$pidfile")

    if kill -0 "$pid" 2>/dev/null; then
      # A pidfile outlives a reboot, and by then that number can belong to
      # something else entirely — killing its process GROUP would be a real
      # accident. Confirm the process still looks like ours before signalling.
      args=$(ps -p "$pid" -o args= 2>/dev/null || true)
      case "$args" in
        *uvicorn*|*vite*|*"npm run dev"*|*node*)
          # Kill the whole group: npm spawns vite as a child, and killing only
          # the parent would leave the port occupied. Fall back to the bare pid
          # where there is no separate group (no setsid, e.g. macOS).
          kill -- -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
          ok "stopped $name (pid $pid)"
          stopped=1
          ;;
        *)
          warn "$DEV_DIR/$name.pid is stale (pid $pid is something else) — ignoring it"
          ;;
      esac
    fi
    rm -f "$pidfile"
  done

  [ "$stopped" = 1 ] && sleep 1 || true
}

# REPORT, never kill, servers we have no pidfile for.
#
# A pidfile can be lost while the server it tracked keeps running, and that
# server still holds the Qdrant folder lock — which is what makes the ingest
# step fail. The obvious "fix" is to pgrep for uvicorn/vite and kill the match's
# process GROUP; do not. That pattern also matches the shell that launched this
# script, and killing that group kills the setup run itself (observed). Naming
# the PIDs is nearly as useful and cannot misfire.
report_untracked() {
  command -v pgrep >/dev/null 2>&1 || return 0
  here=$(pwd -P)
  found=""
  for pid in $(pgrep -f "uvicorn app.main|node.*vite" 2>/dev/null || true); do
    [ "$pid" = "$$" ] && continue
    cwd=$(readlink "/proc/$pid/cwd" 2>/dev/null || true)   # Linux; empty elsewhere
    case "$cwd" in
      "$here"|"$here"/*) found="$found $pid" ;;
    esac
  done
  [ -n "$found" ] || return 0
  warn "These server processes from this repo have no pidfile, so --stop cannot"
  warn "reach them:$found"
  warn "Stop them by hand if a later step complains:  kill$found"
}

port_free_or_fail() {  # $1 = port
  if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:$1/" 2>/dev/null; then
    warn "Something this script did not start is already listening on port $1."
    fail "Stop it first (e.g. pkill -f uvicorn / pkill -f vite), then re-run."
  fi
}

wait_for() {  # $1 = url, $2 = name, $3 = log file
  printf '  waiting for %s ' "$2"
  for _ in $(seq 1 60); do
    if curl -s -o /dev/null --max-time 2 "$1" 2>/dev/null; then
      printf '\n'; ok "$2 is up"; return 0
    fi
    printf '.'; sleep 1
  done
  printf '\n'
  warn "$2 did not come up — last lines of its log:"
  tail -15 "$3" | sed 's/^/    /'
  fail "full log: $3"
}

# setsid puts the server in its own session so stop_servers can kill the whole
# group (npm spawns vite as a child). It is util-linux — macOS does not ship it,
# and this script claims macOS support, so fall back to a plain background job
# there; stop_servers handles both shapes.
SETSID=""
command -v setsid >/dev/null 2>&1 && SETSID="setsid"

start_one() {  # $1 = name, $2 = directory, rest = command
  name="$1"; dir="$2"; shift 2
  ( cd "$dir" && exec $SETSID "$@" ) > "$DEV_DIR/$name.log" 2>&1 &
  echo $! > "$DEV_DIR/$name.pid"
}

start_servers() {
  mkdir -p "$DEV_DIR"
  stop_servers                       # a re-run restarts cleanly
  port_free_or_fail 8000
  port_free_or_fail 5173

  bold "6/7 Starting the backend (uvicorn, http://localhost:8000)…"
  start_one backend backend ./.venv/bin/uvicorn app.main:app --reload --port 8000
  wait_for "http://127.0.0.1:8000/api/health" "the API" "$DEV_DIR/backend.log"

  bold "7/7 Starting the frontend (vite, http://localhost:5173)…"
  start_one frontend frontend npm run dev
  wait_for "http://127.0.0.1:5173/" "the web app" "$DEV_DIR/frontend.log"

  echo
  bold "Done — everything is running."
  echo "  Open:     http://localhost:5173"
  echo "  Sign in:  any rep code above (e.g. 7800001) — password: qorvexa"
  echo "  API:      http://localhost:8000/api/health"
  echo "  Logs:     $DEV_DIR/backend.log · $DEV_DIR/frontend.log"
  echo "  Stop:     ./setup.sh --stop      Restart: ./setup.sh --local"
}

# ── local dev mode ───────────────────────────────────────────────────────────
local_mode() {
  bold "Local mode — needs Python 3.12+, Node 20+, and PostgreSQL 16+ running locally."
  command -v python3 >/dev/null 2>&1 || fail "python3 not found"
  command -v npm     >/dev/null 2>&1 || fail "npm not found (install Node 20+)"
  command -v psql    >/dev/null 2>&1 || fail "psql not found (install PostgreSQL 16+)"

  ensure_env
  mkdir -p "$DEV_DIR"

  bold "1/7 Python environment (backend/.venv)…"
  if [ -d backend/.venv ]; then
    ok "backend/.venv already exists — reusing it"
  else
    echo "  creating a virtualenv and installing dependencies (1–2 minutes)…"
    ( cd backend && python3 -m venv .venv )
  fi
  # NOT piped into grep: with `set -o pipefail` a grep that matches nothing
  # fails the pipeline, so the `|| true` needed to tolerate that would ALSO
  # swallow a genuine pip failure — and the confusing symptom lands three steps
  # later as "uvicorn: not found". Log the noise, show it only on failure.
  if ! ( cd backend && ./.venv/bin/pip install -r requirements.txt ) > "$DEV_DIR/pip.log" 2>&1; then
    warn "pip install failed:"
    # ERROR lines first when pip produced any: the tail of a pip log is often
    # "Requirement already satisfied" noise, and the one line that matters can
    # sit above it.
    if grep -q "^ERROR" "$DEV_DIR/pip.log"; then
      grep "^ERROR" "$DEV_DIR/pip.log" | head -5 | sed 's/^/    /'
    else
      tail -20 "$DEV_DIR/pip.log" | sed 's/^/    /'
    fi
    fail "full log: $DEV_DIR/pip.log"
  fi
  ok "Python dependencies ready"

  bold "2/7 Checking Postgres…"
  # shellcheck disable=SC1091
  set -a; . backend/.env; set +a
  DB_URL="${DATABASE_URL:?DATABASE_URL missing from backend/.env}"
  if ! psql "$DB_URL" -c 'SELECT 1' >/dev/null 2>&1; then
    # Parse the role, password and database out of the DSN the user actually has,
    # rather than assuming $USER. The printed command has to create exactly what
    # DATABASE_URL asks for, or following it leaves them no better off.
    DB_ROLE=$(printf '%s' "$DB_URL" | sed -E 's|^[a-zA-Z+]+://([^:/@]+).*|\1|')
    DB_PASS=$(printf '%s' "$DB_URL" | sed -E 's|^[a-zA-Z+]+://[^:/@]+:([^@]*)@.*|\1|')
    DB_NAME=$(printf '%s' "$DB_URL" | sed -E 's|^[a-zA-Z+]+://[^/]+/([^?]+).*|\1|')
    warn "Cannot connect with DATABASE_URL from backend/.env."
    warn "Create the role and database first (one-time, as the postgres superuser):"
    warn "  sudo -u postgres psql -c \"CREATE ROLE $DB_ROLE LOGIN PASSWORD '$DB_PASS' CREATEDB;\""
    warn "  sudo -u postgres createdb -O $DB_ROLE $DB_NAME"
    fail "…then re-run ./setup.sh --local"
  fi
  ok "Postgres reachable"

  bold "3/7 Loading the dataset + schemas…"
  # seed_app.sql carries the data AND the qorvexa_ro role plus the column-level
  # grant that withholds app.reps.password_hash. ON_ERROR_STOP so a half-applied
  # schema fails loudly. Output is suppressed because a data load is thousands
  # of COPY lines; the confirmation below proves it landed.
  # The app schema is pure synthetic data (chat history lives in `public`), so
  # a re-run drops and reloads it — same behaviour the old loader had. Without
  # the drop, the seed's CREATE SCHEMA fails on the second run.
  # The leading -c silences the expected NOTICE flood on a re-run ("already
  # exists, skipping" for every idempotent object) without hiding real errors,
  # which arrive at ERROR level and stop the script. It has to be a -c, not
  # PGOPTIONS: the DSN carries its own options= parameter, and libpq ignores
  # the environment variable whenever the connection string sets options.
  QUIET='SET client_min_messages = warning'
  psql -q "$DB_URL" -c "$QUIET" -c 'DROP SCHEMA IF EXISTS app CASCADE' >/dev/null
  ( cd backend \
    && psql -q -v ON_ERROR_STOP=1 "$DB_URL" -c "$QUIET" -f etl/seed_app.sql   >/dev/null \
    && psql -q -v ON_ERROR_STOP=1 "$DB_URL" -c "$QUIET" -f etl/chat_history.sql >/dev/null \
    && psql -q -v ON_ERROR_STOP=1 "$DB_URL" -c "$QUIET" -f etl/agent_schema.sql >/dev/null \
    && psql -q -v ON_ERROR_STOP=1 "$DB_URL" -c "$QUIET" -f etl/agenda_schema.sql >/dev/null )
  reps=$(psql "$DB_URL" -tAc 'select count(*) from app.reps' | tr -d '[:space:]')
  ok "Dataset loaded — $reps reps, 938 doctors, plus chat/agent/agenda schemas"
  psql "$DB_URL" -tAc 'select rep_code from app.reps order by rep_code limit 5' \
      | sed 's/^/    sign in as /'
  echo "    (password: qorvexa)"

  bold "4/7 Ingesting the literature corpus (offline, no API key needed)…"
  # Stop any servers we previously started FIRST: the local-mode Qdrant store
  # takes an exclusive folder lock, and a running backend holds it.
  stop_servers
  report_untracked
  if ! ( cd backend && ./.venv/bin/python -m etl.ingest_docs data/literature \
        --scope global --embeddings-from evals/rag_corpus_vectors.npz ) \
        > "$DEV_DIR/ingest.log" 2>&1; then
    if grep -q "already accessed by another instance" "$DEV_DIR/ingest.log"; then
      warn "Another process is holding the Qdrant store (backend/qdrant_data)."
      warn "Stop every backend/uvicorn using this repo, then re-run:"
      warn "  ./setup.sh --stop   # or: pkill -f 'uvicorn app.main'"
      fail "the local Qdrant store allows one writer at a time"
    fi
    warn "corpus ingest failed — last lines:"
    tail -20 "$DEV_DIR/ingest.log" | sed 's/^/    /'
    fail "full log: $DEV_DIR/ingest.log"
  fi
  sed 's/^/    /' "$DEV_DIR/ingest.log"
  ok "Corpus ingested"

  bold "5/7 Frontend dependencies…"
  if [ -d frontend/node_modules ]; then
    ok "frontend/node_modules already exists — reusing it"
  else
    echo "  npm ci (1–2 minutes)…"
    ( cd frontend && npm ci ) 2>&1 | tail -3 | sed 's/^/    /'
    ok "frontend dependencies ready"
  fi

  start_servers
}

case "${1:-}" in
  --local)  local_mode ;;
  --docker) docker_mode ;;
  --stop)
    # Stop BOTH shapes: --stop should mean "stop the app", and the user should
    # not have to remember which mode they started it in.
    bold "Stopping…"
    stop_servers
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
      if [ -n "$(docker compose ps -q 2>/dev/null)" ]; then
        docker compose down
        ok "docker compose stopped (data survives; add -v to wipe it)"
      fi
    fi
    ok "done"
    ;;
  "")
    # Auto-pick: Docker when it is installed AND its daemon answers; local
    # otherwise. --local / --docker force either path explicitly.
    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
      bold "Docker detected and running — using Docker mode (force local with --local)."
      docker_mode
    else
      bold "Docker not available — using local mode (force Docker with --docker)."
      local_mode
    fi
    ;;
  *) fail "Unknown option: $1  (use --local, --docker, --stop, or no argument)" ;;
esac
