#!/usr/bin/env bash
# Health check of the running demo (run ./scripts/up.sh first). Ends with "All 13 checks passed."
set -uo pipefail
. "$(dirname "$0")/lib.sh"

TOTAL=0; BAD=0
check() { # LABEL COMMAND...
  local label="$1"; shift
  TOTAL=$((TOTAL + 1))
  if "$@" >/dev/null 2>&1; then ok "$label"; else fail "$label"; BAD=$((BAD + 1)); fi
}
code_is() { [ "$(http_code "$1")" = "$2" ]; }
three_running() { [ "$($DC ps --status running -q | wc -l | tr -d ' ')" = "$EXPECTED_CONTAINERS" ]; }
setting_is() { [ "$(db_psql -c "show $1")" = "$2" ]; }
orders_flow() { local a b; a="$(writer_field acked_rows)"; sleep 2; b="$(writer_field acked_rows)"; [ "$b" -gt "$a" ]; }
db_has_orders() { [ "$(db_psql -c 'select count(*) from orders')" -gt 0 ]; }
archive_healthy() { [ "$(archive_field failed_count)" = "0" ] && [ -n "$(archive_field last_archived_wal)" ]; }
archive_moves() { local a b; a="$(archive_field archived_count)"; db_psql -c 'select pg_switch_wal()' >/dev/null; sleep 3; b="$(archive_field archived_count)"; [ "$b" -gt "$a" ]; }
console_has_data() { local d; d="$(curl -s -m 6 "$CONSOLE_URL/api/live")"; [[ "$d" == *'"db": {"up": true'* && "$d" == *'"writer"'* ]]; }
results_dir() { test -d "$RESULTS_DIR"; }
restore_script_ok() { bash -n "$ROOT/docker/restore-prepare.sh"; }

check "Docker daemon reachable"                          $DOCKER info
check "all 3 containers are running"                     three_running
check "the database accepts queries"                     db_psql -c 'select 1'
check "WAL archiving is on"                              setting_is archive_mode on
check "a WAL segment is closed at least every 10 s"      setting_is archive_timeout 10s
check "orders are being written (acknowledged rows grow)" orders_flow
check "the orders table has rows"                        db_has_orders
check "the archiver has no failures"                     archive_healthy
check "the archive grows when a segment is closed"       archive_moves
check "the writer answers on its control port"           code_is "$WRITER_URL/healthz" 200
check "the console is up and shows live data"            console_has_data
check "the results folder exists"                        results_dir
check "the restore script is valid"                      restore_script_ok

echo
if [ "$BAD" -eq 0 ]; then echo "All $TOTAL checks passed."; exit 0; fi
echo "$BAD of $TOTAL checks failed." >&2
exit 1
