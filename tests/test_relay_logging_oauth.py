"""Relay queue, logging redaction and OAuth (DCR + PKCE)."""
import json
import threading
import time
import urllib.parse

from minis_bridge import logging_setup
from minis_bridge.store import RelayStore


# --------------------------------------------------------------------------- #
# relay
# --------------------------------------------------------------------------- #
def test_push_and_read(tmp_path):
    store = RelayStore(str(tmp_path / "r.db"))
    item = store.push("inbox", "hello", "minis", "claude")
    got = store.read("inbox", 0)
    assert got["count"] == 1
    assert got["items"][0]["id"] == item["id"]
    assert got["items"][0]["text"] == "hello"
    assert got["items"][0]["from"] == "minis"


def test_cursor_is_monotonic(tmp_path):
    store = RelayStore(str(tmp_path / "r.db"))
    store.push("inbox", "one")
    first = store.read("inbox", 0)
    store.push("inbox", "two")
    second = store.read("inbox", first["cursor"])
    assert [i["text"] for i in second["items"]] == ["two"]


def test_wait_returns_when_message_arrives(tmp_path):
    store = RelayStore(str(tmp_path / "r.db"))
    out = {}

    def reader():
        out["res"] = store.read("inbox", 0, wait=6)

    t = threading.Thread(target=reader)
    t.start()
    time.sleep(0.4)
    store.push("inbox", "late")
    t.join(timeout=8)
    assert out["res"]["count"] == 1


def test_concurrent_writers_do_not_lose_messages(tmp_path):
    """The prototype lost messages with a JSON file + read-modify-write."""
    store = RelayStore(str(tmp_path / "r.db"))
    errors = []

    def worker(n):
        try:
            for i in range(10):
                store.push("inbox", "w%d-%d" % (n, i))
        except Exception as e:      # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors
    assert store.count("inbox") == 80


def test_prune_keeps_queue_small(tmp_path):
    store = RelayStore(str(tmp_path / "r.db"))
    for i in range(50):
        store.push("inbox", str(i))
    store.prune(keep_per_box=10)
    assert store.count("inbox") <= 10


# --------------------------------------------------------------------------- #
# logging
# --------------------------------------------------------------------------- #
def test_redact_hides_token_in_query():
    out = logging_setup.redact("GET /mcp?token=mk-abcde-fghij-klmno-pqrst HTTP/1.1")
    assert "mk-abcde" not in out
    assert "<redacted>" in out


def test_redact_hides_bearer_header():
    out = logging_setup.redact('{"authorization": "Bearer sk-supersecretvalue"}')
    assert "supersecretvalue" not in out


def test_redact_keeps_ordinary_text():
    assert logging_setup.redact("battery 90% unplugged") == "battery 90% unplugged"


# --------------------------------------------------------------------------- #
# OAuth (DCR + PKCE)
# --------------------------------------------------------------------------- #
def test_oauth_discovery(client, cfg):
    status, body = client.raw("/.well-known/oauth-authorization-server")
    assert status == 200
    doc = json.loads(body)
    assert doc["registration_endpoint"].endswith("/register")
    assert doc["code_challenge_methods_supported"] == ["S256"]


def test_oauth_registration_and_token_exchange(client, cfg):
    status, body = client.raw("/register", "POST",
                               {"client_name": "test", "redirect_uris": ["https://example.com/cb"]})
    assert status == 201
    reg = json.loads(body)
    assert reg["client_id"]

    import base64
    import hashlib
    verifier = "a" * 64
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")

    status, body = client.raw(
        "/authorize?" + urllib.parse.urlencode({
            "client_id": reg["client_id"],
            "redirect_uri": "https://example.com/cb",
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
        }), headers={"Authorization": "Bearer " + cfg.token()})
    assert status in (200, 302)
    location = None
    if status == 302:
        location = "Location"
    # the auto-approve flow redirects with the code in the query string
    from urllib.error import HTTPError
    try:
        client.raw("/authorize?" + urllib.parse.urlencode({
            "client_id": reg["client_id"], "redirect_uri": "https://example.com/cb",
            "response_type": "code", "code_challenge": challenge,
            "code_challenge_method": "S256", "state": "xyz"}),
            headers={"Authorization": "Bearer " + cfg.token()})
    except HTTPError as e:
        location = None
    # fetch a code directly through the module (the redirect is not followed by urllib)
    from minis_bridge import oauth
    with oauth._LOCK, oauth._db() as conn:
        row = conn.execute("SELECT code FROM codes ORDER BY created DESC LIMIT 1").fetchone()
    assert row is not None
    code = row["code"]

    status, body = client.raw("/token", "POST", {
        "grant_type": "authorization_code", "code": code,
        "client_id": reg["client_id"], "redirect_uri": "https://example.com/cb",
        "code_verifier": verifier})
    assert status == 200
    token = json.loads(body)["access_token"]

    # the access token authenticates the MCP endpoint
    status, body = client.rpc("tools/list", token=token)
    assert status == 200
    assert body["result"]["tools"]


def test_oauth_rejects_bad_pkce(client, cfg):
    status, body = client.raw("/register", "POST", {"client_name": "t",
                                                     "redirect_uris": ["https://e.com/cb"]})
    reg = json.loads(body)
    from minis_bridge import oauth
    import base64
    import hashlib
    challenge = base64.urlsafe_b64encode(hashlib.sha256(b"right").digest()).decode().rstrip("=")
    client.raw("/authorize?" + urllib.parse.urlencode({
        "client_id": reg["client_id"], "redirect_uri": "https://e.com/cb",
        "response_type": "code", "code_challenge": challenge,
        "code_challenge_method": "S256"}), headers={"Authorization": "Bearer " + cfg.token()})
    with oauth._LOCK, oauth._db() as conn:
        row = conn.execute("SELECT code FROM codes ORDER BY created DESC LIMIT 1").fetchone()
    status, body = client.raw("/token", "POST", {
        "grant_type": "authorization_code", "code": row["code"],
        "client_id": reg["client_id"], "redirect_uri": "https://e.com/cb",
        "code_verifier": "wrong-verifier-entirely"})
    assert status == 400
    assert json.loads(body)["error"] == "invalid_grant"
