# Project 17: Backup and Disaster Recovery (Postgres PITR, RTO and RPO)

A live Postgres 16 database takes orders all the time. The demo destroys it in three different ways and brings it back each time, then **measures** what the disaster cost:

- **RTO** (Recovery Time Objective): how long until the database serves again.
- **RPO** (Recovery Point Objective): how much business was lost, in seconds of orders.

Everything runs on one EC2 instance. There is no S3 and no IAM role: the "off-site" archive is a Docker volume, which is the honest limit of a one-box demo (see Limits).

## How it works

| Piece | What it does |
|---|---|
| `db` (`postgres:16`) | `archive_mode=on`, `archive_timeout=10`, 1 MB WAL segments. Every finished WAL segment is gzipped into the archive volume. |
| `writer` | Inserts about 10 orders per second, one transaction each. It remembers every order the database acknowledged. After an outage it compares its list with what the database really holds. That gap is the measured RPO. |
| `console` (port 8081) | Live page: orders, archiver status, base backups, and the cost of the last disaster. |
| `db-restore` (profile `restore`) | A SECOND database, started only when needed, that replays a backup plus the archive to a chosen target. |
| `scripts/backup.sh` | `take`, `list`, `prune`. A base backup is `pg_basebackup -Ft -z -X stream`. |

**Point-in-time recovery** = a base backup + every WAL segment after it + a target (`restore point`, a timestamp, or "the end of the archive"). Postgres replays the log and stops at the target.

**Why RPO is about 10 seconds:** the last, unfinished WAL segment is only in the dead database's disk. `archive_timeout=10` forces a segment to close at least every 10 s, so at most about 10 s of orders are unarchived. Set it to 0 and the loss window becomes "until the segment fills", which on a quiet database is hours.

## Scenarios (`./scripts/demo.sh <name>`)

| Scenario | Disaster | Recovery |
|---|---|---|
| `backup` | none | Take a base backup; watch the archive grow by itself. |
| `oops` | Someone runs `DELETE FROM orders` | Rewind a second database to a named restore point just before it, copy the lost rows back into production. Production never stops. |
| `drill` | none | "A backup you have not restored is not a backup": rebuild the newest state in a throw-away database, check no gaps. |
| `retention` | none | Keep the newest 2 base backups, delete WAL nobody needs, prove a restore still works. |
| `crash` | Database killed (`kill -9`) and its disk deleted | Rebuild from backup + archive. Prints RTO and RPO. Limits: RTO 120 s, RPO 20 s. |
| `gameday` | all of the above | Runs them in order. |

## Quick start

See `STEPS.md` (full copy-paste guide, including the AWS console steps) and `DEMO.md` (the 5-minute live script).

```bash
chmod +x scripts/*.sh docker/*.sh
./scripts/setup.sh        # once: apt update/upgrade, Docker
./scripts/preflight.sh    # pulls images, runs a real backup and restore, then cleans up
./scripts/up.sh
./scripts/smoke.sh        # All 13 checks passed.
./scripts/demo.sh gameday
./scripts/destroy.sh
```

Open `http://<EC2-PUBLIC-IP>:8081/` (security group: 22 and 8081 from My IP). The database (5432, 5433) and the writer control port (9100) are bound to `127.0.0.1` only.

## Limits (be honest in the demo)

- The archive volume lives on the same disk as the database volume. `crash` deletes only the database volume. If the instance's disk dies, both are gone. Real systems ship the archive to another place (object storage, another region); tools like pgBackRest or WAL-G do that. The mechanism shown here (base backup + WAL + recovery target) is the same.
- Backups are not encrypted. Add that before storing real data.
- One database, one writer. Replication (a standby for fast failover) is a different tool for a different RTO and is not in this project.

## Tests

`python3 -m unittest discover -s tests -v`. Repo checks always run. The service and pipeline tests start REAL Postgres 16 servers through a stub `docker` and are skipped when the `postgresql-16` package or `psycopg` is missing (as on the demo instance).
