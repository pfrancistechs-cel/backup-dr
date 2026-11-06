#!/usr/bin/env python3
"""The business: a shop that records orders into Postgres, about 10 per second, one transaction each.
It is also the measuring stick for disaster recovery. It remembers every order the database ACKNOWLEDGED, so after a
restore it can say exactly how much was lost: how many orders, and how many seconds of business (the real RPO).

Control API (port 9100, published on 127.0.0.1 only):  GET /state   POST /pause   POST /resume   GET /healthz"""
import collections
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg

DB = dict(host=os.environ.get("DB_HOST", "db"), port=int(os.environ.get("DB_PORT", "5432")), user="postgres",
          password=os.environ.get("DB_PASSWORD", ""), dbname="postgres", connect_timeout=2, autocommit=True)
RATE = float(os.environ.get("RATE", "10"))

LOCK = threading.Lock()                       # held while one insert is in flight, so /pause can wait for it
STATE = {"db_up": False, "paused": False, "acked_rows": 0, "last_acked_id": 0, "last_ack_at": None,
         "outage_started": None, "recovery": None, "started_at": time.time()}
ACKS = collections.deque(maxlen=50000)        # (id, wall-clock time of the acknowledgement)


def connect():
    while True:
        try:
            conn = psycopg.connect(**DB)
            conn.execute("CREATE TABLE IF NOT EXISTS orders (id bigint PRIMARY KEY, customer text NOT NULL, "
                         "amount numeric(10,2) NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp())")
            return conn
        except Exception:
            STATE["db_up"] = False
            if STATE["outage_started"] is None:
                STATE["outage_started"] = time.time()
            time.sleep(0.5)


def ack_time(order_id):
    for oid, t in ACKS:
        if oid == order_id:
            return t
    return ACKS[0][1] if ACKS else None


def after_reconnect(conn):
    """The database is back. Compare what we were told was saved with what is really there."""
    row = conn.execute("SELECT coalesce(max(id), 0) FROM orders").fetchone()
    present = int(row[0])
    last = STATE["last_acked_id"]
    outage = time.time() - STATE["outage_started"] if STATE["outage_started"] else 0.0
    if present < last:
        t_last, t_present = ack_time(last), ack_time(present) if present else None
        seconds = round(t_last - t_present, 1) if t_present and t_last else None
        STATE["recovery"] = {"lost_rows": last - present, "data_loss_seconds": seconds,
                             "outage_seconds": round(outage, 1), "restored_last_id": present, "at": time.strftime("%H:%M:%S")}
    else:
        STATE["recovery"] = {"lost_rows": 0, "data_loss_seconds": 0.0, "outage_seconds": round(outage, 1),
                             "restored_last_id": present, "at": time.strftime("%H:%M:%S")}
    STATE["last_acked_id"] = present
    STATE["outage_started"] = None
    return present + 1


def run():
    conn = connect()
    next_id = int(conn.execute("SELECT coalesce(max(id), 0) + 1 FROM orders").fetchone()[0])
    STATE["last_acked_id"] = next_id - 1
    STATE["db_up"] = True
    was_down = False
    while True:
        time.sleep(1.0 / RATE)
        if STATE["paused"]:
            continue
        with LOCK:
            if STATE["paused"]:
                continue
            try:
                if was_down:
                    next_id = after_reconnect(conn)
                    was_down = False
                conn.execute("INSERT INTO orders (id, customer, amount) VALUES (%s, %s, %s)",
                             (next_id, f"customer-{next_id % 97}", round(5 + (next_id % 50) * 1.25, 2)))
            except Exception:
                STATE["db_up"] = False
                if STATE["outage_started"] is None:
                    STATE["outage_started"] = time.time()
                try:
                    conn.close()
                except Exception:
                    pass
                conn = connect()
                STATE["db_up"] = True
                was_down = True
                continue
            now = time.time()
            ACKS.append((next_id, now))
            STATE.update(db_up=True, acked_rows=STATE["acked_rows"] + 1, last_acked_id=next_id, last_ack_at=now)
            next_id += 1


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send_json(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/healthz":
            return self.send_json(200, {"status": "ok"})
        if self.path == "/state":
            s = dict(STATE)
            s["seconds_since_last_ack"] = round(time.time() - s["last_ack_at"], 1) if s["last_ack_at"] else None
            return self.send_json(200, s)
        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path in ("/pause", "/resume"):
            STATE["paused"] = self.path == "/pause"
            if STATE["paused"]:
                with LOCK:                     # wait for an insert that is in flight
                    pass
            return self.send_json(200, {"paused": STATE["paused"], "last_acked_id": STATE["last_acked_id"]})
        self.send_json(404, {"error": "not found"})


def main():
    threading.Thread(target=run, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "9100"))), Handler).serve_forever()


if __name__ == "__main__":
    main()
