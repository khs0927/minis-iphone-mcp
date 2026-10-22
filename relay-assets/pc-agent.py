#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pc-agent — runs on the user's PC (Windows / macOS / Linux) and executes
commands that the Minis agent (iPhone) sends through the Minis bridge.

The phone pushes a command into the bridge relay box "pc"; this agent
long-polls that box, runs the command locally, and posts the result back
into "pcreply".  It also posts a heartbeat into "pchb" so the phone can see
whether the PC is reachable.

Only stdlib is used, so it runs on a stock Windows Python install.

  python pc-agent.py --url http://192.168.1.10:8900 --token mk-xxxx
  python pc-agent.py --url https://xxx.trycloudflare.com,http://192.168.1.10:8900 --token mk-xxxx

Config file (~/.minis-pc-agent.json) keeps the URL list and token so the
agent can be started without arguments:

  {"urls": ["http://192.168.1.10:8900", "https://xxx.trycloudflare.com"],
   "token": "mk-xxxx"}
"""
import json
import os
import platform
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HOME = os.path.expanduser("~")
CONFIG_PATH = os.path.join(HOME, ".minis-pc-agent.json")
LOG_PATH = os.path.join(HOME, ".minis-pc-agent.log")
STATE_PATH = os.path.join(HOME, ".minis-pc-agent.state")
HEARTBEAT_SEC = 30
POLL_WAIT = 30
IS_WIN = os.name == "nt"

_log_fh = None


def log(msg):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        global _log_fh
        if _log_fh is None:
            _log_fh = open(LOG_PATH, "a", encoding="utf-8", errors="replace")
        _log_fh.write(line + "\n")
        _log_fh.flush()
    except Exception:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass


def load_config():
    cfg = {}
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    ap = argparse_urls()
    if ap.get("urls"):
        cfg["urls"] = ap["urls"]
    if ap.get("token"):
        cfg["token"] = ap["token"]
    return cfg


def argparse_urls():
    out = {"urls": [], "token": ""}
    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--url", "-u") and i + 1 < len(argv):
            out["urls"] = [u.strip() for u in argv[i + 1].split(",") if u.strip()]
            i += 2
            continue
        if a == "--token" and i + 1 < len(argv):
            out["token"] = argv[i + 1]
            i += 2
            continue
        i += 1
    return out


class Bridge:
    """Talks to the Minis bridge; fails over between candidate base URLs."""

    def __init__(self, urls, token):
        self.urls = [u.rstrip("/") for u in urls if u]
        self.token = token
        self.active = self.urls[0] if self.urls else ""
        self.cursor = 0.0

    def _req(self, url, method, path, payload=None, timeout=40):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            url + path, data=data, method=method,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + self.token})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
        return json.loads(raw) if raw.strip()[:1] in "{[" else raw

    def _try_all(self, method, path, payload=None, timeout=40):
        err = None
        order = [self.active] + [u for u in self.urls if u != self.active] if self.active else list(self.urls)
        for u in order:
            if not u:
                continue
            try:
                out = self._req(u, method, path, payload, timeout)
                if self.active != u:
                    log("switched to bridge %s" % u)
                    self.active = u
                return out
            except Exception as e:
                err = e
        raise err if err else RuntimeError("no bridge url")

    def poll(self):
        return self._try_all("GET", "/relay/pc?since=%s&wait=%d" % (self.cursor, POLL_WAIT),
                             timeout=POLL_WAIT + 15)

    def push_reply(self, text, meta):
        return self._try_all("POST", "/relay/pcreply",
                             {"text": text, "from": "pc", "to": "minis", "meta": meta})

    def push_heartbeat(self, meta):
        return self._try_all("POST", "/relay/pchb",
                             {"text": "alive", "from": "pc", "to": "minis", "meta": meta})


def decode_out(raw):
    for enc in ("utf-8", "cp949", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", "replace")


def run_command(cmd, shell="auto", timeout=60):
    """Run cmd on this machine.  Returns (stdout, stderr, exit_code)."""
    if shell in (None, "", "auto"):
        shell = "cmd" if IS_WIN else "sh"
    if shell == "powershell":
        argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd]
        use_shell = False
    elif shell == "cmd":
        if IS_WIN:
            argv = ["cmd", "/c", cmd]
        else:
            argv = ["/bin/sh", "-c", cmd]
        use_shell = False
    elif shell == "sh":
        argv = ["/bin/sh", "-c", cmd]
        use_shell = False
    else:
        argv = cmd
        use_shell = True
    try:
        p = subprocess.run(argv, shell=use_shell, capture_output=True,
                           timeout=timeout)
        return (decode_out(p.stdout or b""), decode_out(p.stderr or b""),
                int(p.returncode))
    except subprocess.TimeoutExpired as e:
        return (decode_out(e.stdout or b""), "TIMEOUT after %ss" % timeout, 124)
    except FileNotFoundError as e:
        return ("", "command not found: %s" % e, 127)
    except Exception as e:
        return ("", "%s: %s" % (type(e).__name__, e), 1)


def heartbeat_meta(bridge):
    try:
        host = socket.gethostname()
    except Exception:
        host = "?"
    return {"kind": "heartbeat", "host": host, "platform": platform.platform(),
            "python": platform.python_version(), "pid": os.getpid(),
            "url": bridge.active, "ts": time.time()}


def main():
    cfg = load_config()
    urls = cfg.get("urls") or []
    token = cfg.get("token") or ""
    if not urls or not token:
        log("config missing: need urls + token in %s" % CONFIG_PATH)
        return 1
    bridge = Bridge(urls, token)
    log("pc-agent starting: urls=%s" % ", ".join(urls))

    # restore the relay cursor so a restart does not re-run old commands
    try:
        with open(STATE_PATH) as f:
            bridge.cursor = float(f.read().strip() or 0)
    except Exception:
        bridge.cursor = 0.0

    last_hb = 0.0
    while True:
        try:
            data = bridge.poll()
        except Exception as e:
            log("poll failed: %s" % e)
            time.sleep(5)
            continue
        items = data.get("items", []) if isinstance(data, dict) else []
        cursor = data.get("cursor", bridge.cursor) if isinstance(data, dict) else bridge.cursor
        for it in items:
            bridge.cursor = max(bridge.cursor, float(it.get("ts", 0) or 0))
            meta = it.get("meta") or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            cmd = (meta.get("command") or it.get("text") or "").strip()
            if not cmd:
                continue
            if meta.get("kind") == "message":
                bridge.push_reply("message received: %s" % cmd,
                                  {"reply_to": it.get("id"), "kind": "message"})
                continue
            log("exec: %s" % cmd[:160])
            t0 = time.time()
            out, err, code = run_command(cmd, meta.get("shell", "auto"),
                                         int(meta.get("timeout", 60)))
            ms = int((time.time() - t0) * 1000)
            log("done rc=%s in %sms" % (code, ms))
            try:
                bridge.push_reply(out[-4000:] or "(no output)", {
                    "reply_to": it.get("id"), "kind": "exec",
                    "stdout": out[-20000:], "stderr": err[-8000:],
                    "exit_code": code, "ms": ms,
                    "host": socket.gethostname(),
                    "platform": platform.platform()})
            except Exception as e:
                log("push reply failed: %s" % e)
        if cursor:
            bridge.cursor = max(bridge.cursor, float(cursor))
        try:
            with open(STATE_PATH, "w") as f:
                f.write(str(bridge.cursor))
        except Exception:
            pass
        now = time.time()
        if now - last_hb > HEARTBEAT_SEC:
            last_hb = now
            try:
                bridge.push_heartbeat(heartbeat_meta(bridge))
            except Exception as e:
                log("heartbeat failed: %s" % e)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("interrupted")
    except Exception as e:
        log("fatal: %s: %s" % (type(e).__name__, e))
        sys.exit(1)
