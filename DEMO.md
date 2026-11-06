# Demo: Backup and Disaster Recovery (about 5 minutes)

Open `http://<EC2-PUBLIC-IP>:8081/` and keep it visible. Orders tick up; the archive grows every 10 s.

**1. The idea (30 s).** "A backup is a copy plus the log since the copy. Postgres archives its log continuously, so I can rewind to any moment."

**2. Oops (90 s).**
```bash
./scripts/demo.sh oops
```
Someone deleted every order. A second database is rewound to the second before it, and the lost rows are copied back. Production never stopped. Point at: PASS.

**3. Drill (30 s).**
```bash
./scripts/demo.sh drill
```
"A backup you have not restored is not a backup." It restores the newest state and checks for gaps.

**4. Real disaster (2 min).**
```bash
./scripts/demo.sh crash
```
The database is killed and its disk deleted. Watch the console go red, then recover. Read out the two numbers:
- **RTO**: how long we were down (limit 120 s).
- **RPO**: how many orders we lost (about 10 s at most, because `archive_timeout=10`).

**5. Close (30 s).** "Shorter archive_timeout = less loss. Restore drills = trust. Honest limit: here the archive shares the instance disk; production ships it off-box."

Everything at once: `./scripts/demo.sh gameday`. Done? `./scripts/destroy.sh`, then terminate the instance.
