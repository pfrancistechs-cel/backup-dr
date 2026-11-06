#!/usr/bin/env python3
"""A stand-in for `docker`, for tests only. It maps the few docker commands the scripts use onto LOCAL Postgres servers:

  bdr-db       -> a local server on STUB_PRIMARY_PORT, data in $STUB_DIR/primary_data
  bdr-restore  -> a local server on STUB_RESTORE_PORT, data in $STUB_DIR/restore_data
  /archive     -> $STUB_DIR/archive

The postgres settings (archive_command and friends) are read from the real docker-compose.yml, so the real command
strings are what gets tested. Understands: info, compose (up/ps/down/rm/stop), exec, run, rm, volume rm, ps, pull."""
import os
import re
import shutil
import signal
import subprocess
import sys
import time

STUB = os.environ.get("STUB_DIR", "")
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PGBIN = os.environ.get("PGBIN", "/usr/lib/postgresql/16/bin")
PRIMARY_PORT = os.environ.get("STUB_PRIMARY_PORT", "15432")
RESTORE_PORT = os.environ.get("STUB_RESTORE_PORT", "15433")
ARCHIVE = os.path.join(STUB, "archive")
DIRS = {"primary": os.path.join(STUB, "primary_data"), "restore": os.path.join(STUB, "restore_data")}
VOLUMES = {"backupdr_pgdata": DIRS["primary"], "backupdr_pgdata_restore": DIRS["restore"], "backupdr_archive": ARCHIVE}
PORTS = {"primary": PRIMARY_PORT, "restore": RESTORE_PORT}


def loc(text):
    return text.replace("/archive", ARCHIVE)


def as_postgres(cmd):
    return ["runuser", "-u", "postgres", "--"] + cmd if os.geteuid() == 0 else cmd


def pg_env(extra=None):
    env = dict(os.environ, PATH=PGBIN + os.pathsep + os.environ["PATH"])
    env.update(extra or {})
    return env


def ensure_dir(path, mode=0o700):
    os.makedirs(path, exist_ok=True)
    os.chmod(path, mode)
    if os.geteuid() == 0:
        shutil.chown(path, "postgres", "postgres")


def pid_file(which):
    return os.path.join(STUB, which + ".pid")


def alive(which):
    try:
        with open(pid_file(which)) as fh:
            os.kill(int(fh.read()), 0)
        return True
    except (OSError, ValueError):
        return False


def settings(which):
    if which == "restore":
        return {"archive_mode": "off"}
    with open(os.path.join(REPO, "docker-compose.yml"), encoding="utf-8") as fh:
        text = fh.read()
    block = text[text.index("\n  db:"):text.index("\n  writer:")]
    found = re.findall(r"^      - '?([a-z_]+)=(.*?)'?$", block, re.M)
    return {k: loc(v) for k, v in found}


def start(which):
    if alive(which):
        return
    data = DIRS[which]
    ensure_dir(data)
    if not os.path.exists(os.path.join(data, "PG_VERSION")):
        subprocess.run(as_postgres([PGBIN + "/initdb", "-D", data, "--wal-segsize=1", "-A", "trust", "-U", "postgres"]),
                       env=pg_env(), check=True, stdout=subprocess.DEVNULL)
    cmd = [PGBIN + "/postgres", "-D", data, "-c", f"port={PORTS[which]}", "-c", "listen_addresses=127.0.0.1",
           "-c", f"unix_socket_directories={STUB}"]
    for key, value in settings(which).items():
        cmd += ["-c", f"{key}={value}"]
    log = open(os.path.join(STUB, which + ".log"), "ab")
    proc = subprocess.Popen(as_postgres(cmd), env=pg_env(), stdout=log, stderr=log, start_new_session=True)
    with open(pid_file(which), "w") as fh:
        fh.write(str(proc.pid))


