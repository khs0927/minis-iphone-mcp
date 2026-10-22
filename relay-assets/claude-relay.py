#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mac-side relay: Minis (iPhone)  <->  Claude Code.

Run this on the machine where Claude Code lives:

    python3 claude-relay.py --url http://192.168.123.115:8900 --token <TOKEN>

It long-polls the bridge inbox; every message Minis sent is handed to
`claude -p` (Claude Code CLI, headless) and the answer is posted back to the
bridge outbox, where Minis reads it with the claude_poll tool.

Options
  --url            bridge base URL      (env MINIS_BRIDGE_URL)
  --token          bearer token         (env MINIS_BRIDGE_TOKEN)
  --config         JSON config file     (env MINIS_BRIDGE_CONFIG, default ~/.minis-bridge.json)
  --claude         path to claude binary (default: claude)
  --model          model passed to `claude -p`
  --cwd            working dir for the claude process
  --system-prompt  extra system prompt for the claude process
  --session        also forward claude's answer to this Minis session id
  --once           process a single batch of pending messages and exit
  --log            log file (default ./claude-relay.log)
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

DEFAULT_SYSTEM = (
    "You are the Claude Code agent paired with a Minis agent running on a real "
    "iPhone. The iPhone is reachable through the 'minis-iphone' MCP connector "
    "(tools: iphone*, minis_session*, bridge_info). Use it to read/write the "
    "user's other Minis conversations, and to control the phone (calendar, "
    "reminders, alarms, HomeKit, HealthKit, photos, location, notifications, "
    "shell). Answer the Minis agent directly and concisely."
)


def load_cfg(args):
    cfg = {}
    path = args.config or os.environ.get("MINIS_BRIDGE_CONFIG") or \
        os.path.expanduser("~/.minis-bridge.json")
    try:
        with open(path) as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    url = args.url or os.environ.get("MINIS_BRIDGE_URL") or cfg.get("url")
    token = args.token or os.environ.get("MINIS_BRIDGE_TOKEN") or cfg.get("token")
    if not url or not token:
        sys.exit("missing --url/--token (or MINIS_BRIDGE_URL / MINIS_BRIDGE_TOKEN)")
    return url.rstrip("/"), token, cfg


def http(url, token, path, method="GET", payload=None, timeout=60):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url + path, data=data, method=method, headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode()
        return json.loads(raw) if raw else {}


def run_claude(prompt, args, system_prompt):
    cmd = [args.claude, "-p", prompt]
    if args.model:
        cmd += ["--model", args.model]
    if system_prompt:
        cmd += ["--append-system-prompt", system_prompt]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=int(args.timeout), cwd=args.cwd)
        out = (p.stdout or "").strip()
        if p.returncode != 0 and not out:
            out = "ERROR: " + (p.stderr or "").strip()[:2000]
        return out
    except subprocess.TimeoutExpired:
        return "ERROR: claude timed out"
    except FileNotFoundError:
        return "ERROR: claude CLI not found at %s" % args.claude


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--token")
    ap.add_argument("--config")
    ap.add_argument("--claude", default="claude")
    ap.add_argument("--model")
    ap.add_argument("--cwd")
    ap.add_argument("--system-prompt", default=DEFAULT_SYSTEM)
    ap.add_argument("--session")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--poll", type=int, default=3)
    ap.add_argument("--log", default="claude-relay.log")
    args = ap.parse_args()

    url, token, cfg = load_cfg(args)
    if args.model is None:
        args.model = cfg.get("model")
    if args.session is None:
        args.session = cfg.get("session")
    logf = open(args.log, "a", buffering=1)

    def log(msg):
        line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
        print(line)
        logf.write(line + "\n")

    log("relay start  url=%s  claude=%s" % (url, args.claude))
    cursor = 0
    while True:
        try:
            data = http(url, token, "/relay/inbox?since=%d" % cursor)
        except Exception as e:
            log("poll error: %s" % e)
            time.sleep(args.poll)
            continue
        items = data.get("items", [])
        cursor = data.get("cursor", cursor)
        for it in items:
            text = it.get("text", "")
            log("-> claude: %s" % text[:120].replace("\n", " "))
            answer = run_claude(text, args, args.system_prompt)
            try:
                http(url, token, "/relay/outbox", "POST",
                     {"text": answer, "reply_to": it.get("id")})
            except Exception as e:
                log("post error: %s" % e)
            if args.session:
                try:
                    http(url, token, "/sessions/%s/send" % args.session, "POST",
                         {"prompt": answer})
                except Exception as e:
                    log("session forward error: %s" % e)
            log("<- answered (%d chars)" % len(answer))
        if args.once and items:
            break
        if not items:
            time.sleep(args.poll)


if __name__ == "__main__":
    main()
