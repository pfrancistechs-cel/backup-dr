#!/usr/bin/env bash
# Scripted disaster-recovery tour. Usage: ./scripts/demo.sh [backup|oops|drill|retention|crash|gameday]
# Keep the recovery console (http://<EC2-PUBLIC-IP>:8081/) open while it runs.
#   backup     a base backup, and the WAL archive filling up by itself
#   oops       someone runs DELETE FROM orders. Point-in-time recovery to the second before it, into a SECOND database;
#              then the lost rows are copied back into production
#   drill      "a backup you have not restored is not a backup": rebuild the newest state in a throw-away database
#   retention  keep the newest N base backups and delete the WAL nobody needs
#   crash      the database and its disk are destroyed. Rebuild from the newest backup plus the archive.
#              The script measures the RTO (how long) and the RPO (how much business was lost)
set -uo pipefail
. "$(dirname "$0")/lib.sh"

RTO_LIMIT=120          # seconds we promise for a full rebuild on this instance
RPO_LIMIT=20           # seconds of business we may lose (archive_timeout is 10)

FAILURES=0
assert() { # LABEL COMMAND...
  local label="$1"; shift
  if "$@"; then ok "$label"; else fail "$label"; FAILURES=$((FAILURES + 1)); fi
}
finish() {
  if [ "$FAILURES" -eq 0 ]; then printf '\n\033[1;32mPASS\033[0m  %s\n\n' "$1"; else printf '\n\033[1;31mFAIL\033[0m  %s\n\n' "$1"; exit 1; fi
}
eq() { [ "$1" = "$2" ]; }
le() { awk -v a="$1" -v b="$2" 'BEGIN { exit !(a <= b) }'; }
contiguous() { [ "$(db_psql -c 'select count(*) = max(id) and min(id) = 1 from orders')" = "t" ]; }
rs_contiguous() { [ "$(rs_psql -c 'select count(*) = max(id) and min(id) = 1 from orders')" = "t" ]; }

need_stack() {
  db_psql -c 'select 1' >/dev/null 2>&1 && [ "$(http_code "$WRITER_URL/healthz")" = "200" ] || die "The stack is not running. Run ./scripts/up.sh first."
  mkdir -p "$RESULTS_DIR"
  writer_api POST /resume >/dev/null
}

scenario_backup() {
  log "SCENARIO backup: a base backup, plus WAL archiving that never stops"
  need_stack
  local before name failed
  before="$(archive_field archived_count)"
  name="$(backup_take)"
  say "base backup $name taken"
  assert "the backup has its data and its WAL"           test "$($DOCKER exec bdr-db sh -c "ls /archive/base/$name | grep -c 'tar.gz'")" -ge 2
  assert "the backup is listed"                          test -n "$(backup_names | grep "^$name$")"
  say "waiting for the archive to grow by itself (a WAL segment is closed every 10 seconds) ..."
  assert "new WAL segments reached the archive"          wait_until 40 archive_past "$before"
  failed="$(archive_field failed_count)"
  assert "the archiver has never failed"                 eq "$failed" 0
  finish backup
}

scenario_oops() {
  log "SCENARIO oops: a bad DELETE in production. Rewind a SECOND database to the moment before it"
  need_stack
  local name rp lsn seg n0 m0 t0 rto prod_after
  name="$(backup_take)"; say "base backup $name taken"
  sleep 3                                              # some orders after the backup, to be replayed from the archive
  rp="before_oops_$(date +%H%M%S)"
  writer_api POST /pause >/dev/null                    # so the numbers below are exact
  lsn="$(db_psql -c "select pg_create_restore_point('$rp')")"
  seg="$(db_psql -c "select pg_walfile_name('$lsn')")"
  n0="$(db_psql -c 'select count(*) from orders')"; m0="$(db_psql -c 'select max(id) from orders')"
  say "restore point '$rp' set. Production has $n0 orders (last id $m0)"
  db_psql -c 'delete from orders' >/dev/null
  say "OOPS: DELETE FROM orders. Production has $(db_psql -c 'select count(*) from orders') orders now"
  writer_api POST /resume >/dev/null
  db_psql -c 'select pg_switch_wal()' >/dev/null
  assert "the archive holds the WAL up to the restore point" wait_archived "$seg"
  t0="$(now)"
  assert "the second database was rebuilt and replayed to the restore point" restore_instance restore_point "$rp"
  rto="$(elapsed "$t0")"
  say "restore instance ready after $rto s"
  assert "it has exactly the $n0 orders from before the DELETE" eq "$(rs_psql -c 'select count(*) from orders')" "$n0"
  assert "its newest order is id $m0"                    eq "$(rs_psql -c 'select max(id) from orders')" "$m0"
  assert "production really lost them (fewer orders than before)" test "$(db_psql -c 'select count(*) from orders')" -lt "$n0"
  say "copying the lost orders from the restored database back into production ..."
  db_psql -c 'drop table if exists orders_recovered' -c 'create table orders_recovered (like orders)' >/dev/null
  rs_psql -c 'copy orders to stdout' | db_psql -c 'copy orders_recovered from stdin'
  db_psql -c 'insert into orders select * from orders_recovered on conflict do nothing' -c 'drop table orders_recovered' >/dev/null
  assert "production has every order again, with no gaps" contiguous
  assert "production has at least the $n0 orders"        test "$(db_psql -c 'select count(*) from orders')" -ge "$n0"
  prod_after="$(db_psql -c 'select count(*) from orders')"
  write_report oops "$rto" "" "" "Rewound a second database to '$rp' and copied $n0 orders back. Production never stopped."
  drop_restore_instance
  say "production has $prod_after orders, the writer never stopped"
  finish oops
}

