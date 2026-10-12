#!/bin/bash
# Adds the campus entries to the SHARED crontab of the account, idempotently, with a backup and a check afterwards.
# Existing lines of the sibling products are never touched. Usage: bash crontab_install.sh [--remove]
set -euo pipefail
CAMPUS_HOME="${CAMPUS_HOME:-$HOME/campus}"
CAMPUS_DATA="${CAMPUS_DATA:-$HOME/campus-data}"
MARK="# campus"
BACKUP_DIR="$CAMPUS_DATA/backups"; mkdir -p "$BACKUP_DIR"
STAMP="$(date '+%Y%m%d-%H%M%S')"
BEFORE="$BACKUP_DIR/crontab-$STAMP.before"
crontab -l > "$BEFORE" 2>/dev/null || : > "$BEFORE"
echo "Crontab gesichert: $BEFORE ($(wc -l < "$BEFORE") Zeilen)"
# existing lines without any campus-marked line (idempotent re-run)
grep -v "$MARK" "$BEFORE" > "$BACKUP_DIR/crontab-$STAMP.others" || true
NEW="$BACKUP_DIR/crontab-$STAMP.new"
cp "$BACKUP_DIR/crontab-$STAMP.others" "$NEW"
if [ "${1:-}" != "--remove" ]; then
  cat >> "$NEW" <<EOF
$MARK keepalive (ADR-0015): flock prevents overlap with backup and deploy
@reboot /bin/bash $CAMPUS_HOME/ops/server/keepalive.sh $MARK
*/3 * * * * /bin/bash $CAMPUS_HOME/ops/server/keepalive.sh $MARK
$MARK backup, then restore check ONLY after a successful backup
30 2 * * * /bin/bash $CAMPUS_HOME/ops/server/backup.sh && /bin/bash $CAMPUS_HOME/ops/server/restore_check.sh $MARK
EOF
fi
crontab "$NEW"
AFTER="$BACKUP_DIR/crontab-$STAMP.after"; crontab -l > "$AFTER"
# check: every previous non-campus line is still present, exactly once
missing=0
while IFS= read -r line; do
  [ -z "$line" ] && continue
  grep -qxF -- "$line" "$AFTER" || { echo "FEHLT nach Änderung: $line" >&2; missing=1; }
done < "$BACKUP_DIR/crontab-$STAMP.others"
dups="$(grep "$MARK" "$AFTER" | grep -v "^$MARK" | sort | uniq -d | wc -l)"
if [ "$missing" -ne 0 ] || [ "$dups" -ne 0 ]; then
  echo "Nachkontrolle fehlgeschlagen; stelle die gesicherte Crontab wieder her" >&2
  crontab "$BEFORE"; exit 1
fi
echo "Nachkontrolle ok: $(grep -vc "$MARK" "$AFTER" || true) fremde Zeilen unverändert, $(grep -c "$MARK" "$AFTER") Campus-Zeilen (inkl. Kommentare), keine Duplikate"
grep "$MARK" "$AFTER"
