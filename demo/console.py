#!/usr/bin/env python3
"""The recovery console (port 8081): is the database up, is every change reaching the archive, how old is the newest
base backup, and what did the last disaster cost (RTO and RPO)."""
import json
import os
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg

DB = dict(host=os.environ.get("DB_HOST", "db"), port=int(os.environ.get("DB_PORT", "5432")), user="postgres",
          password=os.environ.get("DB_PASSWORD", ""), dbname="postgres", connect_timeout=2, autocommit=True)
WRITER_URL = os.environ.get("WRITER_URL", "http://writer:9100")
RESULTS_DIR = os.environ.get("RESULTS_DIR", "/results")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def database():
    try:
        with psycopg.connect(**DB) as conn:
            rows, last_id = conn.execute("SELECT count(*), coalesce(max(id), 0) FROM orders").fetchone()
            arch = conn.execute("SELECT coalesce(last_archived_wal, ''), archived_count, failed_count, "
                                "extract(epoch FROM now() - last_archived_time) FROM pg_stat_archiver").fetchone()
            timeline = conn.execute("SELECT timeline_id FROM pg_control_checkpoint()").fetchone()[0]
        return {"up": True, "rows": int(rows), "last_id": int(last_id), "timeline": timeline,
                "archive": {"last_wal": arch[0], "archived": arch[1], "failed": arch[2],
                            "seconds_ago": round(float(arch[3]), 1) if arch[3] is not None else None}}
    except Exception:
        try:
            with psycopg.connect(**{**DB, "dbname": "postgres"}) as conn:   # up, but no table yet
                return {"up": True, "rows": 0, "last_id": 0, "timeline": None, "archive": None}
        except Exception:
            return {"up": False}


def writer():
    try:
        with OPENER.open(WRITER_URL + "/state", timeout=2) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def live():
    return {"db": database(), "writer": writer(), "backups": read_json(os.path.join(RESULTS_DIR, "backups.json")) or [],
            "last": read_json(os.path.join(RESULTS_DIR, "last.json")), "now": time.time()}


PAGE = """<!doctype html><meta charset=utf-8><title>Recovery console</title>
<style>
body{font:15px system-ui;margin:24px;max-width:1000px}h2{margin:0 0 4px}.sub{color:#666;margin-bottom:14px}
.row{display:flex;gap:12px;flex-wrap:wrap;margin:10px 0}.card{border:1px solid #ddd;border-radius:8px;padding:10px 14px;min-width:230px;flex:1}
.card b{display:block;font-size:12px;color:#666;text-transform:uppercase;letter-spacing:.04em;margin-bottom:4px}
.big{font-size:26px;font-weight:700}.up{color:#1b7f3b}.down{color:#b00020}.warn{color:#8a6100}
table{border-collapse:collapse;width:100%}td,th{padding:4px 8px;border-bottom:1px solid #eee;text-align:left}
</style>
<h2>Recovery console</h2><div class=sub>Orders are written about 10 times per second. Every change is archived; base backups are taken on demand.</div>
<div class=row id=cards></div>
<h3>Base backups</h3><table id=bk></table>
<h3>Last disaster</h3><div id=last class=card>none yet. Run: ./scripts/demo.sh gameday</div>
<script>
function ago(s){if(s==null)return '-';s=Math.round(s);return s<90?s+' s ago':Math.round(s/60)+' min ago'}
function parseName(n){const m=/^(\\d{4})(\\d\\d)(\\d\\d)T(\\d\\d)(\\d\\d)(\\d\\d)Z$/.exec(n);return m?Date.UTC(+m[1],m[2]-1,+m[3],+m[4],+m[5],+m[6])/1000:null}
function render(d){
  const db=d.db,w=d.writer;let c='';
  c+='<div class=card><b>Database</b><div class="big '+(db.up?'up':'down')+'">'+(db.up?'UP':'DOWN')+'</div>'+(db.up?db.rows+' orders, last id '+db.last_id+(db.timeline?', timeline '+db.timeline:''):'orders are being refused')+'</div>';
  const a=db.archive;
  c+='<div class=card><b>WAL archive (continuous)</b><div class="big '+(a&&a.failed==0?'up':'warn')+'">'+(a?a.archived:'-')+'</div>'+(a?'segments archived, newest '+ago(a.seconds_ago)+'<br>failures: '+a.failed:'no data')+'</div>';
  c+='<div class=card><b>Writer (the business)</b><div class="big '+(w&&w.db_up?'up':'down')+'">'+(w?w.acked_rows:'-')+'</div>'+(w?'orders acknowledged'+(w.paused?' (paused)':'')+', last id '+w.last_acked_id+(w.db_up?'':'<br><span class=down>cannot reach the database</span>'):'writer not reachable')+'</div>';
  document.getElementById('cards').innerHTML=c;
  let t='<tr><th>Backup</th><th>Taken</th><th>Size</th></tr>';
  d.backups.slice().reverse().forEach(b=>{const s=parseName(b.name);t+='<tr><td>'+b.name+'</td><td>'+(s?ago(d.now-s):'')+'</td><td>'+Math.round(b.size/1024)+' KiB</td></tr>'});
  document.getElementById('bk').innerHTML=d.backups.length?t:'<tr><td>none yet: ./scripts/backup.sh take</td></tr>';
  const l=d.last;if(l){let h='<b>'+l.scenario+' at '+l.at+'</b>';
    h+='<div>RTO (time to be serving again): <span class=big>'+l.rto_seconds+' s</span></div>';
    if(l.rows_lost!=null)h+='<div>RPO (data lost): <span class=big>'+l.rows_lost+' orders'+(l.data_loss_seconds!=null?' = '+l.data_loss_seconds+' s of business':'')+'</span></div>';
    if(l.note)h+='<div>'+l.note+'</div>';document.getElementById('last').innerHTML=h}
}
async function tick(){try{render(await (await fetch('/api/live')).json())}catch(e){}}
tick();setInterval(tick,1500);
</script>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send(self, status, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/healthz":
            return self.send(200, b"ok\n", "text/plain")
        if self.path == "/api/live":
            return self.send(200, live())
        if self.path in ("/", "/index.html"):
            return self.send(200, PAGE.encode(), "text/html; charset=utf-8")
        self.send(404, {"error": "not found"})


def main():
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8081"))), Handler).serve_forever()


if __name__ == "__main__":
    main()
