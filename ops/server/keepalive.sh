#!/bin/bash
# Cron keepalive with health checks, logging and a lock against concurrent starts. Restarts only what is unhealthy.
# Cron: */3 * * * * /bin/bash $HOME/campus/ops/server/keepalive.sh ; @reboot likewise.
set -u
CAMPUS_HOME="${CAMPUS_HOME:-$HOME/campus}"
ENV_FILE="${CAMPUS_ENV_FILE:-$HOME/campus-data/secrets/campus.env}"
[ -f "$ENV_FILE" ] || exit 0
set -a; . "$ENV_FILE"; set +a
LOCK="$CAMPUS_DATA/run/keepalive.lock"; LOG="$CAMPUS_DATA/logs/keepalive.log"; mkdir -p "$CAMPUS_DATA/run" "$CAMPUS_DATA/logs"
exec 9>"$LOCK"
flock -n 9 || exit 0  # another keepalive (or a deploy holding the same lock) is running
CTL="$CAMPUS_HOME/ops/server/campusctl.sh"
for comp in pg temporal api worker; do
  if ! bash "$CTL" health "$comp" >/dev/null 2>&1; then
    printf '%s %s ungesund, starte neu\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$comp" >> "$LOG"
    bash "$CTL" start "$comp" >> "$LOG" 2>&1 || printf '%s %s Neustart fehlgeschlagen\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$comp" >> "$LOG"
  fi
done
# keep the log bounded (last 5000 lines)
if [ -f "$LOG" ] && [ "$(wc -l < "$LOG")" -gt 6000 ]; then tail -n 5000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"; fi
exit 0
