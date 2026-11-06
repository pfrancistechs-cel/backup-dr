# STEPS: Backup and Disaster Recovery (Postgres PITR, RTO and RPO)

## Prerequisites (set these up BEFORE step 1)

| Need | Details |
|---|---|
| **AWS account** | Any account that can launch EC2. No IAM role, no other AWS service. One region for everything. |
| **Instance type** | `m7i-flex.large` (2 vCPU, 8 GiB). This project needs under 1.5 GiB. |
| **OS image (AMI)** | Ubuntu Server 24.04 LTS (64-bit x86). 22.04 also works. |
| **Disk** | 20 GiB gp3. |
| **Key pair** | An RSA `.pem` key pair created in EC2 (step 0a). |
| **Security group** | Created in step 0b. **Inbound: exactly two rules, both with source My IP: SSH TCP 22; Custom TCP 8081 (recovery console).** **Outbound: leave the default (All traffic, 0.0.0.0/0)**, for Docker Hub and Docker's apt repo. The database (5432, 5433) and the writer API (9100) are bound to localhost and need no rule. |
| **Code on the instance** | Option A: private GitHub repo plus a fine-grained token (Contents: Read) as the `git clone` password. Option B: copy the zip with `scp` (step 2). |
| **Accounts / tokens** | None. |
| **On your laptop** | A terminal with `ssh`/`scp` and a web browser. **No SSH tunnel is needed:** open `http://<PUBLIC-IP>:8081/` directly. |

**Time:** about 8 minutes of setup (preflight pulls two images and does a real backup and restore), about 5 minutes for the full tour. **Cost:** about $0.10/hour at your quoted rate, plus pennies for the disk. Do step 11 when finished.

---

## Step 0: Create the key pair, security group and instance (AWS console)

**0a. Key pair.** EC2 > Network & Security > Key pairs > **Create key pair**. Name `bdr-lab-key`, type **RSA**, format **.pem**. The file downloads once; keep it safe. Lock down its permissions:

```bash
# Linux / macOS
chmod 400 ~/Downloads/bdr-lab-key.pem
```
```powershell
# Windows PowerShell (OpenSSH)
icacls "$HOME\Downloads\bdr-lab-key.pem" /inheritance:r /grant:r "$($env:USERNAME):(R)"
```

**0b. Security group.** EC2 > Network & Security > Security Groups > **Create security group**.
- Name: `bdr-lab-sg`, Description: `SSH and recovery console from my IP`, VPC: the default VPC.
- **Inbound rules > Add rule** (twice; Type `SSH` first, then `Custom TCP`; Protocol `TCP`; **Source `My IP`** both times):
  1. Port range `22` (SSH)
  2. Port range `8081` (recovery console)
- **Outbound rules:** leave the default rule (`All traffic`, `0.0.0.0/0`).
- Click **Create security group**.

> Showing someone on another network? "My IP" blocks them. Share your screen, or add their public IP followed by `/32` to the 8081 rule. Never use `0.0.0.0/0`.

**0c. Launch the instance.** EC2 > Instances > **Launch instances**.
- Name: `bdr-lab-box`
- OS Image: **Ubuntu Server 24.04 LTS**, **64-bit (x86)**
- Instance type: `m7i-flex.large`
- Key pair: `bdr-lab-key`
- Network settings > **Edit**: Auto-assign public IP **Enable**; **Select existing security group** > `bdr-lab-sg`
- Storage: **20 GiB**, `gp3`
- Advanced details > IAM instance profile: **No IAM role**
- **Launch instance**, wait for `Running` and `2/2 passed`, copy the **Public IPv4 address** (`<PUBLIC-IP>` below).

> If your network changes, SSH and the console time out: edit **both** inbound rules and set Source to My IP again. Stop/start also changes the public IP.

## Step 1: Connect (from your laptop)

