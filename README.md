<div align="center">

# Minis iPhone MCP Bridge

**Let any MCP client drive a real iPhone — and talk to the Minis agent on it.**

[![CI](https://github.com/khs0927/minis-iphone-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/khs0927/minis-iphone-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)
[![deps](https://img.shields.io/badge/runtime%20deps-0-brightgreen.svg)](pyproject.toml)

</div>

## What this is

A small, dependency-free **MCP server that runs on an iPhone**. It exposes:

* **29 MCP tools** — the iOS frameworks (battery, calendar, reminders, HomeKit,
  HealthKit, location, weather, photos, NFC, clipboard, TTS, …) through the
  Minis app's `apple-*` command line, plus the **Minis conversations**
  themselves (list / search / read / write) and a **relay queue** so a Claude
  agent and the Minis agent can exchange messages.
* Two **network roles** — a *private* endpoint (device/LAN, full tools) and a
  *public* endpoint (the one a tunnel points at, restricted tools).
* A **stateless MCP Streamable HTTP** implementation plus the legacy SSE
  transport for local clients.
* **OAuth 2.1 with Dynamic Client Registration** so a connector can say
  "Sign in" instead of carrying the secret inside the URL.
* Structured logs with **automatic secret redaction**, rate limiting, output
  caps and a Prometheus `/metrics` endpoint.

It was built to connect **Claude Code on a phone** to the Minis agent running
on that same phone — and to do it without leaking the token and without giving
the public internet a shell on the device.

---

## Quick start (on the phone)

```bash
# 1. install the package + the CLI
git clone https://github.com/khs0927/minis-iphone-mcp.git
cd minis-iphone-mcp
sh scripts/install_iphone.sh          # installs into /usr/local/bin + state dir

# 2. run the two roles
minis-bridge start --role private      # 127.0.0.1:8900  (full tool set)
minis-bridge start --role public       # 127.0.0.1:8901  (restricted tool set)

# 3. check
minis-bridge status
minis-bridge selftest
```

Point a tunnel at the **public** port and register it as an MCP connector:

```bash
cloudflared tunnel --protocol http2 --url http://127.0.0.1:8901
# -> https://<random>.trycloudflare.com
```

```
Connector URL : https://<random>.trycloudflare.com/mcp
Authentication : No sign-in
Transport      : Streamable HTTP (default)
```

---

## Architecture

```
                    ┌──────────────────────── iPhone ────────────────────────┐
                    │                                                         │
 Claude Code ──────▶│  public role :8901 ──▶ MCP server ──▶ apple-* CLIs    │
 (claude.ai/app)   │      (restricted)             │            (iOS frameworks)
                    │                               │                            │
 Claude Desktop ──▶│  private role :8900 ─────────▶│  ← 29 tools                │
 (same LAN)        │      (full tool set)          │  ← Minis sessions            │
                    │                               │  ← relay queue (SQLite)      │
                    └───────────────▲───────────────┘                            │
                                    │                                            │
                            cloudflared tunnel (http2)                           │
                    └───────────────┴────────────────────────────────────────────┘
```

### Why two ports?

Tunnel traffic reaches the server from *loopback*, so the peer address cannot
tell "the local network" from "the internet", and HTTP headers are attacker
controlled — a public client could simply claim a private address.  The role
is therefore decided by **which port the request arrived on**, and headers
can only ever *narrow* a scope, never widen it.

| endpoint | reachable from | tools |
|---|---|---|
| `:8900` private | device / LAN | all 29 |
| `:8901` public | anywhere (tunnel) | all except `iphone_shell` |

---

## The 29 tools

| group | tools |
|---|---|
| Minis conversations | `minis_sessions_list` `minis_sessions_search` `minis_session_messages` `minis_session_send` `minis_session_status` |
| iOS native | `iphone` `iphone_help` `iphone_device` `iphone_clipboard` `iphone_homekit` `iphone_alarm` `iphone_reminders` `iphone_calendar` `iphone_healthkit` `iphone_location` `iphone_weather` `iphone_speak` `iphone_open_url` `iphone_maps` `iphone_vision` `iphone_photos` `iphone_notification` `iphone_media` `iphone_nfc` `iphone_shell`¹ |
| relay | `claude_send` `claude_poll` `claude_inbox` |
| introspection | `bridge_info` |

¹ `iphone_shell` is **private-endpoint only**.

`iphone` is the generic escape hatch: it forwards raw arguments to any
`apple-*` command (alarm, bluetooth, calendar, clipboard, device, healthkit,
homekit, location, maps, media, nfc, nlp, notification, open, photos, player,
reminders, speak, speech, vision, weather).  Use `iphone_help` to read a
tool's exact syntax first.

---

## Endpoints

| method | path | purpose |
|---|---|---|
| POST | `/mcp` | MCP JSON-RPC (Streamable HTTP) |
| GET | `/mcp` | server→client SSE stream (private only) |
| GET | `/sse` | legacy HTTP+SSE transport (private only) |
| POST | `/messages?session_id=…` | legacy transport replies |
| GET | `/health` | liveness (no auth) |
| GET | `/metrics` | Prometheus counters |
| GET | `/tools` | tool catalogue |
| GET | `/sessions` | Minis session list (REST) |
| GET/POST | `/relay/inbox`, `/relay/outbox` | relay queue (REST) |
| POST | `/iphone`, `/shell` | native call / shell (private only) |
| GET | `/.well-known/oauth-authorization-server` | OAuth discovery |
| POST | `/register`, `/token` | OAuth DCR + token endpoint |
| GET | `/authorize` | OAuth authorization (PKCE S256) |

Authentication accepts the bearer token in the `Authorization` header, in
`X-Min-Token`, or in a `?token=` query parameter (for clients that cannot set
headers) — plus any OAuth access token issued by `/token`.

---

## Configuration

Everything is environment driven:

| variable | default | meaning |
|---|---|---|
| `MINIS_BRIDGE_ROLE` | `private` | `private` or `public` |
| `MINIS_BRIDGE_PORT` | `8900` | listen port (public role uses 8901) |
| `MINIS_BRIDGE_STATE_DIR` | `/var/minis/shared/claude-bridge` | token, db, logs |
| `MINIS_BRIDGE_PRIVATE_ONLY_TOOLS` | `iphone_shell,shell` | LAN-only tools |
| `MINIS_BRIDGE_ALLOW_PUBLIC_SHELL` | `0` | opt in to shell on the public port |
| `MINIS_BRIDGE_RATE_LIMIT` | `240` | requests per minute per client |
| `MINIS_BRIDGE_MAX_OUTPUT` | `60000` | max characters per tool result |
| `MINIS_BRIDGE_LOG` | `<state>/access.log` | structured JSON log file |
| `MINIS_BRIDGE_ISSUER` | `http://127.0.0.1:<port>` | OAuth issuer URL |
| `MINIS_BRIDGE_DEFAULT_MODEL` | DeepSeek V4.1 Flash | model for `minis_session_send` |

---

## Development

```bash
python3 -m pytest -q          # 36 tests: protocol, auth, policy, relay, OAuth
python3 -m minis_bridge.cli selftest   # live end-to-end check
```

The test suite starts a real server on an ephemeral port and covers the
protocol, the authentication matrix, the public/private policy (including the
spoofed-header regression), the relay queue under concurrent writers, log
redaction and the full OAuth code exchange.

## Documentation

* [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — module map and design notes
* [`docs/SECURITY.md`](docs/SECURITY.md) — threat model, what was fixed and why
* [`docs/CLAUDE_CODE_SETUP.md`](docs/CLAUDE_CODE_SETUP.md) — connecting Claude Code / the Claude app

## License

MIT — see [LICENSE](LICENSE).
