#!/bin/sh
# Host check for the campus: run this ON the target host (ssh, then `sh host_check.sh`). Read-only; prints facts, decides nothing.
# What the campus needs: Python 3.12, PostgreSQL 16 reachable, optionally Docker (compose) and Temporal; an HTTPS entry point.
set -u
echo "== Host"; hostname; uname -srm; id -un; echo "home: $HOME"
echo "== CPU/RAM/Disk"; nproc 2>/dev/null; free -m 2>/dev/null | sed -n 1,2p; df -h "$HOME" | tail -1
echo "== Python"
for p in python3 python3.12 python3.11 python3.10 python3.9 "$HOME/.local/bin/python3.12"; do command -v "$p" >/dev/null 2>&1 && printf '%s: ' "$p" && "$p" --version 2>&1; done
command -v uv >/dev/null 2>&1 && uv --version || echo "uv: fehlt (Userland-Python 3.12 waere mit uv moeglich: curl -LsSf https://astral.sh/uv/install.sh | sh; uv python install 3.12)"
echo "== PostgreSQL"
for c in psql pg_ctl pg_config postgres; do command -v "$c" >/dev/null 2>&1 && printf '%s: ' "$c" && "$c" --version 2>&1; done
command -v psql >/dev/null 2>&1 || echo "psql: fehlt (Client); Server-Verfuegbarkeit im Infomaniak-Manager pruefen"
(getent hosts localhost >/dev/null; for port in 5432 5433; do (echo > /dev/tcp/127.0.0.1/$port) 2>/dev/null && echo "Port $port lokal offen" || echo "Port $port lokal geschlossen"; done) 2>/dev/null
echo "== Container"
for c in docker podman systemctl; do command -v "$c" >/dev/null 2>&1 && echo "$c: vorhanden" || echo "$c: fehlt"; done
echo "== Web"
for c in php apache2 httpd nginx caddy node npm git; do command -v "$c" >/dev/null 2>&1 && printf '%s: ' "$c" && "$c" --version 2>&1 | head -1; done
ls -d "$HOME"/sites/* 2>/dev/null
echo "== Prozesse des Benutzers (laufende Gunicorn/Uvicorn)"; ps -u "$(id -un)" -o pid,etime,cmd 2>/dev/null | grep -E "gunicorn|uvicorn|python" | grep -v grep | head
echo "== Fazit (vom Menschen zu ziehen): 3.12 vorhanden? PostgreSQL erreichbar? Docker? Ohne Docker kein Temporal und keine Sandbox."
