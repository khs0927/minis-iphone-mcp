"""Shared pytest fixtures: a live bridge on an ephemeral port with a temp state."""
import json
import os
import socket
import sys
import threading
import time
import types
import urllib.error
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, base, token):
        self.base = base.rstrip("/")
        self.token = token
        self.opener = urllib.request.build_opener(_NoRedirect)

    def raw(self, path, method="GET", payload=None, headers=None, timeout=15):
        data = json.dumps(payload).encode() if payload is not None else None
        hdr = {"Content-Type": "application/json"}
        hdr.update(headers or {})
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=hdr)
        try:
            with self.opener.open(req, timeout=timeout) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def rpc(self, method, params=None, rid=1, token=None, headers=None):
        hdr = {"Authorization": "Bearer " + (token if token is not None else self.token)}
        hdr.update(headers or {})
        status, body = self.raw("/mcp", "POST",
                                 {"jsonrpc": "2.0", "id": rid, "method": method,
                                  "params": params or {}}, headers=hdr)
        try:
            return status, json.loads(body) if body else {}
        except ValueError:
            return status, {"_raw": body}

    def call_tool(self, name, args=None, token=None, headers=None):
        return self.rpc("tools/call", {"name": name, "arguments": args or {}},
                        token=token, headers=headers)

    def tool_text(self, name, args=None, token=None):
        status, body = self.call_tool(name, args, token)
        try:
            return status, body["result"]["content"][0]["text"]
        except Exception:
            return status, json.dumps(body)


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    """Start the bridge in a thread; yields a namespace with client/cfg/store."""
    from minis_bridge import config as config_mod
    from minis_bridge import oauth as oauth_mod
    from minis_bridge import server as server_mod
    from minis_bridge import tools as tools_mod
    from minis_bridge.store import RelayStore

    state = tmp_path / "state"
    state.mkdir()
    port = _free_port()
    monkeypatch.setenv("MINIS_BRIDGE_STATE_DIR", str(state))
    monkeypatch.setenv("MINIS_BRIDGE_PORT", str(port))
    monkeypatch.setenv("MINIS_BRIDGE_ROLE", "private")
    monkeypatch.setenv("MINIS_BRIDGE_LOG", str(state / "access.log"))
    monkeypatch.setenv("MINIS_BRIDGE_RATE_LIMIT", "1000")
    monkeypatch.setattr(config_mod, "CONFIG", config_mod.Config())
    cfg = config_mod.CONFIG

    store = RelayStore(cfg.db_file)
    tools_mod.set_store(store)
    server_mod.STORE = store
    server_mod.CONFIG = cfg
    oauth_mod.init(cfg)

    httpd = server_mod.ThreadingHTTPServer(("127.0.0.1", port), server_mod.Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)
    ns = types.SimpleNamespace(client=Client("http://127.0.0.1:%d" % port, cfg.token()),
                                cfg=cfg, store=store, port=port)
    try:
        yield ns
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def client(bridge):
    return bridge.client


@pytest.fixture
def cfg(bridge):
    return bridge.cfg


@pytest.fixture
def public_bridge(bridge, monkeypatch):
    """Same server, but every request is classified as coming from the internet."""
    from minis_bridge import policy
    monkeypatch.setattr(policy, "classify", lambda *a, **k: policy.SCOPE_PUBLIC)
    return bridge
