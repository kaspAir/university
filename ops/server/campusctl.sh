#!/bin/bash
# Control of the userland pilot environment: PostgreSQL (socket only), Temporal dev server (127.0.0.1), API (127.0.0.1), worker.
# Usage: campusctl.sh {start|stop|restart|status|health} [pg|temporal|api|worker ...|all]   (several components allowed)
# Started processes never inherit fd 9 (the flock of keepalive/deploy); otherwise a running API would hold the lock forever.
# All state lives below $CAMPUS_DATA; nothing is public: PostgreSQL has no TCP listener, Temporal and the API bind to 127.0.0.1.
set -u
ENV_FILE="${CAMPUS_ENV_FILE:-$HOME/campus-data/secrets/campus.env}"
[ -f "$ENV_FILE" ] || { echo "env file fehlt: $ENV_FILE" >&2; exit 2; }
set -a; . "$ENV_FILE"; set +a
RUN="$CAMPUS_DATA/run"; LOGS="$CAMPUS_DATA/logs"; mkdir -p "$RUN" "$LOGS"
VENV="$CAMPUS_HOME/.venv"
PG_BIN="$("$VENV/bin/python" -c 'import pgserver._commands as c; print(c.POSTGRES_BIN_PATH)')"
TEMPORAL_BIN="$CAMPUS_TOOLS/temporal"
PGDATA="$CAMPUS_DATA/pg"
log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOGS/campusctl.log"; echo "$*"; }

pid_alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }
stop_pid() {  # $1 pidfile $2 name
  if pid_alive "$1"; then
    local pid; pid="$(cat "$1")"; kill -TERM "$pid" 2>/dev/null
    for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null
    log "$2 gestoppt (pid $pid)"
  fi
  rm -f "$1"
}

# ---------------------------------------------------------------- postgresql
health_pg() { "$PG_BIN/pg_isready" -h "$PG_SOCKET_DIR" -d campus -U campus_app -q; }
start_pg() {
  health_pg && return 0
  "$PG_BIN/pg_ctl" -D "$PGDATA" -l "$LOGS/postgres.log" -w -t 60 start >/dev/null 2>&1 && log "postgresql gestartet"
  health_pg
}
stop_pg() { "$PG_BIN/pg_ctl" -D "$PGDATA" -m fast -w -t 60 stop >/dev/null 2>&1 && log "postgresql gestoppt"; }

# ---------------------------------------------------------------- temporal (dev server, sqlite persistence; NOT production grade)
health_temporal() { "$TEMPORAL_BIN" operator cluster health --address "$CAMPUS_TEMPORAL_ADDRESS" 2>/dev/null | grep -q SERVING; }
start_temporal() {
  health_temporal && return 0
  stop_pid "$RUN/temporal.pid" temporal
  mkdir -p "$CAMPUS_DATA/temporal"
  nohup "$TEMPORAL_BIN" server start-dev --ip 127.0.0.1 --port "${CAMPUS_TEMPORAL_ADDRESS##*:}" --headless \
    --db-filename "$CAMPUS_DATA/temporal/temporal.db" --namespace "$CAMPUS_TEMPORAL_NAMESPACE" --log-level warn >> "$LOGS/temporal.log" 2>&1 9>&- &
  echo $! > "$RUN/temporal.pid"
  for _ in $(seq 1 30); do health_temporal && { log "temporal gestartet (pid $(cat "$RUN/temporal.pid"))"; return 0; }; sleep 1; done
  log "temporal: nicht gesund nach 30 s"; return 1
}
stop_temporal() { stop_pid "$RUN/temporal.pid" temporal; }

# ---------------------------------------------------------------- api (uvicorn, 127.0.0.1 only; the PHP proxy of the site forwards to it)
# liveness of the process; readiness (active policy and catalog) is governance state, reported by status, never a reason to restart
health_api() { curl -fsS -m 5 "http://127.0.0.1:$CAMPUS_API_PORT/api/v1/health" 2>/dev/null | grep -q '"status":"ok"'; }
ready_api() { curl -sS -m 5 "http://127.0.0.1:$CAMPUS_API_PORT/api/v1/ready" 2>/dev/null | grep -q '"ready":true'; }
start_api() {
  health_api && return 0
  stop_pid "$RUN/api.pid" api
  cd "$CAMPUS_HOME" || return 1
  nohup "$VENV/bin/python" -m uvicorn campus_api.main:app --host 127.0.0.1 --port "$CAMPUS_API_PORT" --workers 2 \
    --proxy-headers --forwarded-allow-ips 127.0.0.1 --no-server-header --log-level warning >> "$LOGS/api.log" 2>&1 9>&- &
  echo $! > "$RUN/api.pid"
  for _ in $(seq 1 30); do health_api && { log "api gestartet (pid $(cat "$RUN/api.pid"))"; return 0; }; sleep 1; done
  log "api: nicht bereit nach 30 s"; return 1
}
stop_api() { stop_pid "$RUN/api.pid" api; }

# ---------------------------------------------------------------- worker (temporal activities; shares the invocation guard of the API)
health_worker() { pid_alive "$RUN/worker.pid"; }
start_worker() {
  health_worker && return 0
  rm -f "$RUN/worker.pid"
  cd "$CAMPUS_HOME" || return 1
  nohup "$VENV/bin/python" -m campus_workers.worker >> "$LOGS/worker.log" 2>&1 9>&- &
  echo $! > "$RUN/worker.pid"
  sleep 2; health_worker && { log "worker gestartet (pid $(cat "$RUN/worker.pid"))"; return 0; }
  log "worker: sofort beendet, siehe logs/worker.log"; return 1
}
stop_worker() { stop_pid "$RUN/worker.pid" worker; }

# ---------------------------------------------------------------- dispatch
COMPS="pg temporal api worker"
cmd="${1:-status}"; shift || true
# any number of components in dependency order ("restart api worker" restarts both); none or "all" means every component
if [ "$#" -gt 0 ] && [ "$*" != all ]; then
  for c in "$@"; do case "$c" in pg|temporal|api|worker) ;; *) echo "unbekannte Komponente: $c" >&2; exit 2 ;; esac; done
  COMPS="$(for k in pg temporal api worker; do for c in "$@"; do [ "$c" = "$k" ] && printf '%s ' "$k"; done; done)"
fi
rc=0
case "$cmd" in
  start)   for c in $COMPS; do "start_$c" || rc=1; done ;;
  stop)    for c in $(echo "$COMPS" | tr ' ' '\n' | tac); do "stop_$c"; done ;;
  restart) for c in $(echo "$COMPS" | tr ' ' '\n' | tac); do "stop_$c"; done; for c in $COMPS; do "start_$c" || rc=1; done ;;
  health)  for c in $COMPS; do if "health_$c"; then echo "$c: ok"; else echo "$c: FEHLT"; rc=1; fi; done ;;
  status)  for c in $COMPS; do if "health_$c"; then echo "$c: ok"; else echo "$c: nicht gesund"; rc=1; fi; done
           echo "build: $(cat "$RUN/deployed.sha" 2>/dev/null || echo unbekannt)"
           if ready_api; then echo "ready: ja (Policy und Katalog aktiv)"; else echo "ready: nein (Policy oder Katalog fehlt; kein Neustartgrund)"; fi ;;
  *) echo "usage: $0 {start|stop|restart|status|health} [pg|temporal|api|worker ...|all]" >&2; exit 2 ;;
esac
exit $rc
