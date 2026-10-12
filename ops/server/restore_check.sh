#!/bin/bash
# Proves the latest PostgreSQL dump restores: into a scratch database, checks the migration head and table count, drops the scratch db.
# Cron: 45 2 * * * /bin/bash $HOME/campus/ops/server/restore_check.sh   (after backup.sh). Writes OK/FAIL to logs/restore_check.log.
set -u
ENV_FILE="${CAMPUS_ENV_FILE:-$HOME/campus-data/secrets/campus.env}"
set -a; . "$ENV_FILE"; set +a
LOG="$CAMPUS_DATA/logs/restore_check.log"; DEST="$CAMPUS_DATA/backups"
PY="$CAMPUS_HOME/.venv/bin/python"; PG_BIN="$("$PY" -c 'import pgserver._commands as c; print(c.POSTGRES_BIN_PATH)')"
say() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; echo "$*"; }
LATEST="$(ls -1t "$DEST"/pg-*.dump 2>/dev/null | head -1)"
[ -n "$LATEST" ] || { say "FAIL: kein Dump vorhanden"; exit 1; }
export PGPASSWORD="$PGPASSWORD_SUPER"
PSQL=("$PG_BIN/psql" -h "$PG_SOCKET_DIR" -U postgres -d postgres -q -v ON_ERROR_STOP=1)
"${PSQL[@]}" -c "DROP DATABASE IF EXISTS campus_restorecheck" -c "CREATE DATABASE campus_restorecheck OWNER campus_owner" >/dev/null 2>&1 || { say "FAIL: scratch db"; exit 1; }
OWNER_PW=$(sed -n 's#.*campus_owner:\([^@]*\)@.*#\1#p' "$ENV_FILE" | head -1)
if PGPASSWORD="$OWNER_PW" "$PG_BIN/pg_restore" -h "$PG_SOCKET_DIR" -U campus_owner -d campus_restorecheck --no-owner --no-privileges "$LATEST" >/dev/null 2>&1; then
  HEAD="$(PGPASSWORD="$OWNER_PW" "$PG_BIN/psql" -h "$PG_SOCKET_DIR" -U campus_owner -d campus_restorecheck -tAc "select version_num from alembic_version" 2>/dev/null)"
  TABLES="$(PGPASSWORD="$OWNER_PW" "$PG_BIN/psql" -h "$PG_SOCKET_DIR" -U campus_owner -d campus_restorecheck -tAc "select count(*) from information_schema.tables where table_schema in ('public','vault')" 2>/dev/null)"
  EXPECTED="$(cd "$CAMPUS_HOME" && "$PY" -m alembic heads 2>/dev/null | awk '{print $1}')"
  if [ -n "$HEAD" ] && [ "$HEAD" = "$EXPECTED" ] && [ "${TABLES:-0}" -ge 30 ]; then say "OK: $(basename "$LATEST") → head $HEAD, $TABLES Tabellen"; rc=0
  else say "FAIL: $(basename "$LATEST") → head '$HEAD' (erwartet '$EXPECTED'), $TABLES Tabellen"; rc=1; fi
else say "FAIL: pg_restore $(basename "$LATEST")"; rc=1; fi
"${PSQL[@]}" -c "DROP DATABASE IF EXISTS campus_restorecheck" >/dev/null 2>&1
exit $rc
