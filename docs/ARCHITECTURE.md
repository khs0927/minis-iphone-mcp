# Architecture

## Module map

| module | responsibility |
|---|---|
| `minis_bridge/server.py` | HTTP transport, MCP JSON-RPC, scope enforcement, metrics, rate limiting |
| `minis_bridge/tools.py` | the 29 tool definitions (name, schema, handler) |
| `minis_bridge/apple.py` | wrapper around the iOS `apple-*` command line |
| `minis_bridge/minis_sessions.py` | wrapper around the Minis app's `minis-sessions-cli` |
| `minis_bridge/policy.py` | private/public scope classification and the tool allow-list |
| `minis_bridge/store.py` | SQLite relay queue (atomic, crash safe) |
| `minis_bridge/oauth.py` | OAuth 2.1 + DCR + PKCE (stdlib only) |
| `minis_bridge/config.py` | environment driven configuration |
| `minis_bridge/logging_setup.py` | structured JSON logs with secret redaction |
| `minis_bridge/cli.py` | `minis-bridge` control CLI |

## Request flow

```
client ──▶ Handler.do_POST
             ├─ auth?      (header | X-Min-Token | ?token= | OAuth access token)
             ├─ rate limit? (fixed window, per client address)
             ├─ scope      (policy.classify → private | public)
             └─ _rpc()
                 ├─ initialize / ping / tools/list
                 └─ tools/call
                      ├─ policy.tool_allowed()   ← public scope loses iphone_shell
                      └─ tools.TOOLS[name][3](args)
                           └─ apple.* / minis_sessions.* / store
```

## Design decisions

### Two roles instead of header sniffing
The prototype decided "is this client on the LAN?" by looking at the peer
address and the `CF-Connecting-IP` header.  Both are attacker controlled when
the server is reachable from the internet, so a public client could claim a
private address and unlock `iphone_shell` — remote code execution on the
phone.  The role is now bound to the **port**: `:8900` is private, `:8901` is
public, and the tunnel points at the public one.  Headers can only narrow a
scope, never widen it.

### Stateless MCP
The server does not hand out an `Mcp-Session-Id`.  The MCP spec allows both,
and stateless means a client never has to echo a session id back — which
removes a whole class of "client sends a stale session id" bugs.  A client
that sends one anyway is ignored.

### SQLite for the relay
The prototype rewrote a JSON file on every message, which loses messages when
two writers race (bridge + keepalive + Claude Code at the same time).  SQLite
gives atomic appends, indexed reads and crash safety with zero extra
dependencies — important because the target is an Alpine/musl sandbox where
many wheels do not exist.

### Stdlib only
The official `mcp` Python SDK pulls in `anyio`, `starlette`, `pydantic` and
friends, none of which have musllinux/aarch64 wheels.  The hand written
protocol layer keeps the whole bridge installable with `python3` alone, and
the test suite verifies the protocol behaviour that matters.

### Secrets never logged
The access log used to contain the full request line — including
`?token=…`.  Logs are now structured JSON and every string passes through
`logging_setup.redact()` before it is written.

## Known limitations

* A quick tunnel (cloudflared/ngrok) changes its hostname when it restarts,
  which breaks a *pre-registered* connector.  Use a named tunnel with a
  stable hostname, or re-register after the URL changes.  The bridge prints
  the current URL with `minis-bridge url`.
* iOS suspends apps: the server and the tunnel die with the app.  Run a
  keep-alive that restarts them (`minis-keepalive`), or host the tunnel on an
  always-on machine and point it at the phone's LAN address.
* The `iphone_shell` tool is intentionally unavailable on the public endpoint.
  Set `MINIS_BRIDGE_ALLOW_PUBLIC_SHELL=1` only if you accept that.
