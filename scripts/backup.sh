#!/usr/bin/env bash
# Base backups of the running database. The WAL archive (continuous) is already filling up by itself.
#   ./scripts/backup.sh take         one base backup now (about 2 seconds)
#   ./scripts/backup.sh list         the base backups and the archive size
#   ./scripts/backup.sh prune [N]    keep the newest N (default 3) base backups; delete the WAL nobody needs any more
set -uo pipefail
. "$(dirname "$0")/lib.sh"
mkdir -p "$RESULTS_DIR"

case "${1:-}" in
  take)
    name="$(backup_take)" || die "pg_backup failed. Run: $DOCKER logs --tail 30 bdr-db"
    echo "Base backup $name taken. Archive: $(backup_count) base backup(s), $(wal_count) WAL segment(s)." ;;
  list)
    echo "Base backups:"; backup_names | sed 's/^/  /'
    echo "WAL segments in the archive: $(wal_count)   archiver failures so far: $(archive_field failed_count)" ;;
  prune)
    before="$(wal_count)"
    backup_prune "${2:-3}"
    echo "Kept $(backup_count) base backup(s). WAL segments: $before -> $(wal_count)." ;;
  *) echo "Usage: $0 take | list | prune [N]" >&2; exit 2 ;;
esac
