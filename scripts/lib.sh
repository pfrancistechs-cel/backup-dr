#!/usr/bin/env bash
# Shared helpers. Sourced by the other scripts, not run directly.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

# Use plain docker if the current user may talk to the daemon, otherwise sudo.
if docker info >/dev/null 2>&1; then DOCKER="docker"; else DOCKER="sudo docker"; fi
DC="$DOCKER compose"

CONSOLE_URL="${CONSOLE_URL:-http://127.0.0.1:8081}"
WRITER_URL="${WRITER_URL:-http://127.0.0.1:9100}"
RESULTS_DIR="${RESULTS_DIR:-$ROOT/results}"
EXPECTED_CONTAINERS=3
VOL_PGDATA=backupdr_pgdata
VOL_RESTORE=backupdr_pgdata_restore
VOL_ARCHIVE=backupdr_archive
export CONSOLE_URL WRITER_URL RESULTS_DIR

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '  \033[1;32mok\033[0m   %s\n' "$*"; }
fail() { printf '  \033[1;31mFAIL\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }
say()  { printf '    %s\n' "$*"; }

ensure_env() { # create .env on first run
  [ -f "$ROOT/.env" ] || cp "$ROOT/.env.example" "$ROOT/.env"
  set -a; . "$ROOT/.env"; set +a
}
ensure_env
POSTGRES_IMAGE="${POSTGRES_IMAGE:-postgres:16}"

public_ip() { # EC2 metadata (IMDSv2), then a public what-is-my-ip fallback
  local tok ip=""
  tok="$(curl -fsS -m 2 -X PUT http://169.254.169.254/latest/api/token \
         -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' 2>/dev/null || true)"
  if [ -n "$tok" ]; then
    ip="$(curl -fsS -m 2 -H "X-aws-ec2-metadata-token: $tok" \
          http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || true)"
  fi
  if [ -z "$ip" ]; then
    ip="$(curl -fsS -m 4 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]' || true)"
  fi
  echo "${ip:-<EC2-PUBLIC-IP>}"
}

http_code() { curl -s -o /dev/null -m 5 -w '%{http_code}' "$@" 2>/dev/null || true; }   # 000 = no connection

wait_http() { # LABEL URL [SECONDS]  (any 2xx)
  local label="$1" url="$2" secs="${3:-60}" i
  for ((i = 0; i < secs; i++)); do
    if curl -fsS -o /dev/null --max-time 2 "$url" 2>/dev/null; then ok "$label is up"; return 0; fi
    sleep 1
  done
  fail "$label did not answer at $url within ${secs}s"
  return 1
}
wait_until() { # SECONDS COMMAND...   (retries every second)
  local secs="$1" i; shift
  for ((i = 0; i < secs; i++)); do
    "$@" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

now()     { date +%s.%N; }
elapsed() { awk -v a="$1" -v b="$(now)" 'BEGIN { printf "%.1f", b - a }'; }

# ---- databases (always through docker exec: no client programs are needed on the host) ----
db_psql() { $DOCKER exec -i -u postgres bdr-db psql -U postgres -qAt -v ON_ERROR_STOP=1 "$@"; }
rs_psql() { $DOCKER exec -i -u postgres bdr-restore psql -U postgres -qAt -v ON_ERROR_STOP=1 "$@"; }
db_ready()  { [ "$(db_psql -c 'select pg_is_in_recovery()' 2>/dev/null)" = "f" ]; }
rs_ready()  { [ "$(rs_psql -c 'select pg_is_in_recovery()' 2>/dev/null)" = "f" ]; }

# ---- the writer (the business, and the measuring stick) ----
writer_api()   { curl -s -m 5 -X "$1" "$WRITER_URL$2"; }
writer_state() { writer_api GET /state; }
writer_field() { # dotted path into /state, e.g. last_acked_id or recovery.lost_rows
  writer_state | python3 -c 'import json, sys
d = json.load(sys.stdin)
for part in sys.argv[1].split("."):
    d = d.get(part) if isinstance(d, dict) else None
print("" if d is None else d)' "$1"
}

# ---- backups and the WAL archive ----
backup_names() { $DOCKER exec -u postgres bdr-db sh -c 'ls -1 /archive/base 2>/dev/null | sort'; }
backup_count() { backup_names | grep -c . || true; }
wal_count()    { $DOCKER exec -u postgres bdr-db sh -c 'ls -1 /archive/wal 2>/dev/null | grep -c "\.gz$" || true'; }
archive_field() { # last_archived_wal | archived_count | failed_count
  db_psql -c "select coalesce($1::text, '') from pg_stat_archiver"
}
refresh_backups_json() {
  $DOCKER exec -u postgres bdr-db sh -c 'for d in /archive/base/*; do [ -d "$d" ] && echo "$(basename "$d") $(du -sb "$d" | cut -f1)"; done; true' |
    sort | python3 -c 'import json, sys
rows = [line.split() for line in sys.stdin if line.strip()]
print(json.dumps([{"name": r[0], "size": int(r[1])} for r in rows]))' > "$RESULTS_DIR/backups.json.tmp"
  mv "$RESULTS_DIR/backups.json.tmp" "$RESULTS_DIR/backups.json"
  chmod 644 "$RESULTS_DIR/backups.json"
}
backup_take() { # prints the name of the new base backup
  local name
  name="$(date -u +%Y%m%dT%H%M%SZ)"
  while $DOCKER exec bdr-db test -e "/archive/base/$name"; do   # two backups in one second: wait for a new name
    sleep 1; name="$(date -u +%Y%m%dT%H%M%SZ)"
  done
  $DOCKER exec -u postgres bdr-db pg_basebackup -U postgres -D "/archive/base/$name" -Ft -z -X stream \
    --checkpoint=fast -l "$name" >/dev/null || return 1
  refresh_backups_json
  echo "$name"
}
backup_start_wal() { # NAME -> the WAL file the backup starts in (everything from here on must be kept)
  $DOCKER exec -u postgres bdr-db sh -c "tar -xzOf /archive/base/$1/base.tar.gz backup_label | sed -n 's/.*(file \\(.*\\))/\\1/p'"
}
backup_prune() { # KEEP: delete all but the newest KEEP base backups, and the WAL nobody needs any more
  local keep="$1" names oldest_kept start
  names="$(backup_names)"
  local total; total="$(printf '%s\n' "$names" | grep -c . || true)"
  [ "$total" -gt "$keep" ] || return 0
  printf '%s\n' "$names" | head -n "$((total - keep))" | while read -r old; do
    $DOCKER exec -u postgres bdr-db rm -rf "/archive/base/$old"
  done
  oldest_kept="$(backup_names | head -n 1)"
  start="$(backup_start_wal "$oldest_kept")"
  [ -n "$start" ] && $DOCKER exec -u postgres bdr-db pg_archivecleanup -x .gz /archive/wal "$start"
  refresh_backups_json
}
wait_archived() { # SEGMENT-NAME: wait until the archiver has copied that segment (or a later one)
  local seg="$1" last i
  for ((i = 0; i < 60; i++)); do
    last="$(archive_field last_archived_wal)"
    last="${last%.gz}"
    if [ -n "$last" ] && [[ ! "$last" < "$seg" ]]; then return 0; fi
    sleep 1
  done
  return 1
}

# ---- restoring ----
restore_prepare() { # VOLUME BACKUP|latest KIND [VALUE]   fills VOLUME with the backup, ready to replay the archive
  local vol="$1"; shift
  $DOCKER run --rm --user postgres -v "$vol:/var/lib/postgresql/data" -v "$VOL_ARCHIVE:/archive:ro" \
    -v "$ROOT/docker:/docker:ro" "$POSTGRES_IMAGE" bash /docker/restore-prepare.sh "$@"
}
restore_instance() { # KIND [VALUE]: a throw-away database next to production, rebuilt from the archive
  $DC --profile restore rm -sf db-restore >/dev/null 2>&1 || true
  $DOCKER volume rm -f "$VOL_RESTORE" >/dev/null 2>&1 || true
  $DC --profile restore up --no-start db-restore >/dev/null 2>&1 || return 1
  restore_prepare "$VOL_RESTORE" latest "$@" || return 1
  $DC --profile restore up -d db-restore >/dev/null 2>&1 || return 1
  wait_until 180 rs_ready
}
drop_restore_instance() {
  $DC --profile restore rm -sf db-restore >/dev/null 2>&1 || true
  $DOCKER volume rm -f "$VOL_RESTORE" >/dev/null 2>&1 || true
}
rebuild_primary() { # production is gone: build a new one from the latest backup plus the whole archive
  $DC up --no-start db >/dev/null 2>&1 || return 1
  restore_prepare "$VOL_PGDATA" latest latest || return 1
  $DC up -d db >/dev/null 2>&1 || return 1
  wait_until 180 db_ready
}

write_report() { # SCENARIO RTO ROWS_LOST DATA_LOSS_SECONDS NOTE
  python3 - "$RESULTS_DIR/last.json" "$@" <<'PY'
import json, sys, time
path, scenario, rto, lost, secs, note = sys.argv[1:7]
data = {"scenario": scenario, "rto_seconds": float(rto), "at": time.strftime("%H:%M:%S"), "note": note}
if lost != "":
    data["rows_lost"] = int(lost)
    data["data_loss_seconds"] = float(secs) if secs != "" else None
with open(path + ".tmp", "w") as fh:
    json.dump(data, fh)
import os
os.replace(path + ".tmp", path)
os.chmod(path, 0o644)
PY
}

# Conditions for wait_until, re-evaluated on every try (a "$(...)" in wait_until's arguments would run only once).
archive_past() { test "$(archive_field archived_count)" -gt "$1" 2>/dev/null; }   # COUNT
writer_past()  { test "$(writer_field last_acked_id)" -gt "$1" 2>/dev/null; }     # ID
writer_down()  { [ "$(writer_field db_up)" = "False" ]; }
acked_past()   { test "$(writer_field acked_rows)" -gt "$1" 2>/dev/null; }      # ROWS
