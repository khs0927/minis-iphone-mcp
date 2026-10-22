"""Command line interface for the Minis iPhone bridge.

    minis-bridge start [--role private|public] [--port N]
    minis-bridge stop | restart | status | url | token [--rotate] | tools | selftest
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

from . import __version__, logging_setup, tools
from .config import CONFIG
from .store import RelayStore

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _pid_file(role):
    return os.path.join(CONFIG.state_dir, "bridge-%s.pid" % role)


def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def _http(url, method="GET", payload=None, token=None, timeout=20):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode()
        return r.status, (json.loads(raw) if raw.strip()[:1] in "{[" else raw)


def cmd_start(args):
    role = args.role
    port = args.port or (8901 if role == "public" else CONFIG.port)
    env = dict(os.environ)
    env["MINIS_BRIDGE_ROLE"] = role
    env["MINIS_BRIDGE_PORT"] = str(port)
    env.setdefault("MINIS_BRIDGE_STATE_DIR", CONFIG.state_dir)
    os.makedirs(CONFIG.state_dir, exist_ok=True)
    log = open(os.path.join(CONFIG.state_dir, "bridge-%s.out" % role), "ab")
    proc = subprocess.Popen([sys.executable, "-m", "minis_bridge.server"],
                            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    with open(_pid_file(role), "w") as f:
        f.write(str(proc.pid))
    for _ in range(30):
        time.sleep(0.4)
        if proc.poll() is not None:
            # The new process died (usually "Address already in use" because an
            # older instance still holds the port).  Do NOT keep its pid in the
            # pid file, or `stop` would lose track of the instance that is
            # really serving the port.
            print("failed to start (exited); see %s" % os.path.join(CONFIG.state_dir, "bridge-%s.out" % role))
            return 1
        try:
            status, body = _http("http://127.0.0.1:%d/health" % port, timeout=3)
            up = float(body.get("uptime_sec", 0)) if isinstance(body, dict) else 0
            if up > 60:
                print("port %d is already served by an older instance (uptime %.0fs)"
                      % (port, up))
                return 1
            print("started: role=%s port=%d pid=%d" % (role, port, proc.pid))
            return 0
        except Exception:
            continue
    print("failed to start; see %s" % os.path.join(CONFIG.state_dir, "bridge-%s.out" % role))
    return 1


def cmd_stop(args):
    stopped = []
    for role in ("private", "public"):
        pf = _pid_file(role)
        try:
            with open(pf) as f:
                pid = f.read().strip()
        except OSError:
            continue
        if pid and _alive(pid):
            try:
                os.kill(int(pid), signal.SIGTERM)
                stopped.append(role)
            except OSError:
                pass
        try:
            os.remove(pf)
        except OSError:
            pass
    # The pid file can go stale (a failed start overwrites it), so also sweep
    # for any bridge server that is still alive.  Without this, an orphaned
    # instance keeps the port and every later start silently fails.
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % d, "rb") as f:
                c = f.read().decode("utf-8", "replace").replace("\x00", " ")
        except OSError:
            continue
        if "minis_bridge.server" in c:
            try:
                os.kill(int(d), signal.SIGTERM)
                stopped.append("orphan %s" % d)
            except OSError:
                pass
    time.sleep(1.0)
    print("stopped: %s" % (", ".join(stopped) or "nothing running"))
    return 0


def cmd_status(args):
    ok = True
    for role, port in (("private", CONFIG.port), ("public", 8901)):
        try:
            status, body = _http("http://127.0.0.1:%d/health" % port, timeout=3)
            print("%-8s UP  http://127.0.0.1:%d  %s" % (role, port, body))
        except Exception:
            print("%-8s DOWN" % role)
            ok = False
    return 0 if ok else 1


def cmd_url(args):
    token = CONFIG.token()
    base = (args.base or "http://127.0.0.1:%d" % CONFIG.port).rstrip("/")
    print("%s/mcp" % base)
    print("  header : Authorization: Bearer %s" % token)
    print("  query  : %s/mcp?token=%s" % (base, token))
    return 0


def cmd_token(args):
    if args.rotate:
        print("new token: %s" % CONFIG.rotate_token())
        print("-> update the connector URL/header, then restart the bridge")
    else:
        print(CONFIG.token())
    return 0


def cmd_tools(args):
    for spec in tools.public_definitions():
        print("%-26s %s" % (spec["name"], spec["description"].split("\n")[0]))
    print("\n%d tools" % len(tools.TOOL_SPECS))
    return 0


def cmd_selftest(args):
    base = (args.url or ("http://127.0.0.1:%d" % CONFIG.port)).rstrip("/")
    token = CONFIG.token()
    failures = []

    def check(label, cond, extra=""):
        print("  [%s] %s %s" % ("PASS" if cond else "FAIL", label, extra))
        if not cond:
            failures.append(label)

    print("== health ==")
    try:
        status, body = _http(base + "/health", timeout=5)
        check("health", status == 200)
    except Exception as e:
        check("health", False, str(e))
        return 1

    print("== auth ==")
    try:
        _http(base + "/tools", timeout=5)
        check("unauthenticated rejected", False, "(accepted!)")
    except urllib.error.HTTPError as e:
        check("unauthenticated rejected", e.code == 401)
    except Exception as e:
        check("unauthenticated rejected", False, str(e))
    try:
        _http(base + "/tools", token=token, timeout=5)
        check("bearer accepted", True)
    except Exception as e:
        check("bearer accepted", False, str(e))
    try:
        _http(base + "/tools?token=" + token, timeout=5)
        check("query token accepted", True)
    except Exception as e:
        check("query token accepted", False, str(e))

    print("== mcp ==")
    try:
        status, body = _http(base + "/mcp", "POST",
                              {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                              token=token, timeout=15)
        check("tools/list", status == 200 and len(body["result"]["tools"]) > 0,
              "(%d tools)" % len(body["result"]["tools"]))
    except Exception as e:
        check("tools/list", False, str(e))
    try:
        status, body = _http(base + "/mcp", "POST",
                              {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                               "params": {"name": "no_such_tool", "arguments": {}}},
                              token=token, timeout=15)
        check("unknown tool errors", "error" in body)
    except Exception as e:
        check("unknown tool errors", False, str(e))
    try:
        status, body = _http(base + "/mcp", "POST",
                              {"jsonrpc": "2.0", "id": 3, "method": "initialize", "params": {}},
                              token=token, timeout=15)
        check("initialize", status == 200 and "result" in body)
    except Exception as e:
        check("initialize", False, str(e))

    print("== relay ==")
    store = RelayStore(CONFIG.db_file)
    try:
        item = store.push("inbox", "selftest", "minis", "claude")
        got = store.read("inbox", 0)
        check("relay push/read", any(i["id"] == item["id"] for i in got["items"]))
    except Exception as e:
        check("relay push/read", False, str(e))

    print("\nRESULT:", "ALL PASS" if not failures else "FAILURES: %s" % ", ".join(failures))
    return 0 if not failures else 1


def main(argv=None):
    logging_setup.setup(CONFIG.log_level)
    ap = argparse.ArgumentParser(prog="minis-bridge")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("start")
    p.add_argument("--role", default="private", choices=["private", "public"])
    p.add_argument("--port", type=int)
    p.set_defaults(func=cmd_start)
    sub.add_parser("stop").set_defaults(func=cmd_stop)
    sub.add_parser("status").set_defaults(func=cmd_status)
    p = sub.add_parser("url"); p.add_argument("--base"); p.set_defaults(func=cmd_url)
    p = sub.add_parser("token"); p.add_argument("--rotate", action="store_true")
    p.set_defaults(func=cmd_token)
    sub.add_parser("tools").set_defaults(func=cmd_tools)
    p = sub.add_parser("selftest"); p.add_argument("--url"); p.set_defaults(func=cmd_selftest)
    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
