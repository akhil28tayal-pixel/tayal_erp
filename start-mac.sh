#!/bin/bash
# Starts Tayal ERP (API + web) on a Mac without Docker.  Usage: ./start-mac.sh
# First run installs what's missing with Homebrew, creates the database, seeds it and asks for the admin login.
# Ctrl+C stops everything.
set -e
cd "$(dirname "$0")"
ROOT=$(pwd)
DB=tayal_erp
DB_URL="postgresql+psycopg://$(whoami)@localhost:5432/$DB"

say() { printf "\n\033[1m==> %s\033[0m\n" "$1"; }

# --- 1. Tools ---------------------------------------------------------------------------------
if ! command -v brew >/dev/null; then
  echo "Homebrew is required: https://brew.sh  (then run this script again)"; exit 1
fi
for pkg in postgresql@16 redis python@3.12 node@22; do
  brew list --versions "$pkg" >/dev/null 2>&1 || { say "Installing $pkg"; brew install "$pkg"; }
done
export PATH="$(brew --prefix postgresql@16)/bin:$(brew --prefix python@3.12)/bin:$(brew --prefix node@22)/bin:$PATH"

say "Starting PostgreSQL and Redis"
brew services start postgresql@16 >/dev/null
brew services start redis >/dev/null
for i in $(seq 1 20); do pg_isready -q -h localhost && break; sleep 1; done
psql -h localhost -d postgres -tAc "select 1 from pg_database where datname='$DB'" | grep -q 1 || createdb -h localhost "$DB"
psql -h localhost -d postgres -tAc "select 1 from pg_database where datname='${DB}_test'" | grep -q 1 || createdb -h localhost "${DB}_test"

# --- 2. Settings (.env is created once and kept) --------------------------------------------------
if [ ! -f .env ]; then
  say "Creating .env"
  echo "ERP_JWT_SECRET=$(python3.12 -c 'import secrets; print(secrets.token_urlsafe(48))')" > .env
fi
set -a; . ./.env; set +a
export ERP_DATABASE_URL="$DB_URL" ERP_REDIS_URL="redis://localhost:6379/0" ERP_ENV=local

# --- 3. Backend -----------------------------------------------------------------------------------
say "Preparing backend"
cd "$ROOT/backend"
[ -d .venv ] || python3.12 -m venv .venv
. .venv/bin/activate
pip install -q --upgrade pip
pip install -q -e ".[dev]"
alembic upgrade head
python -m app.cli seed-base
python -m app.cli seed-company
python -m app.cli seed-catalog

ADMINS=$(psql -h localhost -d "$DB" -tAc "select count(*) from user_roles ur join roles r on r.id=ur.role_id where r.code='SUPER_ADMIN'")
if [ "$ADMINS" = "0" ]; then
  say "Create the admin login (used to sign in to the web app)"
  read -rp "Your phone number: " PHONE
  read -rp "Your name: " NAME
  python -m app.cli create-admin --phone "$PHONE" --name "$NAME"   # asks for the password
fi

# --- 4. Web ---------------------------------------------------------------------------------------
say "Preparing web app"
cd "$ROOT/web"
[ -d node_modules ] || npm install --no-audit --no-fund

# --- 5. Run ---------------------------------------------------------------------------------------
mkdir -p "$ROOT/logs"
cd "$ROOT/backend"
# 0.0.0.0 so the Android app on phones in the same Wi-Fi (or Tailscale) can reach the API.
uvicorn app.main:app --host 0.0.0.0 --port 8000 > "$ROOT/logs/api.log" 2>&1 &
API_PID=$!
cd "$ROOT/web"
# The dashboard runs as an optimised build (pages open several times faster than with `next dev`).
# It is rebuilt only when the web code has changed since the last build. WEB_MODE=dev ./start-mac.sh for live editing.
export API_URL=http://localhost:8000
if [ "${WEB_MODE:-}" = "dev" ]; then
  npx next dev -p 3000 > "$ROOT/logs/web.log" 2>&1 &
else
  if [ ! -f .next/BUILD_ID ] || [ -n "$(find src next.config.mjs package.json tailwind.config.* -newer .next/BUILD_ID -print -quit 2>/dev/null)" ]; then
    say "Building the web app (only after changes; about a minute)"
    npx next build > "$ROOT/logs/web-build.log" 2>&1 || { echo "Web build failed, see logs/web-build.log"; exit 1; }
  fi
  npx next start -p 3000 > "$ROOT/logs/web.log" 2>&1 &
fi
WEB_PID=$!
trap 'echo; echo "Stopping…"; kill $API_PID $WEB_PID 2>/dev/null; exit 0' INT TERM

for i in $(seq 1 30); do curl -sf localhost:8000/health/live >/dev/null && break; sleep 1; done
say "Running"
echo "  Web app:   http://localhost:3000"
echo "  API docs:  http://localhost:8000/api/v1/docs"
LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)
[ -n "$LAN_IP" ] && echo "  Android app server address:  http://$LAN_IP:8000   (phone on the same Wi-Fi)"
echo "  Logs:      $ROOT/logs/api.log, $ROOT/logs/web.log"
echo "  Stop:      Ctrl+C"
echo
echo "  Bring in customers & godowns from Tally (in another Terminal tab):"
echo "    cd $ROOT/backend && . .venv/bin/activate && ERP_DATABASE_URL=$DB_URL \\"
echo "      python -m app.cli sync-tally-masters --tally-url http://<tally-pc-ip>:9000 --dry-run"
wait
