#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Keeps ONE public Cloudflare tunnel to the Minis bridge alive.

Why: a Claude Code client that runs in the cloud cannot reach 192.168.x.x or
127.0.0.1 on the phone, so it needs a public URL. Quick-tunnel URLs change
whenever cloudflared restarts, so this supervisor:
  1. runs exactly one cloudflared child (http2 - QUIC is blocked here)
  2. continuously watches the log for the newest assigned URL
  3. writes it to public-url.txt and patches mcp-config*.json
  4. raises a local iPhone notification when the URL changes
  5. restarts cloudflared whenever it dies
"""
import json
import os
import re
import subprocess
import time

DIR = "/var/minis/shared/claude-bridge"
LOG = os.path.join(DIR, "tunnel.log")
URLF = os.path.join(DIR, "public-url.txt")
CFGF = os.path.join(DIR, "mcp-config.json")
CFG_SSE = os.path.join(DIR, "mcp-config-sse.json")
CF = "/usr/local/bin/cloudflared"
ORIGIN = "http://127.0.0.1:8900"
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def scan_url():
    """Newest URL that appears in the log (= newest tunnel)."""
    try:
        with open(LOG, "r", errors="replace") as f:
            txt = f.read()
    except Exception:
        return ""
    m = URL_RE.findall(txt)
    return m[-1] if m else ""


def write_configs(url):
    token = open(os.path.join(DIR, "token")).read().strip()
    for path, suffix, typ in ((CFGF, "/mcp", "http"), (CFG_SSE, "/sse", "sse")):
        cfg = {"mcpServers": {"minis-iphone": {
            "type": typ,
            # token embedded in the URL so clients that cannot set custom
            # headers (mobile connector field) still authenticate
            "url": url + suffix + "?token=" + token,
            "headers": {"Authorization": "Bearer " + token}}}}
        with open(path, "w") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    with open(URLF, "w") as f:
        f.write(url + "\n")


def notify(url):
    try:
        subprocess.run(["/usr/local/bin/apple-notification", "schedule",
                        "--title", "미니스 브리지",
                        "--body", "새 공개 URL: " + url,
                        "--after", "3"],
                       capture_output=True, text=True, timeout=20)
    except Exception:
        pass


def kill_stray_cloudflared():
    """Kill cloudflared processes that are not our child (iSH has no pkill)."""
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % pid, "rb") as f:
                cmd = f.read().decode("utf-8", "replace").replace("\x00", " ")
        except Exception:
            continue
        if "cloudflared" in cmd and "tunnel" in cmd:
            try:
                os.kill(int(pid), 15)
            except Exception:
                pass


def main():
    kill_stray_cloudflared()
    time.sleep(1)
    child = None
    last = ""
    while True:
        url = scan_url()
        if url and url != last:
            write_configs(url)
            notify(url)
            last = url
            print("tunnel url:", url, flush=True)
        if child is None or child.poll() is not None:
            if child is not None:
                print("cloudflared exited -> restarting", flush=True)
                time.sleep(3)
            with open(LOG, "ab") as log:
                child = subprocess.Popen(
                    [CF, "tunnel", "--protocol", "http2", "--url", ORIGIN,
                     "--no-autoupdate"],
                    stdout=log, stderr=subprocess.STDOUT)
            print("cloudflared started pid", child.pid, flush=True)
        time.sleep(3)


if __name__ == "__main__":
    main()
