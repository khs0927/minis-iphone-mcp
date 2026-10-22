# Security

## Threat model

The bridge is reachable from three places: the device itself, the local
network, and — through a tunnel — the public internet.  The bearer token is
the only thing standing between "anyone on the internet" and "a shell on the
phone".

| # | Threat | Mitigation |
|---|---|---|
| T1 | Public client claims a private address to unlock the shell | Scope is bound to the **port** (`MINIS_BRIDGE_ROLE`), headers can only narrow it |
| T2 | Token leaks through logs | Structured JSON logs + `redact()` on every string |
| T3 | Token leaks through the URL | `Authorization` header preferred; `?token=` supported but discouraged; OAuth DCR available so no secret is needed in the URL at all |
| T4 | Unauthenticated access | Every endpoint except `/health` and `/metrics` requires a token (or OAuth access token) |
| T5 | DoS via long polling / flooding | Fixed-window rate limit, thread-per-connection capped by the server, output size caps, tool timeouts |
| T6 | Command injection through tool arguments | Arguments are passed as **argv lists**, never through a shell; only the explicit `iphone_shell` tool uses `/bin/sh -c`, and it is private-only |
| T7 | Sensitive data in tool output | Tool output is capped (`MINIS_BRIDGE_MAX_OUTPUT`) and never logged |
| T8 | Message loss in the relay | SQLite with WAL and atomic writes; a concurrency test covers 8 parallel writers |
| T9 | Malicious MCP client | Unknown tools/methods return JSON-RPC errors; tool calls are validated against the catalogue; a failing tool can never kill the server |

## What was fixed in 2.0

The 1.x prototype was audited; these were the findings and the fixes:

| finding (1.x) | fix (2.0) |
|---|---|
| `CF-Connecting-IP` was trusted unconditionally → a public client could spoof a private IP and run `iphone_shell` (**remote code execution**) | role/port based scoping; the spoofing case is now a regression test (`test_public_client_cannot_reach_shell_even_with_spoofed_headers`) |
| the access log contained the full request line, including `?token=…` | structured JSON logs with redaction; the request line is never printed |
| no rate limiting, no output cap, no tool timeout caps | rate limit, output cap, per-tool timeout, metrics |
| relay queue was a JSON file rewritten on every change (racy) | SQLite |
| `bridge_info` leaked the token file path and a token preview | removed |
| no test suite | 36 pytest tests + CI |
| `Mcp-Session-Id` was generated but never enforced | stateless server (no session id issued) |

## Hardening checklist for a public deployment

1. Use a **named** tunnel with a stable hostname (a quick tunnel changes its
   name on every restart and your connector URL breaks).
2. Keep `MINIS_BRIDGE_ALLOW_PUBLIC_SHELL=0` (the default).
3. Rotate the token after every change: `minis-bridge token --rotate`, then
   update the connector.
4. Terminate TLS at the tunnel, never expose the raw port.
5. Prefer OAuth (`/register` + `/authorize` + `/token`) over a static token in
   the URL, so the secret never reaches the connector configuration UI.
6. Review `minis-bridge selftest` output after every upgrade.
