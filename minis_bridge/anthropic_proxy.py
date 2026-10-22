#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Anthropic Messages API -> OpenAI Chat Completions proxy (local, stdlib only).

Lets the real Claude Code CLI run ON the iPhone against any OpenAI-compatible
provider (DeepSeek, NVIDIA NIM, ...), which is what makes the local
"Claude Code" sub-agent possible without any cloud connector.

  listen : 127.0.0.1:8788
  usage  : ANTHROPIC_BASE_URL=http://127.0.0.1:8788 ANTHROPIC_API_KEY=dummy claude -p "hi"

env:
  UPSTREAM_BASE   default https://api.deepseek.com
  UPSTREAM_KEY    env var name holding the key (default DEEPSEEK_API_KEY)
  UPSTREAM_MODEL  model to request upstream (default deepseek-chat)
  PROXY_PORT      default 8788
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PROXY_PORT", "8788"))
UPSTREAM_BASE = os.environ.get("UPSTREAM_BASE", "https://api.deepseek.com").rstrip("/")
UPSTREAM_KEY_VAR = os.environ.get("UPSTREAM_KEY", "DEEPSEEK_API_KEY")
UPSTREAM_MODEL = os.environ.get("UPSTREAM_MODEL", "deepseek-chat")
KEY = os.environ.get(UPSTREAM_KEY_VAR, "")


def jdump(o):
    return json.dumps(o, ensure_ascii=False).encode("utf-8")


