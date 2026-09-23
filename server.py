#!/usr/bin/env python3
"""TaskDeck - background task monitor service (stdlib only, macOS/Linux/Windows).

Binds 127.0.0.1 only. Data in ~/.taskdeck/.
API:
  POST /api/tasks            register a task {name, goal, command, log_path, pid?, cwd?, max_minutes?, beat_seconds?}
  GET  /api/tasks            overview data (grouped by client)
  GET  /api/tasks/<id>       task detail
  GET  /api/tasks/<id>/log?offset=N   incremental log
  POST /api/tasks/<id>/kill  terminate process
  GET  /api/health           liveness probe
  GET  /                     overview page
  GET  /task/<id>            detail page
"""
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HOST = "127.0.0.1"
PORT = 8747
DATA_DIR = os.path.join(os.path.expanduser("~"), ".taskdeck")
LOG_DIR = os.path.join(DATA_DIR, "logs")
STORE_PATH = os.path.join(DATA_DIR, "tasks.json")
FRONTEND_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend.html")
POLL_INTERVAL = 5
DEFAULT_BEAT = 300
SPIN_CPU = 25.0
ERROR_PATTERNS = (b"Traceback (most recent call last)", b"SyntaxError", b"panic:", b"Segmentation fault")

_lock = threading.Lock()


def pid_alive(pid):
    if pid is None:
        return None
    try:
        if os.name == "nt":
            import ctypes
            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            k.CloseHandle(h)
            return True
        os.kill(int(pid), 0)
        return True
    except (OSError, ProcessLookupError, PermissionError, ValueError):
        return False


def cpu_percent(pid):
    if pid is None or os.name == "nt":
        return None
    try:
        out = subprocess.run(
            ["ps", "-o", "%cpu=", "-p", str(pid)],
            capture_output=True, text=True, timeout=3,
        )
        return float(out.stdout.strip())
    except Exception:
        return None


def read_tail(path, nbytes=4096):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - nbytes))
            return f.read()
    except OSError:
        return b""


def looks_failed(path):
    tail = read_tail(path)
    return any(p in tail for p in ERROR_PATTERNS)


def slugify(name, fallback="task"):
    s = re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5]+", "-", (name or "").strip()).strip("-")
    return (s or fallback)[:40]


class Store:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.tasks = {}
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.tasks = json.load(f)
        except (OSError, ValueError):
            self.tasks = {}

    def save(self):
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.tasks, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def add(self, meta):
        tid = uuid.uuid4().hex[:10]
        now = time.time()
        task = {
            "id": tid,
            "name": meta.get("name") or "unnamed",
            "goal": meta.get("goal") or "",
            "command": meta.get("command") or "",
            "group": meta.get("group") or meta.get("cwd") or "default",
            "cwd": meta.get("cwd") or "",
            "pid": meta.get("pid"),
            "log_path": meta.get("log_path") or "",
            "max_minutes": meta.get("max_minutes"),
            "beat_seconds": int(meta.get("beat_seconds") or DEFAULT_BEAT),
            "status": "running",
            "exit_code": None,
            "last_beat": now,
            "log_size": 0,
            "cpu": None,
            "created_at": now,
            "ended_at": None,
            "overdue": False,
        }
        with self.lock:
            try:
                task["log_size"] = os.path.getsize(task["log_path"]) if task["log_path"] and os.path.exists(task["log_path"]) else 0
            except OSError:
                pass
            self.tasks[tid] = task
            self.save()
        return task

    def get(self, tid):
        with self.lock:
            return self.tasks.get(tid)

    def all(self):
        with self.lock:
            return list(self.tasks.values())


STORE = None


def poll_once():
    now = time.time()
    changed = False
    for t in STORE.all():
        if t["status"] in ("exited", "failed", "killed"):
            continue
        log_path = t.get("log_path") or ""
        try:
            size = os.path.getsize(log_path) if log_path and os.path.exists(log_path) else 0
        except OSError:
            size = 0
        if size > t.get("log_size", 0):
            t["last_beat"] = now
        t["log_size"] = size

        alive = pid_alive(t.get("pid")) if t.get("pid") else None
        if alive is False:
            t["ended_at"] = now
            t["status"] = "failed" if looks_failed(log_path) else "exited"
            changed = True
            continue

        stalled = (now - t.get("last_beat", now)) > t["beat_seconds"]
        cpu = cpu_percent(t.get("pid")) if alive else None
        t["cpu"] = cpu
        if stalled:
            t["status"] = "spinning" if (cpu is not None and cpu >= SPIN_CPU) else "hang"
        else:
            t["status"] = "running"
        mm = t.get("max_minutes")
        t["overdue"] = bool(mm and (now - t["created_at"]) > mm * 60)
        changed = True
    if changed:
        STORE.save()


