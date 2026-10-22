"""Minimal OAuth 2.1 authorization server with Dynamic Client Registration.

Why: a connector URL that carries the secret (``?token=...``) leaks it into
the connector configuration UI, browser history and chat transcripts.  With
OAuth the client authenticates with a short lived access token and the URL
stays clean.

Endpoints (the subset a remote MCP connector needs):

  GET  /.well-known/oauth-authorization-server   discovery document
  POST /register                                 dynamic client registration
  GET  /authorize                                 authorization code (+ consent page)
  POST /token                                     code (PKCE S256) -> access token

Stdlib only: hashlib/secrets/sqlite3.
"""
import base64
import hashlib
import html
import json
import os
import secrets
import sqlite3
import threading
import time
import urllib.parse

from . import logging_setup

LOG = logging_setup.get("minis_bridge.oauth")

_LOCK = threading.Lock()
_DB = None
_CONFIG = None
_TTL = 3600


def _db():
    """A fresh connection per call - sqlite objects must not cross threads."""
    path = _CONFIG.db_file + ".oauth"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _create_tables(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS clients (
                client_id TEXT PRIMARY KEY, client_secret TEXT, name TEXT,
                redirect_uris TEXT, created REAL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS codes (
                code TEXT PRIMARY KEY, client_id TEXT, redirect_uri TEXT,
                challenge TEXT, scope TEXT, created REAL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS tokens (
                token TEXT PRIMARY KEY, client_id TEXT, scope TEXT, expires REAL)""")
    conn.commit()


def init(config):
    global _CONFIG, _TTL
    _CONFIG = config
    _TTL = int(os.environ.get("MINIS_BRIDGE_OAUTH_TTL", "3600"))
    conn = _db()
    try:
        _create_tables(conn)
    finally:
        conn.close()


def _base_url():
    issuer = _CONFIG.oauth_issuer
    if not issuer:
        issuer = "http://127.0.0.1:%d" % _CONFIG.port
    return issuer.rstrip("/")


def _pkce_ok(verifier, challenge):
    if not challenge:
        return True
    if not verifier:
        return False
    digest = hashlib.sha256(verifier.encode()).digest()
    computed = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return secrets.compare_digest(computed, challenge)


def valid_access_token(token):
    if not token:
        return False
    try:
        with _LOCK, _db() as conn:
            row = conn.execute("SELECT expires FROM tokens WHERE token=?", (token,)).fetchone()
    except sqlite3.Error:
        return False
    if not row:
        return False
    return row["expires"] > time.time()


def _mint(client_id, scope):
    token = "mb_" + secrets.token_urlsafe(32)
    with _LOCK, _db() as conn:
        conn.execute("INSERT INTO tokens (token, client_id, scope, expires) VALUES (?,?,?,?)",
                     (token, client_id, scope or "", time.time() + _TTL))
        conn.commit()
    return token


def handle(handler, path, q):
    if path == "/.well-known/oauth-authorization-server":
        base = _base_url()
        return handler._send(200, {
            "issuer": base,
            "authorization_endpoint": base + "/authorize",
            "token_endpoint": base + "/token",
            "registration_endpoint": base + "/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
            "scopes_supported": ["mcp"],
        })
    if path == "/register":
        return _register(handler)
    if path == "/authorize":
        return _authorize(handler, q)
    if path == "/token":
        return _token(handler)
    return handler._send(404, {"error": "not_found"})


def _register(handler):
    body = handler._body()
    name = body.get("client_name") or body.get("name") or "mcp-client"
    uris = body.get("redirect_uris") or []
    if isinstance(uris, str):
        uris = [uris]
    cid = "client_" + secrets.token_urlsafe(16)
    secret = secrets.token_urlsafe(32)
    with _LOCK, _db() as conn:
        conn.execute("INSERT INTO clients (client_id, client_secret, name, redirect_uris, created)"
                     " VALUES (?,?,?,?,?)",
                     (cid, secret, name, json.dumps(uris), time.time()))
        conn.commit()
    handler._send(201, {"client_id": cid, "client_secret": secret, "client_name": name,
                        "redirect_uris": uris,
                        "token_endpoint_auth_method": "client_secret_post",
                        "grant_types": ["authorization_code", "refresh_token"],
                        "response_types": ["code"]})


def _authorize(handler, q):
    client_id = (q.get("client_id", [""])[0] or "")
    redirect = (q.get("redirect_uri", [""])[0] or "")
    state = (q.get("state", [""])[0] or "")
    challenge = (q.get("code_challenge", [""])[0] or "")
    scope = (q.get("scope", ["mcp"])[0] or "mcp")
    with _LOCK, _db() as conn:
        row = conn.execute("SELECT * FROM clients WHERE client_id=?", (client_id,)).fetchone()
    if not row:
        return handler._send(400, {"error": "invalid_client"})
    allowed = json.loads(row["redirect_uris"] or "[]")
    if allowed and redirect not in allowed:
        return handler._send(400, {"error": "invalid_redirect_uri"})

    code = "code_" + secrets.token_urlsafe(24)
    with _LOCK, _db() as conn:
        conn.execute("INSERT INTO codes (code, client_id, redirect_uri, challenge, scope, created)"
                     " VALUES (?,?,?,?,?,?)",
                     (code, client_id, redirect, challenge, scope, time.time()))
        conn.commit()

    target = redirect + ("&" if "?" in redirect else "?") + urllib.parse.urlencode(
        {"code": code, "state": state})
    if not _auto_approve():
        page = ("<!doctype html><meta charset=utf-8><title>Minis bridge authorization</title>"
                "<body style='font-family:system-ui;background:#111;color:#eee;padding:40px'>"
                "<h2>Authorize MCP client</h2><p>Client <b>%s</b> requests access to the "
                "Minis iPhone bridge.</p><p><a href='%s'>Approve</a></p></body>") % (
                    html.escape(str(row["name"])), html.escape(target, quote=True))
        return handler._send(200, page, "text/html; charset=utf-8")
    handler._send(302, b"", "text/html", headers={"Location": target})


def _auto_approve():
    return os.environ.get("MINIS_BRIDGE_OAUTH_AUTO_APPROVE", "1") == "1"


def _token(handler):
    body = handler._body()
    grant = body.get("grant_type", "authorization_code")
    if grant == "authorization_code":
        code = body.get("code", "")
        verifier = body.get("code_verifier", "")
        client_id = body.get("client_id", "")
        redirect = body.get("redirect_uri", "")
        with _LOCK, _db() as conn:
            row = conn.execute("SELECT * FROM codes WHERE code=?", (code,)).fetchone()
            if row:
                conn.execute("DELETE FROM codes WHERE code=?", (code,))
                conn.commit()
        if not row:
            return handler._send(400, {"error": "invalid_grant",
                                        "error_description": "unknown code"})
        if client_id and row["client_id"] != client_id:
            return handler._send(400, {"error": "invalid_grant",
                                        "error_description": "client mismatch"})
        if redirect and row["redirect_uri"] and redirect != row["redirect_uri"]:
            return handler._send(400, {"error": "invalid_grant",
                                        "error_description": "redirect mismatch"})
        if not _pkce_ok(verifier, row["challenge"] or ""):
            return handler._send(400, {"error": "invalid_grant",
                                        "error_description": "PKCE verification failed"})
        token = _mint(row["client_id"], row["scope"])
        return handler._send(200, {"access_token": token, "token_type": "Bearer",
                                   "expires_in": _TTL, "scope": row["scope"]})
    if grant == "refresh_token":
        rt = body.get("refresh_token", "")
        with _LOCK, _db() as conn:
            row = conn.execute("SELECT * FROM tokens WHERE token=?", (rt,)).fetchone()
        if not row:
            return handler._send(400, {"error": "invalid_grant"})
        token = _mint(row["client_id"], row["scope"])
        return handler._send(200, {"access_token": token, "token_type": "Bearer",
                                   "expires_in": _TTL, "scope": row["scope"]})
    return handler._send(400, {"error": "unsupported_grant_type", "grant_type": grant})
