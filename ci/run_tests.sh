#!/bin/sh
# Test run inside python:3.12-slim (CI). Expects CAMPUS_TEST_DATABASE_URL to point at a reachable PostgreSQL.
# Runs as the calling user with HOME=/tmp, so nothing root-owned is left in the workspace.
set -eu

python --version
export PATH="$HOME/.local/bin:$PATH"
pip install --user --no-cache-dir -q -r requirements-dev.txt

echo "Warte auf PostgreSQL ..."
i=0
until python -c "import os, psycopg; psycopg.connect(os.environ['CAMPUS_TEST_DATABASE_URL'].replace('postgresql+psycopg', 'postgresql')).close()" 2>/dev/null; do
  i=$((i + 1))
  if [ "$i" -ge 30 ]; then
    echo "PostgreSQL nach 60 s nicht erreichbar" >&2
    exit 1
  fi
  sleep 2
done
echo "PostgreSQL erreichbar."

mkdir -p reports
python -m pytest -q --junitxml=reports/junit.xml
python -m ruff check apps packages services tests migrations
