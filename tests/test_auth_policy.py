"""Authentication matrix and the public/private scope policy.

The most important regression test in this file is
``test_public_client_cannot_reach_shell_even_with_spoofed_headers`` - the
prototype trusted the ``CF-Connecting-IP`` header, which let a public client
claim a private address and unlock ``iphone_shell`` (remote code execution on
the phone).
"""
import json

from minis_bridge import policy


def test_missing_token_is_401(client, cfg):
    status, _ = client.raw("/tools")
    assert status == 401


def test_wrong_token_is_401(client, cfg):
    status, _ = client.raw("/tools", headers={"Authorization": "Bearer nope-nope-nope"})
    assert status == 401


def test_bearer_header_ok(client, cfg):
    status, _ = client.raw("/tools", headers={"Authorization": "Bearer " + cfg.token()})
    assert status == 200


def test_query_token_ok(client, cfg):
    status, _ = client.raw("/tools?token=" + cfg.token())
    assert status == 200


def test_x_min_token_ok(client, cfg):
    status, _ = client.raw("/tools", headers={"X-Min-Token": cfg.token()})
    assert status == 200


def test_health_needs_no_auth(client, cfg):
    status, body = client.raw("/health")
    assert status == 200
    assert json.loads(body)["ok"] is True


# --------------------------------------------------------------------------- #
# scope policy
# --------------------------------------------------------------------------- #
def test_classify_loopback_is_private():
    assert policy.classify("127.0.0.1", "127.0.0.1:8900", role="private") == policy.SCOPE_PRIVATE


def test_classify_lan_is_private():
    assert policy.classify("192.168.1.20", "192.168.1.20:8900", role="private") == policy.SCOPE_PRIVATE


def test_classify_public_peer_is_public():
    assert policy.classify("8.8.8.8", "example.com", role="private") == policy.SCOPE_PUBLIC


def test_classify_tunnel_hostname_is_public():
    assert policy.classify("127.0.0.1", "abc.trycloudflare.com",
                            role="private") == policy.SCOPE_PUBLIC


def test_classify_public_role_always_public():
    assert policy.classify("127.0.0.1", "127.0.0.1:8901",
                            role="public") == policy.SCOPE_PUBLIC


def test_headers_can_never_widen_the_scope():
    """A public client that claims a private address stays public."""
    scope = policy.classify("8.8.8.8", "192.168.1.20:8900", role="private")
    assert scope == policy.SCOPE_PUBLIC


def test_tool_allowed_blocks_shell_for_public():
    allowed, why = policy.tool_allowed("iphone_shell", policy.SCOPE_PUBLIC)
    assert not allowed and why


def test_tool_allowed_allows_shell_for_private():
    allowed, _ = policy.tool_allowed("iphone_shell", policy.SCOPE_PRIVATE)
    assert allowed


def test_public_client_cannot_reach_shell_even_with_spoofed_headers(public_bridge):
    client, cfg = public_bridge.client, public_bridge.cfg
    status, body = client.call_tool("iphone_shell", {"command": "echo pwned"},
                                    headers={"CF-Connecting-IP": "192.168.1.1",
                                             "X-Forwarded-For": "10.0.0.1"})
    assert "error" in body, body
    assert "-32001" in json.dumps(body)


def test_public_client_cannot_use_shell_rest_endpoint(public_bridge):
    client, cfg = public_bridge.client, public_bridge.cfg
    status, body = client.raw("/shell", "POST", {"command": "echo pwned"},
                               headers={"Authorization": "Bearer " + cfg.token()})
    assert status == 403
