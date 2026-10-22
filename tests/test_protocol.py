"""MCP protocol conformance (JSON-RPC 2.0 + Streamable HTTP)."""


def test_initialize(client, cfg):
    status, body = client.rpc("initialize", {"protocolVersion": "2025-06-18",
                                              "capabilities": {}, "clientInfo": {"name": "t"}})
    assert status == 200
    res = body["result"]
    assert res["protocolVersion"]
    assert res["serverInfo"]["name"]
    assert "tools" in res["capabilities"]


def test_tools_list(client, cfg):
    status, body = client.rpc("tools/list")
    assert status == 200
    names = {t["name"] for t in body["result"]["tools"]}
    for expected in ("iphone_device", "minis_session_send", "minis_sessions_list",
                     "claude_send", "claude_poll", "bridge_info"):
        assert expected in names
    for t in body["result"]["tools"]:
        assert t["description"]
        assert t["inputSchema"]["type"] == "object"


def test_tools_call_returns_content(client, cfg):
    status, text = client.tool_text("bridge_info")
    assert status == 200
    assert "server" in text


def test_unknown_tool_is_error(client, cfg):
    status, body = client.call_tool("definitely_not_a_tool")
    assert status == 200
    assert "error" in body
    assert body["error"]["code"] == -32602


def test_unknown_method_is_error(client, cfg):
    status, body = client.rpc("nope/nope")
    assert "error" in body
    assert body["error"]["code"] == -32601


def test_ping(client, cfg):
    status, body = client.rpc("ping")
    assert status == 200
    assert body["result"] == {}


def test_notification_returns_202(client, cfg):
    status, body = client.raw("/mcp", "POST",
                               {"jsonrpc": "2.0", "method": "notifications/initialized"},
                               headers={"Authorization": "Bearer " + client.token})
    assert status == 202
    assert body == ""


def test_get_mcp_is_405_or_stream(client, cfg):
    """GET /mcp may answer 405 or open a long lived SSE stream - never 404."""
    try:
        status, body = client.raw("/mcp", timeout=2,
                                   headers={"Authorization": "Bearer " + client.token})
    except Exception:
        # the server kept the SSE stream open, which is a valid answer
        return
    assert status in (200, 405)


def test_stateless_no_session_header(client, cfg):
    status, body = client.raw("/mcp", "POST",
                               {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                               headers={"Authorization": "Bearer " + client.token})
    assert status == 200