```bash
ssh -i ~/Downloads/bdr-lab-key.pem ubuntu@<PUBLIC-IP>
```
Type `yes` at the fingerprint prompt. Everything below runs **on the instance**.

## Step 2: Update packages and get the code

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git unzip
```

**Option A (private GitHub repo):**
```bash
git clone https://github.com/<you>/backup-dr.git
cd backup-dr
```
Username = your GitHub username, password = the fine-grained token.

**Option B (no GitHub). From your laptop, in the folder holding the zip:**
```bash
scp -i ~/Downloads/bdr-lab-key.pem backup-dr.zip ubuntu@<PUBLIC-IP>:~
```
Then on the instance:
```bash
unzip backup-dr.zip
cd backup-dr
```

## Step 3: Make the scripts executable (do not skip)

Zipped or uploaded files often lose the executable flag, which gives `Permission denied`.

```bash
chmod +x scripts/*.sh docker/*.sh
ls -l scripts/ docker/        # every .sh line should start with -rwxr-xr-x
```

## Step 4: Install Docker

```bash
./scripts/setup.sh
```
Runs `apt update`/`upgrade`, installs base tools, and installs Docker Engine and Compose v2 from Docker's repo (fallback: Ubuntu's `docker.io`). Takes 3-5 minutes and ends with `Setup complete.` If it says to log out and in, do that, or keep using `sudo` (the scripts do).

## Step 5: Preflight (run well before the demo)

```bash
./scripts/preflight.sh
```
Checks Docker, disk, memory, ports 5432/5433/8081/9100 and Docker Hub; pulls `postgres:16` and `python:3.12-slim`; starts the stack; takes a real base backup; destroys the database and rebuilds it from backup + archive; then cleans up. Every line says `ok` and the last line is `Preflight passed. Backups work and a database can be rebuilt from them.` First run: 6-8 minutes.

## Step 6: Start the stack

```bash
./scripts/up.sh
```
Starts 3 containers (db, writer, console) and prints:

```
Recovery console : http://<PUBLIC-IP>:8081/
```

## Step 7: Health check

```bash
./scripts/smoke.sh
```
Every line says `ok` and it ends with **`All 13 checks passed.`**

## Step 8: Open the console (on your laptop)

`http://<PUBLIC-IP>:8081/`: the order counter climbs about 10 per second, the WAL archive grows every 10 s, and the backups table shows your base backups. After a disaster a red/green panel shows the measured RTO and RPO. **Keep this tab open.**

## Step 9: Run the disaster tour (on the instance)

```bash
./scripts/demo.sh gameday          # all five scenarios: about 4 minutes
```
Or one at a time:
```bash
./scripts/demo.sh backup           # a base backup; the archive grows by itself
./scripts/demo.sh oops             # DELETE FROM orders, then point-in-time recovery into a second database, rows copied back
./scripts/demo.sh drill            # restore the newest state into a throw-away database, check for gaps
./scripts/demo.sh retention        # keep the 2 newest base backups, delete the WAL nobody needs, restore still works
./scripts/demo.sh crash            # kill the database and delete its disk, rebuild, print RTO and RPO
```
Each ends with a green `PASS`. Use it yourself:
```bash
./scripts/backup.sh take           # a base backup now
./scripts/backup.sh list
./scripts/backup.sh prune 2        # keep the newest 2
sudo docker exec bdr-db psql -U postgres -c 'select count(*), max(id) from orders'
```

## Step 10: Unit tests (optional)

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```
On the instance the repo checks run and the real-Postgres tests are skipped (they need `postgresql-16` and `psycopg` installed on the host, which this guide does not install). `OK` at the end.

## Step 11: Clean up, then terminate the instance

```bash
./scripts/destroy.sh
```
Removes the containers, the three volumes (including the archive) and the images. In the AWS console: EC2 > Instances > `bdr-lab-box` > **Instance state > Terminate instance**. Optionally delete `bdr-lab-sg` and `bdr-lab-key`.

---

## What you should see

| Step | Result |
|---|---|
| 4 | Docker and Compose versions, then `Setup complete.` |
| 5 | All `ok`, then `Preflight passed.` |
| 6 | The console URL with your public IP |
| 7 | `All 13 checks passed.` |
| 8 | Live order counter, archive count rising, WAL "last archived" under 15 s ago |
| 9 | Five `PASS` lines, then `ALL SCENARIOS PASSED`. `crash` prints RTO (seconds) and RPO (orders and seconds, about 0-10 s) |
| 10 | `OK` |

## Risky dependencies and fallbacks

| Dependency | Risk | Fallback |
|---|---|---|
| Docker Hub (`postgres:16`, `python:3.12-slim`) | Rate limit or a slow pull | `sudo docker login` with a free account; set `POSTGRES_IMAGE` / `PYTHON_IMAGE` in `.env` and re-run preflight |
| PyPI (during the image build) | `pip install psycopg` fails | Re-run `./scripts/up.sh`; check the outbound rule |
| Named-volume permissions | The restore step runs as the `postgres` user and writes into the volume | `sudo docker compose logs archive-init`; run `./scripts/destroy.sh` then preflight again |
| Same-disk archive | The archive shares the instance disk | Known limit of a one-box demo, explained in README |
| Do not run `destroy.sh` before the demo | It deletes the archive and backups | Re-run preflight afterwards |
| MinIO, LocalStack, S3 | **Not used** | none needed |

## If something goes wrong

| Symptom | Fix |
|---|---|
| SSH: `Connection timed out` | Your IP changed. Edit `bdr-lab-sg`, inbound rule 22, Source My IP. Check the instance is Running with a public IP. |
| Console never loads | (1) Use `http://` and port 8081. (2) The rule needs Source My IP and your current IP. (3) Check the instance's current public IP. (4) On the instance `curl -i http://localhost:8081/healthz` should return `200`; if so, it is the security group. |
| Testing the public IP from the instance hangs | Expected. Use `localhost` on the instance. |
| SSH: `Permission denied (publickey)` | User `ubuntu`, the right `.pem`, `chmod 400`. |
| `./scripts/x.sh: Permission denied` | `chmod +x scripts/*.sh docker/*.sh` (step 3), or `bash scripts/x.sh`. |
| `bad interpreter: /usr/bin/env bash^M` | `sed -i 's/\r$//' scripts/*.sh docker/*.sh` |
| `docker: permission denied` | Use `sudo`, or log out and in after setup. |
| Preflight: `Docker Hub reachable` or a pull fails | Outbound rule must be All traffic; `sudo docker login`; retry. |
| Preflight: `Port NNNN is free` fails | `sudo ss -ltnp \| grep ':8081 '`; stop that process, or `./scripts/destroy.sh` if it is an old run. |
| `up.sh`: the database never accepts connections | `sudo docker logs --tail 30 bdr-db`. |
| `smoke.sh`: `WAL archiving is on` or `archiver has no failures` fails | `sudo docker logs --tail 30 bdr-db` shows archive_command errors, usually the archive volume's permissions: `sudo docker compose logs archive-init`. |
| `smoke.sh`: `all 3 containers are running` fails | `sudo docker compose ps -a`, then `sudo docker compose up -d`. |
| `demo.sh oops`/`drill`: restore database does not come up | `sudo docker logs --tail 40 bdr-restore`. A missing WAL file means the archive was pruned or archiving was off when the backup was taken. Take a new backup. |
| `demo.sh crash`: RPO over 20 s | The writer was paused or the archiver was failing. Check `smoke.sh`. |
| Console shows a red "database down" after a demo was interrupted | `./scripts/up.sh` rebuilds missing pieces; if the database volume is gone, `./scripts/demo.sh crash` restores it from the archive. |
| `demo.sh`: `The stack is not running` | `./scripts/up.sh` |
| Disk full | `sudo docker system prune -af` (images are pulled again by preflight) |