def poller():
    while True:
        try:
            poll_once()
        except Exception:
            pass
        time.sleep(POLL_INTERVAL)


def kill_task(t):
    pid = t.get("pid")
    if not pid:
        return False, "no pid recorded (log-only task)"
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=10)
        else:
            os.kill(int(pid), signal.SIGTERM)

        def finisher():
            time.sleep(6)
            if pid_alive(pid):
                try:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                       capture_output=True, timeout=10)
                    else:
                        os.kill(int(pid), signal.SIGKILL)
                except OSError:
                    pass
        threading.Thread(target=finisher, daemon=True).start()
    except (OSError, ProcessLookupError):
        pass
    with STORE.lock:
        t["status"] = "killed"
        t["ended_at"] = time.time()
        STORE.save()
    return True, "killed"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wheaders_done = True
        self.wfile.write(data)

    def _read_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            return json.loads(raw.decode("utf-8"))
        except (ValueError, OSError):
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        try:
            if u.path == "/api/health":
                return self._send(200, {"ok": True, "time": time.time()})
            if u.path == "/api/tasks":
                tasks = STORE.all()
                tasks.sort(key=lambda t: t["created_at"], reverse=True)
                return self._send(200, {"now": time.time(), "tasks": tasks})
            if len(parts) == 2 and parts[0] == "api" and parts[1] == "tasks":
                return self._send(404, {"error": "missing task id"})
            if len(parts) == 3 and parts[0] == "api" and parts[1] == "tasks":
                t = STORE.get(parts[2])
                if not t:
                    return self._send(404, {"error": "not found"})
                return self._send(200, {"now": time.time(), "task": t})
            if len(parts) == 4 and parts[0] == "api" and parts[1] == "tasks" and parts[3] == "log":
                t = STORE.get(parts[2])
                if not t:
                    return self._send(404, {"error": "not found"})
                q = parse_qs(u.query)
                try:
                    offset = max(0, int(q.get("offset", ["0"])[0]))
                except ValueError:
                    offset = 0
                path = t.get("log_path") or ""
                if not path or not os.path.exists(path):
                    return self._send(200, {"size": 0, "offset": 0, "chunk": ""})
                size = os.path.getsize(path)
                if offset > size:
                    offset = 0
                with open(path, "rb") as f:
                    f.seek(offset)
                    chunk = f.read(min(size - offset, 262144))
                return self._send(200, {
                    "size": size,
                    "offset": offset + len(chunk),
                    "chunk": chunk.decode("utf-8", errors="replace"),
                })
            if u.path == "/favicon.svg":
                svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
                       '<rect width="32" height="32" rx="7" fill="#0F172A"/>'
                       '<circle cx="16" cy="16" r="7" fill="#22C55E"/></svg>')
                return self._send(200, svg.encode(), "image/svg+xml")
            if u.path == "/" or (len(parts) == 2 and parts[0] == "task"):
                try:
                    with open(FRONTEND_PATH, "rb") as f:
                        html = f.read()
                except OSError:
                    return self._send(500, {"error": "frontend.html missing"})
                return self._send(200, html, "text/html; charset=utf-8")
            return self._send(404, {"error": "not found"})
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        try:
            if u.path == "/api/tasks":
                meta = self._read_json()
                if not meta.get("name") or not (meta.get("log_path") or meta.get("command")):
                    return self._send(400, {"error": "name and (log_path or command) required"})
                t = STORE.add(meta)
                return self._send(200, {"task": t, "url": "http://%s:%d/task/%s" % (HOST, PORT, t["id"])})
            if len(parts) == 4 and parts[0] == "api" and parts[1] == "tasks" and parts[3] == "kill":
                t = STORE.get(parts[2])
                if not t:
                    return self._send(404, {"error": "not found"})
                ok, msg = kill_task(t)
                return self._send(200, {"ok": ok, "message": msg, "task": t})
            return self._send(404, {"error": "not found"})
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    global STORE
    os.makedirs(LOG_DIR, exist_ok=True)
    STORE = Store(STORE_PATH)
    threading.Thread(target=poller, daemon=True).start()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print("TaskDeck listening on http://%s:%d" % (HOST, PORT), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
