"""HTTP / MCP transport for the Minis iPhone bridge.

===========================  ==========================================
POST /mcp                   MCP JSON-RPC (Streamable HTTP) - the modern
                             transport, works from every client.
GET  /mcp                   server->client SSE stream (private clients).
GET  /sse + POST /messages  legacy HTTP+SSE transport (private clients).
GET  /health                liveness probe (no auth).
GET  /metrics               counters in Prometheus text format.
GET  /tools                 tool catalogue (auth).
REST facade                 /sessions, /relay/*, /iphone, /shell.
===========================  ==========================================

The server is *stateless*: it does not hand out an ``Mcp-Session-Id``, so a
client never has to echo one back (the MCP spec allows both, and stateless is
the simplest correct option).
"""
import json
import os
import queue
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import apple, logging_setup, minis_sessions, oauth, policy, tools
from .download import serve_download
from .config import CONFIG
from .store import RelayStore

LOG = logging_setup.get("minis_bridge.server")
START_TIME = time.time()
SSE_SESSIONS = {}
STORE = None

METRICS = {"requests_total": 0, "tools_calls_total": 0, "tools_errors_total": 0,
           "auth_failures_total": 0, "rate_limited_total": 0,
           "scope_public_total": 0, "scope_private_total": 0}
_BY_PATH = {}
_METRICS_LOCK = threading.Lock()
_RATE = {}
_RATE_LOCK = threading.Lock()


def _bump(name, **labels):
    with _METRICS_LOCK:
        if labels:
            key = name + "{" + ",".join("%s=%s" % (k, v) for k, v in sorted(labels.items())) + "}"
            _BY_PATH[key] = _BY_PATH.get(key, 0) + 1
        else:
            METRICS[name] = METRICS.get(name, 0) + 1