scenario_drill() {
  log "SCENARIO drill: restore the newest state into a throw-away database and check it"
  need_stack
  [ "$(backup_count)" -gt 0 ] || backup_take >/dev/null
  sleep 12                                             # let the archive catch up with the newest orders
  local prod_last t0 rto restored
  prod_last="$(writer_field last_acked_id)"
  t0="$(now)"
  assert "the restore database came up from backup + archive" restore_instance latest
  rto="$(elapsed "$t0")"
  restored="$(rs_psql -c 'select max(id) from orders')"
  say "restore took $rto s. Production last id at the start: $prod_last, restored last id: $restored"
  assert "the restored data has no gaps"                 rs_contiguous
  assert "it is at most 25 seconds behind production"    test "$restored" -ge "$((prod_last - 250))"
  assert "it took less than $RTO_LIMIT seconds"          le "$rto" "$RTO_LIMIT"
  write_report drill "$rto" "" "" "Restored backup + archive into a second database; max id $restored."
  drop_restore_instance
  finish drill
}

scenario_retention() {
  log "SCENARIO retention: keep the newest 2 base backups, delete the WAL nobody needs any more"
  need_stack
  local i wal_before wal_after
  for i in 1 2 3; do backup_take >/dev/null; sleep 1.2; done
  wal_before="$(wal_count)"
  say "before: $(backup_count) base backups, $wal_before WAL segments"
  backup_prune 2
  wal_after="$(wal_count)"
  say "after:  $(backup_count) base backups, $wal_after WAL segments"
  assert "exactly 2 base backups are left"               eq "$(backup_count)" 2
  assert "the archive did not grow"                      test "$wal_after" -le "$wal_before"
  assert "a restore from what is left still works"       restore_instance latest
  assert "the restored data has no gaps"                 rs_contiguous
  drop_restore_instance
  finish retention
}

scenario_crash() {
  log "SCENARIO crash: the database container and its disk are destroyed. Rebuild from backup + archive"
  need_stack
  local t0 rto lost secs rows_before
  backup_take >/dev/null
  say "a fresh base backup exists; orders keep flowing for 12 more seconds ..."
  sleep 12
  rows_before="$(writer_field last_acked_id)"
  say "the business has been told that orders up to id $rows_before are safe"
  t0="$(now)"
  $DOCKER rm -f bdr-db >/dev/null                      # kill -9: no clean shutdown, no last WAL flush to the archive
  $DOCKER volume rm -f "$VOL_PGDATA" >/dev/null
  say "DISASTER: the database is gone and so is its disk"
  assert "the writer notices: the database is unreachable" wait_until 30 writer_down
  assert "the rebuilt database came up and accepts writes" rebuild_primary
  rto="$(elapsed "$t0")"
  assert "the writer reconnected and compared notes"     wait_until 40 test -n "$(writer_field recovery.lost_rows)"
  lost="$(writer_field recovery.lost_rows)"; secs="$(writer_field recovery.data_loss_seconds)"
  echo
  say "RTO (disaster -> serving again): $rto s   (limit: $RTO_LIMIT s)"
  say "RPO (business lost):             $lost orders = ${secs:-0} s of orders"
  echo
  assert "RTO is under $RTO_LIMIT seconds"               le "$rto" "$RTO_LIMIT"
  assert "RPO is under $RPO_LIMIT seconds of business"   le "${secs:-0}" "$RPO_LIMIT"
  assert "the restored data has no gaps"                 contiguous
  assert "the database is on a new timeline (2)"         eq "$(db_psql -c 'select timeline_id from pg_control_checkpoint()')" 2
  assert "orders flow again"                             wait_until 25 writer_past "$rows_before"
  write_report crash "$rto" "$lost" "$secs" "Rebuilt from the newest base backup plus the archive. Lost orders were never archived: archive_timeout=10 s bounds the loss."
  finish crash
}

case "${1:-gameday}" in
  backup)    scenario_backup ;;
  oops)      scenario_oops ;;
  drill)     scenario_drill ;;
  retention) scenario_retention ;;
  crash)     scenario_crash ;;
  gameday)   scenario_backup; scenario_oops; scenario_drill; scenario_retention; scenario_crash
             printf '\033[1;32mALL SCENARIOS PASSED\033[0m\n' ;;
  *) echo "Usage: $0 [backup|oops|drill|retention|crash|gameday]" >&2; exit 2 ;;
esac
