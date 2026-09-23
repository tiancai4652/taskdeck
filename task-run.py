#!/usr/bin/env python3
"""task-run - run a long command in the background, log it, and register it with TaskDeck.

Usage:
  task-run --name NAME --goal GOAL [--max-minutes N] [--beat-seconds N] [--group G] -- COMMAND...

One command = spawn detached + log redirect + stdin closed + registered.
Prints the TaskDeck detail URL on success.
"""
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.request
from urllib.error import URLError

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "server.py")
HOST, PORT = "127.0.0.1", 8747
DATA_DIR = os.path.join(os.path.expanduser("~"), ".taskdeck")
LOG_DIR = os.path.join(DATA_DIR, "logs")

USAGE = """usage: task-run --name NAME --goal GOAL [--max-minutes N] [--beat-seconds N] [--group G] -- COMMAND...
example:
  task-run --name "crawl-reports" --goal "抓取2026中报PDF" --max-minutes 120 -- python crawl.py
"""


def parse_args(argv):
    opts = {"max_minutes": None, "beat_seconds": None, "group": None, "cwd": os.getcwd()}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--":
            return opts, argv[i + 1:]
        if a in ("--name", "--goal", "--group", "--max-minutes", "--beat-seconds", "--cwd"):
            if i + 1 >= len(argv):
                sys.exit("missing value for %s\n%s" % (a, USAGE))
            key = {"--name": "name", "--goal": "goal", "--group": "group",
                   "--max-minutes": "max_minutes", "--beat-seconds": "beat_seconds",
                   "--cwd": "cwd"}[a]
            opts[key] = argv[i + 1]
            i += 2
        else:
            sys.exit("unknown arg: %s\n%s" % (a, USAGE))
    sys.exit("no command given (missing -- separator)\n%s" % USAGE)


def spawn(cmd):
    log_path = os.path.join(LOG_DIR, "%d-%s.log" % (int(time.time()), slug(" ".join(cmd))))
    os.makedirs(LOG_DIR, exist_ok=True)
    lf = open(log_path, "ab")
    kwargs = dict(stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    if os.name == "nt":
        full = ["cmd", "/c", subprocess.list2cmdline(cmd)]
        kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_GROUP
    else:
        full = ["/bin/sh", "-c", " ".join(shlex.quote(c) for c in cmd)]
        kwargs["start_new_session"] = True
    p = subprocess.Popen(full, **kwargs)
    return p.pid, log_path


def slug(s, limit=30):
    out = []
    for c in (s or "task"):
        out.append(c if c.isalnum() else "-")
    return ("".join(out)).strip("-")[:limit] or "task"


def post(path, payload):
    req = urllib.request.Request(
        "http://%s:%d%s" % (HOST, PORT, path),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def ensure_server():
    try:
        with urllib.request.urlopen("http://%s:%d/api/health" % (HOST, PORT), timeout=2) as r:
            if r.status == 200:
                return True
    except Exception:
        pass
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen([sys.executable, SERVER], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, **kwargs)
    except OSError:
        return False
    for _ in range(10):
        time.sleep(0.4)
        try:
            with urllib.request.urlopen("http://%s:%d/api/health" % (HOST, PORT), timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            continue
    return False


def main():
    opts, cmd = parse_args(sys.argv[1:])
    if not opts.get("name") or not opts.get("goal"):
        sys.exit("--name and --goal are required\n%s" % USAGE)
    pid, log_path = spawn(cmd)
    payload = {
        "name": opts["name"],
        "goal": opts["goal"],
        "command": " ".join(cmd),
        "log_path": log_path,
        "pid": pid,
        "cwd": opts.get("cwd") or os.getcwd(),
        "group": opts.get("group") or opts.get("cwd") or os.getcwd(),
        "max_minutes": opts["max_minutes"],
        "beat_seconds": opts["beat_seconds"],
    }
    url = None
    if ensure_server():
        try:
            resp = post("/api/tasks", payload)
            url = resp.get("url")
        except (URLError, OSError) as e:
            print("register failed: %s" % e, file=sys.stderr)
    print("pid:       %s" % pid)
    print("log:       %s" % log_path)
    if url:
        print("dashboard: %s" % url)
    else:
        print("dashboard: http://%s:%d/ (server unavailable, task still running)" % (HOST, PORT))


if __name__ == "__main__":
    main()
