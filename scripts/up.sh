#!/usr/bin/env bash
# Starts the database, the business (writer) and the console, and waits until orders are flowing into the archive.
set -euo pipefail
. "$(dirname "$0")/lib.sh"
mkdir -p "$RESULTS_DIR"; chmod 755 "$RESULTS_DIR"

log "Starting the stack (first start pulls Postgres and builds one small image, 2-3 minutes; later starts ~15 seconds)"
$DC up -d --build

log "Waiting for every part to answer"
wait_until 120 db_psql -c 'select 1' && ok "the database accepts connections" || die "Run: $DOCKER logs --tail 30 bdr-db"
wait_http "the writer"  "$WRITER_URL/healthz" 60 || die "Run: $DOCKER logs --tail 30 bdr-writer"
wait_http "the console" "$CONSOLE_URL/healthz" 60 || die "Run: $DOCKER logs --tail 30 bdr-console"
wait_until 60 acked_past 5 && ok "orders are being written" || die "The writer is not writing. Run: $DOCKER logs --tail 30 bdr-writer"
[ -f "$RESULTS_DIR/backups.json" ] || refresh_backups_json

IP="$(public_ip)"
cat <<EOF

Ready. Open this in your browser (from the laptop allowed by the security group):
  Recovery console : http://${IP}:8081/    <- keep it open: orders, WAL archive, backups, and the cost of each disaster

Next: ./scripts/smoke.sh   then   ./scripts/demo.sh gameday
By hand: ./scripts/backup.sh take      (a base backup)      ./scripts/backup.sh list
EOF
