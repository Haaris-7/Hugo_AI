#!/usr/bin/env bash
#
# Hugo — one-command setup.
#
#   git clone <repo> && cd <repo> && ./setup.sh
#
# That's it. This script handles everything:
#   1. Checks that Docker is installed
#   2. Creates .env from the template (if missing)
#   3. Builds and starts the full stack (postgres, api, worker, frontend)
#   4. Waits for the API and frontend to become healthy
#   5. Opens the setup wizard in your browser
#
# After you configure the live integrations in the wizard, click
# "Open the cockpit" to start using Hugo.
#
# Options:
#   --stop     Stop the running stack
#   --restart  Rebuild and restart (use after code changes)
#   --clean    Stop and remove all containers, volumes, and database
#
set -euo pipefail

GREEN='\033[1;32m'
CYAN='\033[1;36m'
YELLOW='\033[1;33m'
RED='\033[1;31m'
RESET='\033[0m'

log()  { printf "\n${CYAN}▶ %s${RESET}\n" "$*"; }
ok()   { printf "${GREEN}✓ %s${RESET}\n" "$*"; }
warn() { printf "${YELLOW}⚠ %s${RESET}\n" "$*"; }
fail() { printf "${RED}✗ %s${RESET}\n" "$*" >&2; exit 1; }

# ── Handle --stop / --restart ────────────────────────────────────────────────
for arg in "$@"; do
  case "$arg" in
    --stop)
      log "Stopping Hugo"
      docker compose down
      ok "Stopped"
      exit 0
      ;;
    --restart)
      log "Restarting Hugo (rebuild)"
      docker compose down
      docker compose up --build -d
      ok "Restarted — cockpit at http://localhost:3000"
      exit 0
      ;;
    --clean)
      log "Cleaning Hugo (removing containers, volumes, and database)"
      docker compose down -v
      ok "Cleaned — run ./setup.sh to start fresh"
      exit 0
      ;;
  esac
done

# ── 1. Prerequisites ────────────────────────────────────────────────────────
log "Checking prerequisites"

if ! command -v docker >/dev/null 2>&1; then
  fail "Docker is required. Install it from https://docs.docker.com/get-docker/"
fi
ok "Docker $(docker --version | awk '{print $3}' | tr -d ',')"

if ! docker info >/dev/null 2>&1; then
  fail "Docker daemon is not running. Start Docker Desktop first."
fi
ok "Docker daemon is running"

if ! docker compose version >/dev/null 2>&1; then
  fail "Docker Compose v2 is required. It ships with Docker Desktop."
fi
ok "Docker Compose $(docker compose version --short 2>/dev/null || echo 'v2')"

# ── 2. Environment file ─────────────────────────────────────────────────────
if [ ! -f .env ]; then
  log "Creating .env from template"
  cp .env.example .env
  ok "Created .env — the setup wizard will help you configure it"
else
  ok ".env already exists"
fi

# Generate local service credentials before Compose reads .env. The setup
# wizard never changes these, so the frontend proxy and backend stay in sync.
if grep -q '^HUGO_API_TOKEN=change-me$' .env; then
  token="$(openssl rand -hex 24)"
  sed -i "s/^HUGO_API_TOKEN=change-me$/HUGO_API_TOKEN=${token}/" .env
fi
if grep -q '^HUGO_AGENT_TOKEN=change-agent-token$' .env; then
  token="$(openssl rand -hex 24)"
  sed -i "s/^HUGO_AGENT_TOKEN=change-agent-token$/HUGO_AGENT_TOKEN=${token}/" .env
fi

# ── 3. Build and start ──────────────────────────────────────────────────────
log "Building and starting Hugo (this may take a minute on first run)"
docker compose up --build -d

# ── 4. Wait for services ────────────────────────────────────────────────────
log "Waiting for services to be ready"

wait_for() {
  local name="$1" url="$2" max_attempts=30 attempt=0
  while [ $attempt -lt $max_attempts ]; do
    if curl -sf -o /dev/null "$url" 2>/dev/null; then
      ok "$name is ready"
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 2
  done
  warn "$name did not respond after ${max_attempts} attempts"
  return 1
}

wait_for "API"      "http://localhost:8000/health"
wait_for "Cockpit"  "http://localhost:3000"

# ── 5. Open the setup wizard ────────────────────────────────────────────────
echo ""
printf "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"
printf "${GREEN}  Hugo is running!${RESET}\n"
echo ""
printf "  Setup wizard:  ${CYAN}http://localhost:3000/setup${RESET}\n"
printf "  Cockpit:       ${CYAN}http://localhost:3000${RESET}\n"
printf "  API docs:      ${CYAN}http://localhost:8000/docs${RESET}\n"
echo ""
printf "  Configure the live integrations in the setup wizard.\n"
printf "  To stop:  ${YELLOW}./setup.sh --stop${RESET}\n"
printf "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"

# Try to open the browser (best-effort, works on macOS/Linux/WSL)
if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "http://localhost:3000/setup" 2>/dev/null || true
elif command -v open >/dev/null 2>&1; then
  open "http://localhost:3000/setup" 2>/dev/null || true
elif command -v wslview >/dev/null 2>&1; then
  wslview "http://localhost:3000/setup" 2>/dev/null || true
fi
