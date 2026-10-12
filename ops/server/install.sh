#!/bin/bash
# One-time installation of the userland pilot environment on the shared Infomaniak host (docs/BETRIEB_SERVER.md). Idempotent.
# Creates ONLY files below $HOME: ~/campus (repo), ~/campus-data (state, secrets, backups), ~/.campus-tools (uv, python, temporal).
# Touches nothing of the other applications. Does not write the crontab and does not touch the web root (separate, confirmed steps).
set -euo pipefail
CAMPUS_HOME="${CAMPUS_HOME:-$HOME/campus}"
CAMPUS_DATA="${CAMPUS_DATA:-$HOME/campus-data}"
CAMPUS_TOOLS="${CAMPUS_TOOLS:-$HOME/.campus-tools}"
REPO_URL="${REPO_URL:-https://github.com/kaspAir/university.git}"
BRANCH="${BRANCH:-main}"
API_PORT="${API_PORT:-8060}"
TEMPORAL_PORT="${TEMPORAL_PORT:-7260}"
ENV_FILE="$CAMPUS_DATA/secrets/campus.env"

echo "== Vorprüfung (keine Änderung)"
AVAIL_MB=$(free -m | awk 'NR==2{print $7}'); LOAD=$(cut -d' ' -f1 /proc/loadavg); NPROC=$(ps -u "$(id -un)" --no-headers | wc -l); ULIM=$(ulimit -u)
echo "RAM verfügbar: ${AVAIL_MB} MB · Last: $LOAD · Prozesse: $NPROC von $ULIM"
[ "$AVAIL_MB" -ge 2048 ] || { echo "Weniger als 2 GB RAM verfügbar; Installation abgebrochen" >&2; exit 1; }
[ "$NPROC" -le $((ULIM - 100)) ] || { echo "Prozesslimit zu nah; Installation abgebrochen" >&2; exit 1; }
for p in "$API_PORT" "$TEMPORAL_PORT"; do ss -ltn 2>/dev/null | awk '{print $4}' | grep -q ":$p\$" && { echo "Port $p ist belegt" >&2; exit 1; }; done

echo "== Werkzeuge (uv, Python 3.12, Temporal) in $CAMPUS_TOOLS"
mkdir -p "$CAMPUS_TOOLS/bin"
[ -x "$CAMPUS_TOOLS/bin/uv" ] || curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR="$CAMPUS_TOOLS/bin" UV_NO_MODIFY_PATH=1 sh >/dev/null
export UV_PYTHON_INSTALL_DIR="$CAMPUS_TOOLS/python" UV_CACHE_DIR="$CAMPUS_TOOLS/cache" PATH="$CAMPUS_TOOLS/bin:$PATH"
uv python install 3.12 >/dev/null 2>&1 || true
if [ ! -x "$CAMPUS_TOOLS/temporal" ]; then
  curl -sSL -m 120 -o "$CAMPUS_TOOLS/temporal.tgz" "https://temporal.download/cli/archive/latest?platform=linux&arch=amd64"
  tar xzf "$CAMPUS_TOOLS/temporal.tgz" -C "$CAMPUS_TOOLS" temporal && rm -f "$CAMPUS_TOOLS/temporal.tgz"
fi
"$CAMPUS_TOOLS/temporal" --version

echo "== Repository $CAMPUS_HOME ($BRANCH)"
if [ ! -d "$CAMPUS_HOME/.git" ]; then git clone -q --branch "$BRANCH" "$REPO_URL" "$CAMPUS_HOME"; fi
cd "$CAMPUS_HOME" && git fetch -q origin && git checkout -q --detach "origin/$BRANCH"
SHA="$(git rev-parse HEAD)"

echo "== Virtualenv"
[ -d "$CAMPUS_HOME/.venv" ] || uv venv -q -p 3.12 "$CAMPUS_HOME/.venv"
VIRTUAL_ENV="$CAMPUS_HOME/.venv" uv pip install -q -r requirements.txt -r ops/server/requirements-server.txt
PY="$CAMPUS_HOME/.venv/bin/python"
PG_BIN="$("$PY" -c 'import pgserver._commands as c; print(c.POSTGRES_BIN_PATH)')"

echo "== Datenverzeichnisse"
mkdir -p "$CAMPUS_DATA"/{pg-run,temporal,blobs,logs,run,backups,secrets}
chmod 700 "$CAMPUS_DATA" "$CAMPUS_DATA/secrets" "$CAMPUS_DATA/pg-run"

gen() { "$PY" -c 'import secrets, string; a = string.ascii_letters + string.digits; print("".join(secrets.choice(a) for _ in range(32)))'; }
if [ ! -f "$ENV_FILE" ]; then
  echo "== Geheimnisse erzeugen (nur in $ENV_FILE, chmod 600)"
  PW_SUPER=$(gen); PW_OWNER=$(gen); PW_APP=$(gen); PW_VAULT=$(gen)
  umask 077
  cat > "$ENV_FILE" <<EOF
