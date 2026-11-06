"""Test helper: the whole stack as local processes. Real Postgres servers (installed from the Ubuntu package, started by
the stub docker), the real writer and console, and the real scripts. Needs the postgresql-16 server package and psycopg."""
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PGBIN = next((p for p in ("/usr/lib/postgresql/16/bin", "/usr/lib/postgresql/17/bin", "/usr/lib/postgresql/15/bin")
              if os.path.exists(p + "/postgres")), None)
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

try:
    import psycopg  # noqa: F401
    HAVE_PSYCOPG = True
except ImportError:
    HAVE_PSYCOPG = False

AVAILABLE = bool(PGBIN) and HAVE_PSYCOPG and shutil.which("runuser") is not None or (bool(PGBIN) and HAVE_PSYCOPG and os.geteuid() != 0)
SKIP_REASON = "needs the postgresql server package, psycopg and (as root) runuser; the tests run in a sandbox, not on the demo instance"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Stack:
    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="bdr")
        os.chmod(self.tmp, 0o755)
        self.bin = os.path.join(self.tmp, "bin")
        self.results = os.path.join(self.tmp, "results")
        for d in (self.bin, self.results, os.path.join(self.tmp, "archive", "wal"), os.path.join(self.tmp, "archive", "base")):
            os.makedirs(d, exist_ok=True)
        if os.geteuid() == 0:
            for dirpath, _, _ in os.walk(os.path.join(self.tmp, "archive")):
                shutil.chown(dirpath, "postgres", "postgres")
            shutil.chown(self.tmp, "postgres", "postgres")
            os.chmod(self.tmp, 0o755)
        self.ports = {k: free_port() for k in ("primary", "restore", "writer", "console")}
        self.procs = []
        shim = os.path.join(self.bin, "docker")
        with open(shim, "w") as fh:
            fh.write(f'#!/bin/sh\nexec "{sys.executable}" "{os.path.join(ROOT, "tests", "stubdocker.py")}" "$@"\n')
        os.chmod(shim, 0o755)
        self.env = dict(os.environ, PYTHONUNBUFFERED="1", no_proxy="127.0.0.1", NO_PROXY="127.0.0.1",
                        STUB_DIR=self.tmp, STUB_PRIMARY_PORT=str(self.ports["primary"]),
                        STUB_RESTORE_PORT=str(self.ports["restore"]), PGBIN=PGBIN,
                        PATH=self.bin + os.pathsep + os.environ["PATH"])

    @property
    def writer_url(self):
        return f"http://127.0.0.1:{self.ports['writer']}"

    @property
    def console_url(self):
        return f"http://127.0.0.1:{self.ports['console']}"

    def docker(self, *args, **kw):
        return subprocess.run(["docker", *args], env=self.env, capture_output=True, text=True, **kw)

    def psql(self, sql, port=None):
        env = dict(self.env, PGHOST="127.0.0.1", PGPORT=str(port or self.ports["primary"]))
        cmd = [PGBIN + "/psql", "-U", "postgres", "-qAt", "-c", sql]
        if os.geteuid() == 0:
            cmd = ["runuser", "-u", "postgres", "--"] + cmd
        return subprocess.run(cmd, env=env, capture_output=True, text=True).stdout.strip()

    def start_writer_and_console(self):
        common = dict(DB_HOST="127.0.0.1", DB_PORT=str(self.ports["primary"]), DB_PASSWORD="")
        demo, py = os.path.join(ROOT, "demo"), sys.executable
        self.spawn([py, f"{demo}/writer.py"], PORT=str(self.ports["writer"]), **common)
        self.spawn([py, f"{demo}/console.py"], PORT=str(self.ports["console"]), WRITER_URL=self.writer_url,
                   RESULTS_DIR=self.results, **common)

    def spawn(self, cmd, **env):
        self.procs.append(subprocess.Popen(cmd, env={**self.env, **env}, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL, start_new_session=True))

    def start(self, secs=60, with_apps=True):
        self.docker("compose", "up", "-d", "db")                   # initdb + start, like the first start of the real stack
        end = time.time() + secs
        while self.psql("select 1") != "1":
            if time.time() > end:
                raise RuntimeError("postgres did not start")
            time.sleep(0.3)
        if with_apps:
            self.start_writer_and_console()
            for url in (self.writer_url + "/healthz", self.console_url + "/healthz"):
                while True:
                    try:
                        OPENER.open(url, timeout=2).read()
                        break
                    except Exception:
                        if time.time() > end:
                            raise RuntimeError(f"not ready: {url}")
                        time.sleep(0.3)
            while True:                                            # the writer has created the table and written rows
                if self.psql("select count(*) from orders") not in ("", "0") and "ERROR" not in self.psql("select 1"):
                    break
                if time.time() > end:
                    raise RuntimeError("no orders yet")
                time.sleep(0.3)
        return self

    def script_env(self, **extra):
        env = dict(self.env, WRITER_URL=self.writer_url, CONSOLE_URL=self.console_url, RESULTS_DIR=self.results)
        env.update(extra)
        return env

    def stop(self):
        for proc in self.procs:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
        for which in ("primary", "restore"):
            try:
                with open(os.path.join(self.tmp, which + ".pid")) as fh:
                    os.killpg(int(fh.read()), signal.SIGKILL)
            except (OSError, ValueError):
                pass
        time.sleep(0.5)
        shutil.rmtree(self.tmp, ignore_errors=True)