def stop(which, sig):
    try:
        with open(pid_file(which)) as fh:
            pid = int(fh.read())
        os.killpg(pid, sig)
    except (OSError, ValueError):
        return
    for _ in range(100):                       # wait for it to be gone
        try:
            os.kill(pid, 0)
            time.sleep(0.1)
        except OSError:
            break
    try:
        os.remove(pid_file(which))
    except OSError:
        pass


def cmd_compose(argv):
    args = list(argv)
    while "--profile" in args:
        i = args.index("--profile")
        del args[i:i + 2]
    if not args:
        return 0
    sub, rest = args[0], args[1:]
    services = [a for a in rest if not a.startswith("-")]
    if sub == "up":
        for svc in services or ["db"]:
            which = {"db": "primary", "db-restore": "restore"}.get(svc)
            if which:
                ensure_dir(DIRS[which])
                if "--no-start" not in rest:
                    start(which)
        return 0
    if sub == "ps":
        if "-q" in rest:
            print("c1\nc2\nc3")
        return 0
    if sub in ("rm", "stop"):
        if "db-restore" in services:
            stop("restore", signal.SIGTERM)
        if "db" in services:
            stop("primary", signal.SIGTERM)
        return 0
    if sub == "down":
        stop("restore", signal.SIGTERM)
        stop("primary", signal.SIGTERM)
        return 0
    return 0


def cmd_exec(argv):
    args = list(argv)
    user = None
    while args and args[0].startswith("-"):
        flag = args.pop(0)
        if flag == "-u":
            user = args.pop(0)
    container, cmd = args[0], [loc(a) for a in args[1:]]
    which = {"bdr-db": "primary", "bdr-restore": "restore"}.get(container)
    if which is None or not alive(which):
        sys.stderr.write(f"Error response from daemon: No such container: {container}\n")
        return 1
    env = pg_env({"PGHOST": "127.0.0.1", "PGPORT": PORTS[which]})
    return subprocess.run(as_postgres(cmd) if user == "postgres" else cmd, env=env).returncode


def cmd_run(argv):
    args = list(argv)
    mounts, env_extra = {}, {}
    while args and args[0].startswith("-"):
        flag = args.pop(0)
        if flag in ("--user", "-u"):
            args.pop(0)
        elif flag == "-v":
            vol, path = args.pop(0).split(":")[:2]
            if vol in VOLUMES:
                mounts[path] = VOLUMES[vol]
        elif flag == "-e":
            key, _, value = args.pop(0).partition("=")
            env_extra[key] = value
    args.pop(0)                                  # the image
    script = args[1]
    if script.startswith("/docker/"):
        script = os.path.join(REPO, "docker", script[len("/docker/"):])
    for path in mounts.values():
        if not os.path.isdir(path):
            ensure_dir(path)
    env = pg_env({"ARCHIVE_DIR": mounts.get("/archive", ARCHIVE), "PGDATA_DIR": mounts.get("/var/lib/postgresql/data", ""),
                  **env_extra})
    return subprocess.run(as_postgres([args[0], script] + args[2:]), env=env).returncode


def main(argv):
    cmd = argv[0] if argv else ""
    rest = argv[1:]
    if cmd == "compose":
        return cmd_compose(rest)
    if cmd == "exec":
        return cmd_exec(rest)
    if cmd == "run":
        return cmd_run(rest)
    if cmd == "rm":
        for name in [a for a in rest if not a.startswith("-")]:
            if name == "bdr-db":
                stop("primary", signal.SIGKILL)            # a crash: no clean shutdown
            elif name == "bdr-restore":
                stop("restore", signal.SIGKILL)
        return 0
    if cmd == "volume" and rest[:1] == ["rm"]:
        for name in [a for a in rest[1:] if not a.startswith("-")]:
            if name in VOLUMES and name != "backupdr_archive":
                shutil.rmtree(VOLUMES[name], ignore_errors=True)
        return 0
    return 0                                                # info, ps, pull, rmi, image ...


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
