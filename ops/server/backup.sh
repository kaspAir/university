#!/bin/bash
# Daily backup: PostgreSQL (pg_dump custom format), Temporal sqlite (online backup via the sqlite3 backup API), blobs (tar).
# Retention 14 days. Cron: 30 2 * * * /bin/bash $HOME/campus/ops/server/backup.sh
set -u
ENV_FILE="${CAMPUS_ENV_FILE:-$HOME/campus-data/secrets/campus.env}"
set -a; . "$ENV_FILE"; set +a
STAMP="$(date '+%Y%m%d-%H%M%S')"; DEST="$CAMPUS_DATA/backups"; LOG="$CAMPUS_DATA/logs/backup.log"; mkdir -p "$DEST"
PY="$CAMPUS_HOME/.venv/bin/python"; PG_BIN="$("$PY" -c 'import pgserver._commands as c; print(c.POSTGRES_BIN_PATH)')"
OWNER_PW=$(sed -n 's#.*campus_owner:\([^@]*\)@.*#\1#p' "$ENV_FILE" | head -1)
say() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; }
rc=0
if PGPASSWORD="$OWNER_PW" "$PG_BIN/pg_dump" -h "$PG_SOCKET_DIR" -U campus_owner -d campus -Fc -f "$DEST/pg-$STAMP.dump"; then
  say "pg_dump ok: pg-$STAMP.dump ($(du -h "$DEST/pg-$STAMP.dump" | cut -f1))"
else say "pg_dump FEHLGESCHLAGEN"; rc=1; fi
if [ -f "$CAMPUS_DATA/temporal/temporal.db" ]; then
  if "$PY" - "$CAMPUS_DATA/temporal/temporal.db" "$DEST/temporal-$STAMP.db" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
with dst: src.backup(dst)
src.close(); dst.close()
PY
  then say "temporal sqlite ok: temporal-$STAMP.db"; else say "temporal sqlite FEHLGESCHLAGEN"; rc=1; fi
fi
if tar czf "$DEST/blobs-$STAMP.tgz" -C "$CAMPUS_DATA" blobs 2>/dev/null; then say "blobs ok"; else say "blobs FEHLGESCHLAGEN"; rc=1; fi
find "$DEST" -maxdepth 1 -type f \( -name 'pg-*.dump' -o -name 'temporal-*.db' -o -name 'blobs-*.tgz' \) -mtime +14 -delete
[ $rc -eq 0 ] && say "backup abgeschlossen" || say "backup mit Fehlern"
exit $rc
