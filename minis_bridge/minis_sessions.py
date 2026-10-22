"""Thin wrapper around the Minis app's session CLI.

``minis-sessions-cli`` is provided by the Minis app (a zero byte shim the app
intercepts).  We only call it with an argument list - never through a shell -
and always with a timeout.
"""
import json
import os
import subprocess

CLI = os.environ.get("MINIS_SESSIONS_CLI", "minis-sessions-cli")

# Fast model for bridge-initiated conversations: the app shares one global pool
# of execution slots between all sessions, so a slow model turns a quick round
# trip into a long queue wait.
DEFAULT_MODEL = os.environ.get(
    "MINIS_BRIDGE_DEFAULT_MODEL",
    "01DCF61E-97A0-4B90-B6B0-1A2107022D2C/deepseek/deepseek-v4.1-flash")


def _run(args, timeout=90):
    try:
        proc = subprocess.run([CLI] + [str(a) for a in args],
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "command": " ".join(args)}
    except OSError as e:
        return {"error": str(e), "command": " ".join(args)}
    out = (proc.stdout or "").strip()
    try:
        return json.loads(out) if out else {}
    except ValueError:
        return {"stdout": out, "stderr": (proc.stderr or "").strip(),
                "exit_code": proc.returncode}


def list_sessions(limit=30):
    return _run(["list", "--limit", int(limit)], timeout=60)


def search(keywords, limit=30):
    return _run(["search", "--keywords", keywords, "--limit", int(limit)], timeout=90)


def messages(session_id, offset=0, full=False, limit=50):
    args = ["messages", "--id", session_id, "--offset", int(offset), "--limit", int(limit)]
    if full:
        args.append("--full")
    return _run(args, timeout=90)


def status(session_id):
    return _run(["status", "--id", session_id], timeout=60)


def send(prompt, session_id=None, model=None):
    """Send a prompt.  The prompt is a POSITIONAL argument of the CLI."""
    args = ["send"]
    if session_id:
        args += ["--session", session_id]
    args += ["--model", model or DEFAULT_MODEL]
    args.append(prompt)
    return _run(args, timeout=120)
