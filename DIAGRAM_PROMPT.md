# Diagram prompt (paste into an AI image model)

Create a clean, wide infrastructure diagram, white background, flat icons, thin labelled arrows, a legend at the bottom. Title: "Backup and Disaster Recovery on one EC2 instance (Postgres PITR)".

Outer box: "EC2 instance m7i-flex.large (Docker Compose)". Inside, left to right:

1. "writer (Python)" at the left. Arrow to the database labelled "about 10 orders/s, one transaction each". A small notebook icon on it: "remembers every acknowledged id". A dashed arrow back from the database labelled "after an outage: compare, report lost rows (measured RPO)".
2. "db: Postgres 16" in the centre, with its own cylinder "Docker volume pgdata". A label: "archive_mode=on, archive_timeout=10 s, 1 MB WAL segments".
3. Below the database, a second cylinder "Docker volume archive" with two folders: "wal/ (gzipped segments)" and "base/ (pg_basebackup tar.gz)". Two arrows from the database into it: "archive_command: every finished segment" (solid, green) and "backup.sh take: base backup" (solid, blue).
4. To the right, a dotted-outline box "db-restore (second Postgres, started on demand)" with its own cylinder "pgdata_restore". An arrow from the archive volume into it labelled "restore_command + recovery target (restore point / time / latest)". Another arrow from it back to the database labelled "copy lost rows back (oops case)".
5. "console :8081" at the top right, fed by arrows from the database and writer. A browser icon outside the box labelled "Your laptop (My IP only)" connects to it with "http://EC2-IP:8081".

A red lightning bolt on the database cylinder labelled "disaster: kill -9 and volume deleted", with a clock bracket from the bolt to the restored database labelled "RTO". A second bracket on the writer's timeline labelled "RPO (at most about 10 s)".

Security group note at the edge: "Inbound: 22 and 8081 from My IP only. 5432, 5433, 9100 bound to 127.0.0.1". Small note: "No S3, no IAM role: archive is a local volume (limit)".
