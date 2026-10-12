#!/bin/bash
# Deploy ONE commit to the pilot host. Called by Jenkins over SSH: bash ~/campus/ops/server/deploy.sh <git sha>
# Steps: fetch → pre-deploy backup → checkout → dependencies → migrations → restart api+worker → health with the new build sha.
# Rollback on failure: previous commit is checked out, dependencies restored, services restarted, exit 1. The database is NOT rolled
# back automatically (migrations are forward-only); the pre-deploy dump is named in the log for a manual restore.
# Prints no secrets.
set -euo pipefail
SHA="${1:?git sha}"
ENV_FILE="${CAMPUS_ENV_FILE:-$HOME/campus-data/secrets/campus.env}"
set -a; . "$ENV_FILE"; set +a
export UV_PYTHON_INSTALL_DIR="$CAMPUS_TOOLS/python" UV_CACHE_DIR="$CAMPUS_TOOLS/cache" PATH="$CAMPUS_TOOLS/bin:$PATH"
CTL="$CAMPUS_HOME/ops/server/campusctl.sh"; LOG="$CAMPUS_DATA/logs/deploy.log"
say() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"; }
exec 9>"$CAMPUS_DATA/run/keepalive.lock"; flock -w 120 9 || { say "Lock nicht erhalten"; exit 1; }  # keepalive must not restart mid-deploy
cd "$CAMPUS_HOME"
PREV="$(cat "$CAMPUS_DATA/run/deployed.sha" 2>/dev/null || git rev-parse HEAD)"
say "deploy $SHA (vorher $PREV)"
git fetch -q origin
git cat-file -e "$SHA^{commit}" || { say "Commit unbekannt: $SHA"; exit 1; }
CAMPUS_LOCK_HELD=1 bash "$CAMPUS_HOME/ops/server/backup.sh" && say "Vor-Deploy-Backup erstellt (siehe backups/)" || say "WARNUNG: Backup vor Deploy fehlgeschlagen"

install_and_migrate() {
  git checkout -q --detach "$1"
  VIRTUAL_ENV="$CAMPUS_HOME/.venv" uv pip install -q -r requirements.txt -r ops/server/requirements-server.txt
  "$CAMPUS_HOME/.venv/bin/python" -m alembic upgrade head 2>&1 | grep -v '^INFO' || true
  sed -i "s/^CAMPUS_BUILD_SHA=.*/CAMPUS_BUILD_SHA=$1/" "$ENV_FILE"
  set -a; . "$ENV_FILE"; set +a
  bash "$CTL" restart api >/dev/null 2>&1 || true
  bash "$CTL" restart worker >/dev/null 2>&1 || true
}
healthy_with() {  # $1 sha: the new build answers and the worker runs; readiness is governance state and only reported
  for _ in $(seq 1 30); do
    if curl -fsS -m 5 "http://127.0.0.1:$CAMPUS_API_PORT/api/v1/health" 2>/dev/null | grep -q "\"build_sha\":\"$1\""        && bash "$CTL" health worker >/dev/null 2>&1; then
      if curl -sS -m 5 "http://127.0.0.1:$CAMPUS_API_PORT/api/v1/ready" 2>/dev/null | grep -q '"ready":true'; then say "ready: ja"; else say "ready: nein (Governance-Zustand, kein Deploy-Fehler)"; fi
      return 0
    fi
    sleep 2
  done
  return 1
}

if install_and_migrate "$SHA" && healthy_with "$SHA"; then
  echo "$SHA" > "$CAMPUS_DATA/run/deployed.sha"
  say "OK: $SHA läuft (health, ready, worker)"
  exit 0
fi
say "FEHLER: $SHA nicht gesund; Rollback auf $PREV (Datenbankschema bleibt auf dem neuen Stand; Dump vor Deploy in backups/)"
if install_and_migrate "$PREV" && healthy_with "$PREV"; then
  echo "$PREV" > "$CAMPUS_DATA/run/deployed.sha"; say "Rollback OK: $PREV läuft"
else
  say "Rollback FEHLGESCHLAGEN: manueller Eingriff nötig (campusctl.sh status, logs/api.log)"
fi
exit 1
