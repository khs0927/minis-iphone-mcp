"""Serve a file from the bridge state directory.

Used to hand an artifact (a build, a log, a photo export) to a browser based
client - for example to upload it to Google Drive from the in-app WebView.
Only files that live directly in the state directory and match a conservative
name pattern are served; there is no path traversal.
"""
import os
import re

ALLOWED = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
MAX_SIZE = 64 * 1024 * 1024

# The state directory also holds the bearer token and the relay database.
# Those must never be downloadable, not even by an authenticated client.
DENY = {"token", "relay.db", "relay.db.oauth"}
DENY_SUFFIX = (".db", ".oauth", ".key", ".crt", ".cer", ".pem", ".out", ".log")


def _denied(name):
    low = name.lower()
    return low in DENY or low.endswith(DENY_SUFFIX)


def serve_download(handler, name):
    from .config import CONFIG

    if not ALLOWED.match(name or "") or name.startswith("."):
        return handler._send(400, {"error": "invalid file name"})
    if _denied(name):
        return handler._send(403, {"error": "file is not downloadable"})
    path = os.path.join(CONFIG.state_dir, name)
    if not os.path.isfile(path):
        return handler._send(404, {"error": "not found", "name": name})
    try:
        size = os.path.getsize(path)
    except OSError:
        return handler._send(404, {"error": "not found"})
    if size > MAX_SIZE:
        return handler._send(413, {"error": "file too large"})
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        return handler._send(500, {"error": str(e)})
    ctype = "application/octet-stream"
    if name.endswith(".zip"):
        ctype = "application/zip"
    elif name.endswith(".json"):
        ctype = "application/json"
    elif name.endswith(".md"):
        ctype = "text/markdown; charset=utf-8"
    elif name.endswith(".log") or name.endswith(".txt"):
        ctype = "text/plain; charset=utf-8"
    handler._send(200, data, ctype, headers={
        "Content-Disposition": 'attachment; filename="%s"' % name,
        "Cache-Control": "no-store",
    })