def blocks_to_text(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return json.dumps(content, ensure_ascii=False)
    out = []
    for b in content:
        if not isinstance(b, dict):
            out.append(str(b))
        elif b.get("type") == "text":
            out.append(b.get("text", ""))
        elif b.get("type") == "thinking":
            out.append(b.get("thinking", ""))
        elif b.get("type") == "tool_result":
            out.append(tool_result_text(b))
        else:
            out.append("[%s]" % b.get("type", "block"))
    return "\n".join(x for x in out if x)


def tool_result_text(b):
    c = b.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
    if c is None:
        return ""
    return json.dumps(c, ensure_ascii=False)


def anthropic_to_openai(body):
    """Translate an Anthropic /v1/messages body into an OpenAI chat body."""
    msgs = []
    sysblk = body.get("system")
    if sysblk:
        text = blocks_to_text(sysblk)
        if text:
            msgs.append({"role": "system", "content": text})

    for m in body.get("messages") or []:
        role = m.get("role", "user")
        content = m.get("content")
        if isinstance(content, str):
            msgs.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            msgs.append({"role": role, "content": ""})
            continue

        texts = []
        tool_calls = []
        tool_results = []
        for b in content:
            if not isinstance(b, dict):
                texts.append(str(b))
                continue
            t = b.get("type")
            if t == "text":
                texts.append(b.get("text", ""))
            elif t == "tool_use":
                tool_calls.append({
                    "id": b.get("id", "call_%d" % len(tool_calls)),
                    "type": "function",
                    "function": {"name": b.get("name", "tool"),
                                 "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)},
                })
            elif t == "tool_result":
                tool_results.append({"role": "tool",
                                     "tool_call_id": b.get("tool_use_id", ""),
                                     "content": tool_result_text(b) or "(empty)"})
            elif t == "image":
                texts.append("[image omitted]")
            elif t == "thinking":
                pass
            else:
                texts.append("[%s]" % t)

        if tool_results:
            msgs.extend(tool_results)
        if texts or tool_calls:
            msg = {"role": role, "content": "\n".join(x for x in texts if x)}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            msgs.append(msg)

    out = {
        "model": UPSTREAM_MODEL,
        "messages": msgs,
        "max_tokens": body.get("max_tokens", 4096),
    }
    for k_src, k_dst in (("temperature", "temperature"), ("top_p", "top_p")):
        if body.get(k_src) is not None:
            out[k_dst] = body[k_src]
    if body.get("stop_sequences"):
        out["stop"] = body["stop_sequences"]
    tools = body.get("tools")
    if tools:
        otools = []
        for t in tools:
            if not isinstance(t, dict):
                continue
            otools.append({"type": "function", "function": {
                "name": t.get("name", "tool"),
                "description": t.get("description", ""),
                "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
            }})
        if otools:
            out["tools"] = otools
    tc = body.get("tool_choice")
    if isinstance(tc, dict):
        typ = tc.get("type")
        if typ == "auto":
            out["tool_choice"] = "auto"
        elif typ == "any":
            out["tool_choice"] = "required"
        elif typ == "tool" and tc.get("name"):
            out["tool_choice"] = {"type": "function",
                                  "function": {"name": tc["name"]}}
    out["stream"] = bool(body.get("stream"))
    return out


def openai_to_anthropic(resp, model):
    ch = (resp.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    content = []
    text = msg.get("content") or ""
    if text:
        content.append({"type": "text", "text": text})
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            args = {"_raw": raw}
        content.append({"type": "tool_use", "id": tc.get("id", "call_0"),
                        "name": fn.get("name", "tool"), "input": args})
    fr = ch.get("finish_reason")
    stop_reason = {"stop": "end_turn", "length": "max_tokens",
                   "tool_calls": "tool_use", "content_filter": "end_turn"}.get(fr, "end_turn")
    usage = resp.get("usage") or {}
    return {
        "id": "msg_" + str(resp.get("id", "gen"))[:24],
        "type": "message", "role": "assistant", "model": model,
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": usage.get("prompt_tokens", 0),
                  "output_tokens": usage.get("completion_tokens", 0)},
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "anthropic-proxy/1.0"

    def log_message(self, fmt, *args):
        try:
            with open("/var/minis/shared/claude-bridge/proxy.log", "a") as f:
                f.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % args))
        except Exception:
            pass

    def _json(self, code, obj):
        body = jdump(obj)
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            self._json(200, {"data": [{"id": UPSTREAM_MODEL, "type": "model",
                                       "display_name": UPSTREAM_MODEL}],
                             "has_more": False})
        elif self.path.startswith("/health"):
            self._json(200, {"ok": True, "upstream": UPSTREAM_BASE,
                              "model": UPSTREAM_MODEL, "key_set": bool(KEY)})
        else:
            self._json(404, {"error": {"type": "not_found", "message": self.path}})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = {}
        path = self.path.split("?")[0]

        if path.endswith("/count_tokens"):
            txt = json.dumps(body, ensure_ascii=False)
            self._json(200, {"input_tokens": max(1, len(txt) // 4)})
            return

        if not path.endswith("/messages"):
            self._json(404, {"error": {"type": "not_found", "message": path}})
            return

        oai = anthropic_to_openai(body)
        upstream = UPSTREAM_BASE + "/chat/completions"
        req = urllib.request.Request(upstream, data=jdump(oai), method="POST", headers={
            "Authorization": "Bearer " + KEY,
            "Content-Type": "application/json",
        })
        try:
            r = urllib.request.urlopen(req, timeout=300)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:800]
            self._json(e.code, {"type": "error", "error": {
                "type": "api_error", "message": detail}})
            return
        except Exception as e:
            self._json(502, {"type": "error", "error": {
                "type": "api_error", "message": "upstream failed: %s" % e}})
            return

        if not oai.get("stream"):
            data = json.loads(r.read().decode("utf-8"))
            self._json(200, openai_to_anthropic(data, oai["model"]))
            return

        # ---- streaming: OpenAI SSE -> Anthropic SSE ----
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        def ev(name, data):
            self.wfile.write(("event: %s\ndata: %s\n\n" % (name, json.dumps(data, ensure_ascii=False))).encode())
            self.wfile.flush()

        model = oai["model"]
        ev("message_start", {"type": "message_start", "message": {
            "id": "msg_stream", "type": "message", "role": "assistant", "model": model,
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}}})
        text_open = False
        blocks = {}          # tool index -> (block_index, name, id)
        next_index = 0
        finish = None
        out_tokens = 0
        buf = b""
        done = False
        try:
            for raw_chunk in r:
                buf += raw_chunk
                while b"\n" in buf:
                    raw_line, buf = buf.split(b"\n", 1)
                    line = raw_line.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        buf = b""
                        done = True
                        break
                    try:
                        chunk = json.loads(data)
                    except Exception:
                        continue
                    usage = chunk.get("usage") or {}
                    if usage.get("completion_tokens"):
                        out_tokens = usage["completion_tokens"]
                    ch = (chunk.get("choices") or [{}])[0]
                    delta = ch.get("delta") or {}
                    dtext = delta.get("content")
                    if dtext:
                        if not text_open:
                            ev("content_block_start", {"type": "content_block_start", "index": next_index,
                                                      "content_block": {"type": "text", "text": ""}})
                            text_index = next_index
                            next_index += 1
                            text_open = True
                        ev("content_block_delta", {"type": "content_block_delta", "index": text_index,
                                                   "delta": {"type": "text_delta", "text": dtext}})
                    for tc in delta.get("tool_calls") or []:
                        ti = tc.get("index", 0)
                        fn = tc.get("function") or {}
                        if ti not in blocks:
                            bi = next_index
                            next_index += 1
                            blocks[ti] = bi
                            ev("content_block_start", {"type": "content_block_start", "index": bi,
                                                      "content_block": {"type": "tool_use",
                                                                       "id": tc.get("id", "call_%d" % ti),
                                                                       "name": fn.get("name", "tool"),
                                                                       "input": {}}})
                        if fn.get("arguments"):
                            ev("content_block_delta", {"type": "content_block_delta", "index": blocks[ti],
                                                       "delta": {"type": "input_json_delta",
                                                                 "partial_json": fn["arguments"]}})
                    if ch.get("finish_reason"):
                        finish = ch["finish_reason"]
                if done:
                    break
        except Exception as e:
            try:
                ev("error", {"type": "error", "error": {"type": "api_error", "message": str(e)}})
            except Exception:
                pass

        if text_open:
            ev("content_block_stop", {"type": "content_block_stop", "index": text_index})
        for bi in blocks.values():
            ev("content_block_stop", {"type": "content_block_stop", "index": bi})
        stop_reason = {"stop": "end_turn", "length": "max_tokens",
                       "tool_calls": "tool_use"}.get(finish, "end_turn")
        ev("message_delta", {"type": "message_delta",
                             "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                             "usage": {"output_tokens": out_tokens}})
        ev("message_stop", {"type": "message_stop"})
        self.close_connection = True


if __name__ == "__main__":
    if not KEY:
        sys.stderr.write("upstream key missing: %s\n" % UPSTREAM_KEY_VAR)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    print("anthropic proxy on 127.0.0.1:%d -> %s (%s)" % (PORT, UPSTREAM_BASE, UPSTREAM_MODEL))
    sys.stdout.flush()
    srv.serve_forever()
