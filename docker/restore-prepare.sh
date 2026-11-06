#!/usr/bin/env bash
# Runs INSIDE a throw-away container as the postgres user. Turns a base backup plus the WAL archive into a data
# directory that Postgres will replay up to a chosen point and then open for business.
#   restore-prepare.sh BACKUP|latest  latest|restore_point|time  [VALUE]
# Environment: ARCHIVE_DIR (default /archive), PGDATA_DIR (default /var/lib/postgresql/data)
set -euo pipefail
ARCHIVE_DIR="${ARCHIVE_DIR:-/archive}"
PGDATA_DIR="${PGDATA_DIR:-/var/lib/postgresql/data}"
backup="${1:?backup name or latest}"
kind="${2:?latest, restore_point or time}"
value="${3:-}"

if [ "$backup" = "latest" ]; then
  backup="$(ls -1 "$ARCHIVE_DIR/base" | sort | tail -n 1)"
fi
[ -n "$backup" ] && [ -f "$ARCHIVE_DIR/base/$backup/base.tar.gz" ] || { echo "no such base backup: '$backup'" >&2; exit 1; }
case "$kind" in
  latest) ;;
  restore_point|time) [ -n "$value" ] || { echo "$kind needs a value" >&2; exit 1; } ;;
  *) echo "unknown recovery target kind: $kind" >&2; exit 1 ;;
esac

echo "restore-prepare: backup $backup, target $kind ${value:-end of archive}"
find "$PGDATA_DIR" -mindepth 1 -delete
tar -xzf "$ARCHIVE_DIR/base/$backup/base.tar.gz" -C "$PGDATA_DIR"
mkdir -p "$PGDATA_DIR/pg_wal"
tar -xzf "$ARCHIVE_DIR/base/$backup/pg_wal.tar.gz" -C "$PGDATA_DIR/pg_wal"
chmod 700 "$PGDATA_DIR"

{
  echo "restore_command = 'gunzip -c $ARCHIVE_DIR/wal/%f.gz > %p'"
  echo "recovery_target_action = 'promote'"
  case "$kind" in
    restore_point) echo "recovery_target_name = '$value'" ;;
    time)          echo "recovery_target_time = '$value'" ;;
  esac
} >> "$PGDATA_DIR/postgresql.auto.conf"
touch "$PGDATA_DIR/recovery.signal"
echo "restore-prepare: done. Start Postgres on $PGDATA_DIR and it will replay the archive."
