# Connecting Claude Code / the Claude app

## 1. The connector (recommended)

Claude's custom connectors are fetched by **Anthropic's cloud**, not by the
device — so the phone must be reachable over **public HTTPS**.

```
URL           : https://<your-tunnel-host>/mcp
Authentication: No sign-in
Transport     : Streamable HTTP (default)
```

If the connector UI offers **Request headers**, add:

```
Authorization: Bearer <token>          # minis-bridge token
```

Otherwise use the query form (the token then appears in the UI):

```
https://<your-tunnel-host>/mcp?token=<token>
```

> With the OAuth endpoints of this bridge you can avoid the secret in the URL
> entirely: register a client with `POST /register`, redirect the user to
> `GET /authorize` (PKCE S256) and exchange the code at `POST /token`.

### Registering the connector in the Claude app (web)

1. claude.ai → **Customize** → **Connectors** → **+ Add** → **Add custom connector**
2. Paste the URL, choose **No sign-in**, keep the default transport
3. **Advanced → tool permissions → Always allow** for the tools you want the
   agent to use without asking
4. Save — the connector is now available in the iOS app and in Claude Code
   (web / mobile), because they share the account

### Known gotchas

* A cloud sandbox running `curl` against your tunnel is usually blocked by the
  permission classifier ("exfiltration scouting").  **Use the connector tools,
  not curl.**
* If the connector tools are set to *approval required*, Claude Code's
  automatic mode blocks them ("auto-mode bypass").  Set them to *always
  allow*.
* A quick tunnel hostname changes when the tunnel restarts → re-register the
  connector with the new URL, or use a named tunnel.

## 2. Claude Code CLI (same device or LAN)

```bash
claude mcp add --scope user --transport http minis-iphone \
  http://192.168.123.115:8900/mcp \
  --header "Authorization: Bearer $(cat /var/minis/shared/claude-bridge/token)"

claude mcp list          # health check
```

Use the **private** port here: the CLI runs on the same network, so it can
use the full tool set including `iphone_shell`.

## 3. Claude Code CLI *on the phone* (Alpine/iSH)

Claude Code ships as a Bun native binary, which crashes in the iOS sandbox
(`Illegal instruction`).  The pure-JS build (1.0.128) runs under Node:

```bash
apk add nodejs npm
# node needs a smaller thread stack in this sandbox
ulimit -s 8192
node cli.js --version
```

Two iOS specific requirements:

* `ulimit -s 8192` — the default 128 MB stack makes `pthread_create` fail
  (`uv_thread_create` assertion) and Node dies at startup.
* An Anthropic-compatible endpoint.  This repository ships
  `minis_bridge/anthropic_proxy.py`, a stdlib-only translation proxy that
  exposes the Anthropic Messages API on top of an OpenAI-compatible provider
  (DeepSeek, NVIDIA NIM, …):

```bash
python3 -m minis_bridge.anthropic_proxy &      # 127.0.0.1:8788
ANTHROPIC_BASE_URL=http://127.0.0.1:8788 \
ANTHROPIC_API_KEY=local-proxy \
claude -p "아이폰 배터리 알려줘"
```

## 4. Exchanging messages with the Minis agent

Both directions are available as MCP tools:

```jsonc
// Minis -> Claude
{"name": "claude_send",  "arguments": {"prompt": "배터리 확인해줘"}}
// Claude -> Minis
{"name": "claude_poll",  "arguments": {"wait": 5}}
// straight into a Minis conversation
{"name": "minis_session_send", "arguments": {"prompt": "안녕하세요"}}
{"name": "minis_session_messages", "arguments": {"session_id": "..."}}
```

The relay queue is a SQLite table, so nothing is lost when the phone sleeps
or two writers race.