# Phronesis-Campus Pilot (userland). Generated $(date -Iseconds). Never commit, never print.
CAMPUS_HOME=$CAMPUS_HOME
CAMPUS_DATA=$CAMPUS_DATA
CAMPUS_TOOLS=$CAMPUS_TOOLS
PG_SOCKET_DIR=$CAMPUS_DATA/pg-run
PGPASSWORD_SUPER=$PW_SUPER
CAMPUS_DATABASE_URL=postgresql+psycopg://campus_app:$PW_APP@/campus?host=$CAMPUS_DATA/pg-run
CAMPUS_MIGRATION_DATABASE_URL=postgresql+psycopg://campus_owner:$PW_OWNER@/campus?host=$CAMPUS_DATA/pg-run
CAMPUS_VAULT_DATABASE_URL=postgresql+psycopg://campus_vault:$PW_VAULT@/campus?host=$CAMPUS_DATA/pg-run
CAMPUS_CAPABILITY_LEVEL=development_mock
CAMPUS_COOKIE_SECURE=true
CAMPUS_APPROVED_ADAPTERS=mock
CAMPUS_SANDBOX_RUNNER=none
CAMPUS_BLOB_ROOT=$CAMPUS_DATA/blobs
CAMPUS_TEMPORAL_ADDRESS=127.0.0.1:$TEMPORAL_PORT
CAMPUS_TEMPORAL_NAMESPACE=default
CAMPUS_TEMPORAL_MODE=dev-server-sqlite
CAMPUS_API_PORT=$API_PORT
CAMPUS_BUILD_SHA=$SHA
PYTHONPATH=$CAMPUS_HOME/packages/domain:$CAMPUS_HOME/packages/contracts:$CAMPUS_HOME/packages/infrastructure:$CAMPUS_HOME/services/llm_gateway:$CAMPUS_HOME/services/sandbox_controller:$CAMPUS_HOME/workers/research:$CAMPUS_HOME/apps/api
EOF
  umask 022
fi
set -a; . "$ENV_FILE"; set +a

if [ ! -f "$CAMPUS_DATA/pg/PG_VERSION" ]; then
  echo "== PostgreSQL 16 initialisieren (nur Unix-Socket, scram-sha-256, kein TCP)"
  PWFILE="$CAMPUS_DATA/secrets/.pw"; umask 077; printf '%s' "$PGPASSWORD_SUPER" > "$PWFILE"; umask 022
  "$PG_BIN/initdb" -D "$CAMPUS_DATA/pg" -U postgres -A scram-sha-256 --pwfile="$PWFILE" -E UTF8 --locale=C.UTF-8 >/dev/null
  rm -f "$PWFILE"
  cat >> "$CAMPUS_DATA/pg/postgresql.conf" <<EOF
# --- campus pilot: no network listener, socket in the home directory, modest memory on a shared host
listen_addresses = ''
unix_socket_directories = '$CAMPUS_DATA/pg-run'
max_connections = 40
shared_buffers = 128MB
work_mem = 8MB
log_min_messages = warning
log_line_prefix = '%m [%p] '
EOF
  "$PG_BIN/pg_ctl" -D "$CAMPUS_DATA/pg" -l "$CAMPUS_DATA/logs/postgres.log" -w -t 60 start >/dev/null
  OWNER_PW=$(sed -n 's#.*campus_owner:\([^@]*\)@.*#\1#p' "$ENV_FILE" | head -1)
  APP_PW=$(sed -n 's#.*campus_app:\([^@]*\)@.*#\1#p' "$ENV_FILE" | head -1)
  VAULT_PW=$(sed -n 's#.*campus_vault:\([^@]*\)@.*#\1#p' "$ENV_FILE" | head -1)
  PGPASSWORD="$PGPASSWORD_SUPER" "$PG_BIN/psql" -h "$CAMPUS_DATA/pg-run" -U postgres -d postgres -q -v ON_ERROR_STOP=1 \
    -c "CREATE ROLE campus_owner LOGIN PASSWORD '$OWNER_PW'" -c "CREATE ROLE campus_app LOGIN PASSWORD '$APP_PW'" \
    -c "CREATE ROLE campus_vault LOGIN PASSWORD '$VAULT_PW'" -c "CREATE DATABASE campus OWNER campus_owner"
  echo "PostgreSQL initialisiert; Rollen campus_owner/campus_app/campus_vault angelegt."
fi

echo "== Schema"
"$PY" -m alembic upgrade head 2>&1 | grep -v "^INFO" || true
"$PY" -m alembic current 2>&1 | tail -1
echo "$SHA" > "$CAMPUS_DATA/run/deployed.sha"
chmod +x "$CAMPUS_HOME"/ops/server/*.sh
echo "== fertig. Start: bash $CAMPUS_HOME/ops/server/campusctl.sh start"