def rate_limited(client_ip, limit):
    """Fixed window limiter (per client address, per minute)."""
    if not limit:
        return False
    now = int(time.time() // 60)
    with _RATE_LOCK:
        cur = _RATE.get(client_ip)
        if cur is None or cur[0] != now:
            _RATE[client_ip] = [now, 1]
            return False
        cur[1] += 1
        return cur[1] > limit


class Handler(BaseHTTPRequestHandler):
    server_version = "minis-bridge/2.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # never print the raw request line: it can contain the token
        return

    def _log(self, msg, **fields):
        fields.setdefault("client", self.client_address[0])
        logging_setup.event(LOG, msg, **fields)

    # ------------------------------------------------------------------ #
    def _scope(self):
        return policy.classify(
            peer_ip=self.client_address[0],
            host=self.headers.get("Host", ""),
            role=os.environ.get("MINIS_BRIDGE_ROLE", "private"),
            public_hosts=CONFIG.public_hosts,
            tunnel_suffix=CONFIG.tunnel_suffix)

    def _auth(self, q=None):
        """Bearer token in the header, in ?token= or in X-Min-Token."""
        h = (self.headers.get("Authorization") or "").strip()
        if h.lower().startswith("bearer "):
            cand = h[7:].strip()
            if cand and (cand == CONFIG.token() or oauth.valid_access_token(cand)):
                return True
        alt = (self.headers.get("X-Min-Token") or "").strip()
        if alt and (alt == CONFIG.token() or oauth.valid_access_token(alt)):
            return True
        if q:
            cand = (q.get("token", [""])[0] or "").strip()
            if cand and (cand == CONFIG.token() or oauth.valid_access_token(cand)):
                return True
        return False

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            n = 0
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {"_raw": raw.decode("utf-8", "replace")[:2000]}

    def _send(self, code, body, ctype="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, indent=1).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers",
                         "Authorization, Content-Type, Mcp-Session-Id, X-Min-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    # ------------------------------------------------------------------ #
    def _rpc(self, req, scope):
        method = req.get("method", "")
        rid = req.get("id")
        params = req.get("params") or {}

        if method.startswith("notifications/"):
            return None

        if method == "initialize":
            from . import __version__
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": CONFIG.protocol_version,
                "capabilities": {"tools": {"listChanged": False}, "logging": {}},
                "serverInfo": {"name": CONFIG.server_name, "version": __version__},
                "instructions": ("Controls a real iPhone (iOS frameworks) and the Minis app. "
                                 "Use iphone_help for native tool syntax, minis_sessions_* to "
                                 "read/write Minis conversations, claude_send/claude_poll to "
                                 "exchange messages with a Claude agent.")}}

        if method == "ping":
            return {"jsonrpc": "2.0", "id": rid, "result": {}}

        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": tools.public_definitions()}}

        if method in ("resources/list", "prompts/list"):
            key = "resources" if "resources" in method else "prompts"
            return {"jsonrpc": "2.0", "id": rid, "result": {key: []}}

        if method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments") or {}
            spec = tools.TOOLS.get(name)
            if not spec:
                return {"jsonrpc": "2.0", "id": rid, "error": {
                    "code": -32602, "message": "unknown tool: %s" % name}}
            allowed, why = policy.tool_allowed(
                name, scope, CONFIG.private_only_tools, CONFIG.allow_public_shell)
            if not allowed:
                _bump("tools_errors_total")
                return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32001, "message": why}}
            try:
                out = spec[3](args)
                text = json.dumps(out, ensure_ascii=False, indent=1)
                if len(text) > CONFIG.max_tool_output:
                    text = text[:CONFIG.max_tool_output] + "\n...[truncated]"
                is_err = isinstance(out, dict) and bool(out.get("error"))
            except Exception as e:  # a tool must never kill the server
                _bump("tools_errors_total")
                self._log("tool failed", tool=name, error=str(e)[:200])
                text = "tool error: %s: %s" % (type(e).__name__, e)
                is_err = True
            _bump("tools_calls_total")
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": text}], "isError": bool(is_err)}}

        return {"jsonrpc": "2.0", "id": rid, "error": {
            "code": -32601, "message": "method not found: %s" % method}}

    def _mcp_post(self, scope):
        payload = self._body()
        items = payload if isinstance(payload, list) else [payload]
        out = []
        for req in items:
            if not isinstance(req, dict):
                continue
            res = self._rpc(req, scope)
            if res is not None:
                out.append(res)
        if not out:
            self._send(202, b"", "application/json")
            return
        self._send(200, out[0] if len(out) == 1 else out, "application/json")

    # ------------------------------------------------------------------ #
    def do_OPTIONS(self):
        self._send(204, b"", "text/plain")

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = u.path.rstrip("/") or "/"
        _bump("requests_total", path=p, method="GET")

        if p == "/health":
            from . import __version__
            self._send(200, {"ok": True, "server": CONFIG.server_name, "version": __version__,
                             "mcp": "/mcp", "uptime_sec": round(time.time() - START_TIME, 1),
                             "role": os.environ.get("MINIS_BRIDGE_ROLE", "private")})
            return

        if p == "/metrics":
            self._send(200, _prometheus(), "text/plain; version=0.0.4")
            return

        if p.startswith("/.well-known/") or p in ("/authorize", "/register", "/token"):
            oauth.handle(self, p, q)
            return

        if not self._auth(q):
            _bump("auth_failures_total")
            self._log("unauthorized", path=p)
            self._send(401, {"error": "unauthorized",
                             "hint": "Authorization: Bearer <token> (or ?token=...)"})
            return

        scope = self._scope()
        _bump("scope_public_total" if scope == policy.SCOPE_PUBLIC else "scope_private_total")
        self._log("request", path=p, scope=scope)

        if p.startswith("/download/"):
            # Lets the Mac fetch the relay/bootstrap helpers with one curl,
            # instead of the user having to copy files off the phone by hand.
            name = p.split("/download/", 1)[1]
            allowed = {"claude-relay.py", "mac-tunnel.sh"}
            base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            fp = os.path.join(base, "relay-assets", name)
            if name not in allowed or not os.path.isfile(fp):
                self._send(404, {"error": "not found", "allowed": sorted(allowed)})
                return
            with open(fp, "rb") as f:
                data = f.read()
            self._send(200, data, ctype="text/plain; charset=utf-8")
            return
        if p == "/tools":
            self._send(200, {"count": len(tools.TOOL_SPECS), "tools": tools.public_definitions()})
            return
        if p.startswith("/download/"):
            serve_download(self, p[len("/download/"):])
            return
        if p == "/tools/help":
            try:
                self._send(200, apple.help_text(q.get("tool", [""])[0]))
            except apple.NativeError as e:
                self._send(400, {"error": str(e)})
            return
        if p == "/sessions":
            self._send(200, minis_sessions.list_sessions(int(q.get("limit", ["30"])[0])))
            return
        if p.startswith("/sessions/") and p.endswith("/messages"):
            self._send(200, minis_sessions.messages(
                p.split("/")[2], int(q.get("offset", ["0"])[0]),
                q.get("full", ["false"])[0] == "true"))
            return
        if p.startswith("/sessions/") and p.endswith("/status"):
            self._send(200, minis_sessions.status(p.split("/")[2]))
            return
        if p == "/relay/inbox":
            self._send(200, STORE.read("inbox", q.get("since", ["0"])[0]))
            return
        if p == "/relay/outbox":
            self._send(200, STORE.read("outbox", q.get("since", ["0"])[0]),
                                  float(q.get("wait", ["0"])[0]))
            return
        if p == "/mcp":
            if scope != policy.SCOPE_PRIVATE:
                self._send(405, {"error": "GET /mcp is not offered on the public endpoint; "
                                          "use POST /mcp (streamable http)"})
                return
            self._sse_stream(q, keepalive_only=True)
            return
        if p == "/sse":
            if scope != policy.SCOPE_PRIVATE:
                self._send(405, {"error": "the legacy SSE transport is only available to "
                                          "device/LAN clients; use POST /mcp"})
                return
            self._sse_stream(q, keepalive_only=False)
            return
        self._send(404, {"error": "not found", "path": p})

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = u.path.rstrip("/") or "/"
        _bump("requests_total", path=p, method="POST")

        if p in ("/register", "/token"):
            oauth.handle(self, p, q)
            return

        if not self._auth(q):
            _bump("auth_failures_total")
            self._log("unauthorized", path=p)
            self._send(401, {"error": "unauthorized"})
            return

        if rate_limited(self.client_address[0], CONFIG.rate_limit_per_min):
            _bump("rate_limited_total")
            self._send(429, {"error": "rate limit exceeded"}, headers={"Retry-After": "60"})
            return

        scope = self._scope()
        _bump("scope_public_total" if scope == policy.SCOPE_PUBLIC else "scope_private_total")

        if p == "/mcp":
            self._mcp_post(scope)
            return
        if p == "/messages":
            sid = q.get("session_id", [""])[0]
            body = self._body()
            res = self._rpc(body, scope) if isinstance(body, dict) else None
            if res is not None and sid in SSE_SESSIONS:
                SSE_SESSIONS[sid].put(res)
            self._send(202, b"", "application/json")
            return
        if p == "/relay/inbox":
            b = self._body()
            self._send(200, STORE.push("inbox", b.get("text", ""), "minis", "claude"))
            return
        if p == "/relay/outbox":
            b = self._body()
            self._send(200, STORE.push("outbox", b.get("text", ""), "claude", "minis"))
            return
        if p == "/iphone":
            b = self._body()
            self._send(200, apple.run(b.get("tool", ""), *(b.get("args") or []),
                                      timeout=CONFIG.tool_timeout))
            return
        if p == "/shell":
            allowed, why = policy.tool_allowed("iphone_shell", scope,
                                                CONFIG.private_only_tools,
                                                CONFIG.allow_public_shell)
            if not allowed:
                self._send(403, {"error": why})
                return
            self._send(200, tools.t_shell(self._body()))
            return
        if p == "/sessions":
            b = self._body()
            self._send(200, minis_sessions.send(b.get("prompt", ""), b.get("session_id"),
                                                 b.get("model")))
            return
        self._send(404, {"error": "not found", "path": p})

    # ------------------------------------------------------------------ #
    def _sse_stream(self, q, keepalive_only):
        sid = secrets.token_hex(8)
        qq = queue.Queue()
        SSE_SESSIONS[sid] = qq
        endpoint = "/messages?session_id=%s" % sid
        tok = (q.get("token", [""])[0] or "").strip()
        if tok:
            endpoint += "&token=" + tok
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def chunk(data):
            self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
            self.wfile.flush()

        try:
            if keepalive_only:
                while True:
                    chunk(b": ping\n\n")
                    time.sleep(10)
            chunk(("event: endpoint\ndata: %s\n\n" % endpoint).encode())
            while True:
                try:
                    msg = qq.get(timeout=15)
                except queue.Empty:
                    chunk(b": ping\n\n")
                    continue
                chunk(("event: message\ndata: %s\n\n"
                       % json.dumps(msg, ensure_ascii=False)).encode())
        except (BrokenPipeError, ConnectionResetError, ValueError, OSError):
            pass
        finally:
            SSE_SESSIONS.pop(sid, None)
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except Exception:
                pass


def _prometheus():
    lines = []
    with _METRICS_LOCK:
        for k, v in METRICS.items():
            lines.append("# TYPE %s counter" % k)
            lines.append("%s %s" % (k, v))
        for k, v in sorted(_BY_PATH.items()):
            lines.append("minis_bridge_requests_by_path_total{%s} %s" % (k, v))
    return "\n".join(lines) + "\n"


def main():
    global STORE
    logging_setup.setup(CONFIG.log_level, CONFIG.log_file)
    STORE = RelayStore(CONFIG.db_file)
    tools.set_store(STORE)
    oauth.init(CONFIG)
    srv = ThreadingHTTPServer((CONFIG.host, CONFIG.port), Handler)
    srv.daemon_threads = True
    role = os.environ.get("MINIS_BRIDGE_ROLE", "private")
    logging_setup.event(LOG, "bridge listening", host=CONFIG.host, port=CONFIG.port,
                        role=role, pid=os.getpid())
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
