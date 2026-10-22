"""MCP tool catalogue.

Each tool is a declarative entry: name, description, JSON schema, handler.
Handlers return JSON-serialisable data; the transport turns that into MCP
content and enforces the client scope policy.
"""
import os
import subprocess
import time

from . import apple, minis_sessions
from .config import CONFIG

_START = time.time()
_STORE = None


def set_store(store):
    """Inject the relay store (avoids an import cycle)."""
    global _STORE
    _STORE = store


def _schema(props, required=None):
    s = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        s["required"] = required
    return s


def _s(t, d):
    return {"type": t, "description": d}


def _opt(argv, key, flag, val=None):
    if key in argv and argv[key] not in (None, ""):
        argv.append(flag)
        if val is not None:
            argv.append(str(argv[key]))
    return argv


# --------------------------------------------------------------------------- #
# Minis conversation
# --------------------------------------------------------------------------- #
def t_sessions_list(a):
    return minis_sessions.list_sessions(a.get("limit", 30))


def t_sessions_search(a):
    kw = a.get("keywords", "")
    if not kw:
        return {"error": "keywords is required"}
    return minis_sessions.search(kw, a.get("limit", 30))


def t_session_messages(a):
    return minis_sessions.messages(a.get("session_id", ""), a.get("offset", 0), a.get("full", False))


def t_session_send(a):
    prompt = a.get("prompt", "")
    if not prompt:
        return {"error": "prompt is required"}
    return minis_sessions.send(prompt, a.get("session_id"), a.get("model"))


def t_session_status(a):
    return minis_sessions.status(a.get("session_id", ""))


# --------------------------------------------------------------------------- #
# native iOS
# --------------------------------------------------------------------------- #
def t_iphone(a):
    try:
        return apple.run(a.get("tool", ""), *(a.get("args") or []),
                         timeout=CONFIG.tool_timeout, max_output=CONFIG.max_tool_output)
    except apple.NativeError as e:
        return {"error": str(e), "available": sorted(apple.available_tools())}


def t_iphone_help(a):
    try:
        return apple.help_text(a.get("tool", ""))
    except apple.NativeError as e:
        return {"error": str(e), "available": sorted(apple.available_tools())}


def t_device(a):
    return {"info": apple.run("device", "info"),
            "battery": apple.run("device", "battery"),
            "storage": apple.run("device", "storage")}


def t_clipboard(a):
    act = a.get("action", "get")
    if act == "set":
        return apple.run("clipboard", "set", "--text", a.get("text", ""))
    if act in ("clear", "status"):
        return apple.run("clipboard", act)
    return apple.run("clipboard", "get")


def t_homekit(a):
    act = a.get("action", "list")
    argv = [act]
    if act == "search":
        argv += ["--query", a.get("query", "")]
        _opt(argv, "type", "--type")
        _opt(argv, "room", "--room")
    elif act == "get":
        argv += ["--name", a.get("name", "")]
    elif act == "set":
        argv += ["--name", a.get("name", ""), "--characteristic",
                 a.get("characteristic", ""), "--value", a.get("value", "")]
    elif act == "trigger":
        argv += ["--name", a.get("name", "")]
    argv += list(a.get("args") or [])
    return apple.run("homekit", *argv)


def t_alarm(a):
    act = a.get("action", "list")
    argv = [act]
    if act == "set":
        argv += ["--time", a.get("time", "")]
        _opt(argv, "label", "--label")
        _opt(argv, "repeat", "--repeat")
    elif act == "timer":
        argv += ["--duration", a.get("duration", "5m")]
        _opt(argv, "label", "--label")
    elif act == "cancel":
        argv += ["--id", a["id"]] if a.get("id") else ["--all"]
    argv += list(a.get("args") or [])
    return apple.run("alarm", *argv)


def t_reminders(a):
    act = a.get("action", "list")
    argv = [act]
    if act == "list":
        _opt(argv, "list", "--list")
        _opt(argv, "list_id", "--list-id")
        _opt(argv, "limit", "--limit")
        if a.get("incomplete"):
            argv += ["--incomplete"]
    elif act == "create":
        argv += ["--title", a.get("title", "")]
        _opt(argv, "list", "--list")
        _opt(argv, "list_id", "--list-id")
        _opt(argv, "due", "--due")
        _opt(argv, "notes", "--notes")
    elif act in ("complete", "delete", "read", "update"):
        argv += ["--id", a.get("id", "")]
        _opt(argv, "title", "--title")
        _opt(argv, "notes", "--notes")
        _opt(argv, "due", "--due")
    argv += list(a.get("args") or [])
    return apple.run("reminders", *argv)


def t_calendar(a):
    act = a.get("action", "list")
    argv = [act]
    if act == "list":
        _opt(argv, "start", "--start")
        _opt(argv, "end", "--end")
        _opt(argv, "days", "--days")
        _opt(argv, "limit", "--limit")
        _opt(argv, "calendar", "--calendar")
        if a.get("today"):
            argv += ["--today"]
    elif act == "create":
        argv += ["--title", a.get("title", "")]
        if a.get("start"):
            argv += ["--start", a["start"]]
        if a.get("end"):
            argv += ["--end", a["end"]]
        _opt(argv, "calendar", "--calendar")
        _opt(argv, "notes", "--notes")
        _opt(argv, "location", "--location")
    elif act == "delete":
        argv += ["--id", a.get("id", "")]
    argv += list(a.get("args") or [])
    return apple.run("calendar", *argv)


def t_healthkit(a):
    types = a.get("types") or []
    days = a.get("days", 7)
    if not types:
        return apple.run("healthkit", "types")
    if len(types) == 1:
        return apple.run("healthkit", "--type", types[0], "--days", str(days))
    return apple.run("healthkit", "batch", "--types", ",".join(types), "--days", str(days))


def t_location(a):
    act = a.get("action", "current")
    argv = [act]
    if act == "current":
        _opt(argv, "accuracy", "--accuracy")
    elif act == "geocode":
        argv += ["--lat", a.get("lat", ""), "--lng", a.get("lng", "")]
    elif act == "forward":
        argv += ["--address", a.get("address", "")]
    argv += list(a.get("args") or [])
    return apple.run("location", *argv)


def t_weather(a):
    return apple.run("weather", a.get("action", "current"))


def t_speak(a):
    argv = ["speak", "--text", a.get("text", "")]
    _opt(argv, "voice", "--voice")
    _opt(argv, "rate", "--rate")
    _opt(argv, "pitch", "--pitch")
    return apple.run("speak", *argv)


def t_open(a):
    return apple.run("open", a.get("url", ""))


def t_maps(a):
    act = a.get("action", "search")
    argv = [act]
    if act == "search":
        argv += ["--query", a.get("query", "")]
        _opt(argv, "lat", "--lat")
        _opt(argv, "lon", "--lon")
        _opt(argv, "radius", "--radius")
        _opt(argv, "limit", "--limit")
    else:
        argv += ["--from", a.get("from", ""), "--to", a.get("to", "")]
        _opt(argv, "mode", "--mode")
    argv += list(a.get("args") or [])
    return apple.run("maps", *argv)


def t_vision(a):
    argv = [a.get("action", "ocr"), a.get("path", "")]
    _opt(argv, "lang", "--lang")
    _opt(argv, "level", "--level")
    return apple.run("vision", *argv)


def t_photos(a):
    act = a.get("action", "list")
    argv = [act]
    _opt(argv, "limit", "--limit")
    _opt(argv, "album", "--album")
    _opt(argv, "name", "--name")
    _opt(argv, "id", "--id")
    _opt(argv, "path", "--path")
    argv += list(a.get("args") or [])
    return apple.run("photos", *argv)


def t_notification(a):
    act = a.get("action", "pending")
    argv = [act]
    if act == "schedule":
        argv += ["--title", a.get("title", "Minis")]
        if a.get("body"):
            argv += ["--body", a["body"]]
        if a.get("after"):
            argv += ["--after", str(a["after"])]
        elif a.get("at"):
            argv += ["--at", a["at"]]
    elif act == "cancel":
        argv += ["--id", a["id"]] if a.get("id") else ["--all"]
    argv += list(a.get("args") or [])
    return apple.run("notification", *argv)


def t_media(a):
    act = a.get("action", "now-playing")
    argv = [act]
    if act == "volume" and a.get("volume") is not None:
        argv += ["--set", str(a["volume"])]
    if act == "search":
        argv += ["--query", a.get("query", "")]
        _opt(argv, "type", "--type")
        _opt(argv, "limit", "--limit")
    argv += list(a.get("args") or [])
    return apple.run("media", *argv)


def t_nfc(a):
    argv = [a.get("action", "status")] + list(a.get("args") or [])
    return apple.run("nfc", *argv)


def t_shell(a):
    """Run a shell command in the phone's own environment (private clients)."""
    cmd = a.get("command", "")
    if not cmd:
        return {"error": "command is required"}
    timeout = int(a.get("timeout", 60))
    try:
        proc = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True,
                              timeout=timeout, cwd=os.environ.get("MINIS_SHELL_CWD", "/root"))
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "command": cmd}
    except OSError as e:
        return {"error": str(e), "command": cmd}
    out = proc.stdout or ""
    err = proc.stderr or ""
    if len(out) > CONFIG.max_tool_output:
        out = out[:CONFIG.max_tool_output] + "\n...[truncated]"
    if len(err) > 8000:
        err = err[:8000] + "\n...[truncated]"
    return {"stdout": out, "stderr": err, "exit_code": proc.returncode}


# --------------------------------------------------------------------------- #
# relay + introspection
# --------------------------------------------------------------------------- #
def t_claude_send(a):
    text = a.get("prompt") or a.get("text") or ""
    if not text:
        return {"error": "prompt is required"}
    if _STORE is None:
        return {"error": "relay store is not initialised"}
    return _STORE.push("inbox", text, "minis", "claude")


def t_claude_poll(a):
    if _STORE is None:
        return {"error": "relay store is not initialised"}
    return _STORE.read("outbox", a.get("since", 0), a.get("wait", 0))


def t_claude_inbox(a):
    if _STORE is None:
        return {"error": "relay store is not initialised"}
    return _STORE.read("inbox", a.get("since", 0), 0)


# --------------------------------------------------------------------------- #
# paired PC agent (Windows / macOS / Linux) - command channel
# --------------------------------------------------------------------------- #
def t_pc_exec(a):
    """Run a command on the paired PC and wait for the result."""
    cmd = (a.get("command") or "").strip()
    if not cmd:
        return {"error": "command is required"}
    timeout = int(a.get("timeout") or 60)
    shell = a.get("shell") or "auto"
    if _STORE is None:
        return {"error": "relay store is not initialised"}
    meta = {"kind": "exec", "command": cmd, "shell": shell, "timeout": timeout}
    item = _STORE.push("pc", cmd, "minis", "pc", meta)
    cmd_id = item.get("id")
    deadline = time.time() + timeout + 15
    while time.time() < deadline:
        res = _STORE.read("pcreply", 0, 0)
        for it in res.get("items", []):
            m = it.get("meta") or {}
            if isinstance(m, str):
                try:
                    import json as _json
                    m = _json.loads(m)
                except Exception:
                    m = {}
            if m.get("reply_to") == cmd_id:
                return {"id": cmd_id, "ok": True, "stdout": m.get("stdout", ""),
                        "stderr": m.get("stderr", ""), "exit_code": m.get("exit_code"),
                        "ms": m.get("ms"), "host": m.get("host"),
                        "platform": m.get("platform")}
        time.sleep(0.4)
    return {"id": cmd_id, "ok": False,
            "error": "timeout: the PC agent did not answer within %ds" % (timeout + 15),
            "hint": "run pc_status to check whether the agent is alive"}


def t_pc_status(a):
    """Heartbeat and pending-command state of the paired PC agent."""
    if _STORE is None:
        return {"error": "relay store is not initialised"}
    hb = _STORE.read("pchb", 0, 0)
    items = hb.get("items", [])
    last = items[-1] if items else None
    age = None
    if last:
        try:
            age = round(time.time() - float(last.get("ts", 0)), 1)
        except Exception:
            age = None
    pending = _STORE.read("pc", 0, 0)
    return {"alive": bool(last) and (age is None or age < 90),
            "last_heartbeat": last, "heartbeat_age_sec": age,
            "pending_commands": len(pending.get("items", []))}


def t_pc_send(a):
    """Send a free-form message to the paired PC agent."""
    text = a.get("prompt") or a.get("text") or ""
    if not text:
        return {"error": "prompt is required"}
    if _STORE is None:
        return {"error": "relay store is not initialised"}
    return _STORE.push("pc", text, "minis", "pc", {"kind": "message"})


def t_pc_poll(a):
    """Read replies from the paired PC agent."""
    if _STORE is None:
        return {"error": "relay store is not initialised"}
    return _STORE.read("pcreply", a.get("since", 0), a.get("wait", 0))


def t_bridge_info(a):
    import socket
    ip = "?"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass
    from . import __version__
    return {"server": CONFIG.server_name, "version": __version__,
            "role": os.environ.get("MINIS_BRIDGE_ROLE", "private"),
            "mcp_url": "http://%s:%d/mcp" % (ip, CONFIG.port),
            "native_tools": sorted(apple.available_tools()),
            "tool_count": len(TOOL_SPECS),
            "uptime_sec": round(time.time() - _START, 1)}


# --------------------------------------------------------------------------- #
# catalogue
# --------------------------------------------------------------------------- #
def _native_names():
    return ", ".join(sorted(apple.available_tools()))


TOOL_SPECS = [
    ("minis_sessions_list", "List recent Minis chat sessions.",
     _schema({"limit": _s("integer", "max sessions (default 30, max 100)")}), t_sessions_list),
    ("minis_sessions_search", "Full text search across every Minis conversation (AND logic).",
     _schema({"keywords": _s("string", "space separated keywords"),
              "limit": _s("integer", "max results")}, ["keywords"]), t_sessions_search),
    ("minis_session_messages", "Read the messages of one Minis session.",
     _schema({"session_id": _s("string", "session UUID"),
              "offset": _s("integer", "skip N messages"),
              "full": _s("boolean", "full message text")}, ["session_id"]), t_session_messages),
    ("minis_session_send",
     "Send a prompt to a Minis session (new session when session_id is omitted). "
     "Returns immediately; poll minis_session_messages for the reply.",
     _schema({"prompt": _s("string", "the message to send"),
              "session_id": _s("string", "existing session id"),
              "model": _s("string", "model entry id override")}, ["prompt"]), t_session_send),
    ("minis_session_status", "Runtime status of a Minis session (running, termination, last tool).",
     _schema({"session_id": _s("string", "session UUID")}, ["session_id"]), t_session_status),

    ("iphone",
     "Call any iOS native capability. tool: " + _native_names() +
     ". args: raw CLI arguments. Use iphone_help first if unsure.",
     _schema({"tool": _s("string", "native tool name without the apple- prefix"),
              "args": {"type": "array", "items": {"type": "string"},
                       "description": "raw CLI arguments"}}), t_iphone),
    ("iphone_help", "Return the full --help of a native tool.",
     _schema({"tool": _s("string", "native tool name")}, ["tool"]), t_iphone_help),
    ("iphone_device", "iPhone hardware/OS status: model, iOS, CPU, memory, battery, storage.",
     _schema({}), t_device),
    ("iphone_clipboard", "Read, write, clear or inspect the iOS clipboard.",
     _schema({"action": _s("string", "get | set | clear | status"),
              "text": _s("string", "text to write for action=set")}), t_clipboard),
    ("iphone_homekit",
     "Control HomeKit: list, search, read, set a characteristic, list/trigger scenes.",
     _schema({"action": _s("string", "list | search | get | set | scenes | trigger"),
              "name": _s("string", "accessory or scene name"),
              "query": _s("string", "search query"), "type": _s("string", "type filter"),
              "room": _s("string", "room filter"),
              "characteristic": _s("string", "characteristic for action=set"),
              "value": _s("string", "value to write"),
              "args": {"type": "array", "items": {"type": "string"},
                       "description": "raw extra CLI args"}}), t_homekit),
    ("iphone_alarm", "List, create or cancel iOS alarms and timers (AlarmKit).",
     _schema({"action": _s("string", "list | set | timer | cancel"),
              "time": _s("string", "HH:MM or ISO"), "duration": _s("string", "e.g. 10m"),
              "label": _s("string", "label"), "repeat": _s("string", "none | daily | weekdays"),
              "id": _s("string", "alarm id")}), t_alarm),
    ("iphone_reminders", "Read and manage iOS Reminders.",
     _schema({"action": _s("string", "list | lists | create | complete | delete | read | update"),
              "list": _s("string", "list name"), "list_id": _s("string", "list id"),
              "title": _s("string", "title"), "notes": _s("string", "notes"),
              "due": _s("string", "due date"), "id": _s("string", "reminder id"),
              "limit": _s("integer", "max results"),
              "incomplete": _s("boolean", "only incomplete")}), t_reminders),
    ("iphone_calendar", "Query and manage iOS Calendar events.",
     _schema({"action": _s("string", "list | calendars | create | delete"),
              "calendar": _s("string", "calendar name"), "start": _s("string", "ISO start"),
              "end": _s("string", "ISO end"), "days": _s("integer", "last N days"),
              "today": _s("boolean", "today only"), "limit": _s("integer", "max results"),
              "title": _s("string", "title"), "notes": _s("string", "notes"),
              "location": _s("string", "location"), "id": _s("string", "event id")}), t_calendar),
    ("iphone_healthkit",
     "Query Apple Health metrics (kebab-case names, e.g. heart-rate). "
     "Call the iphone tool with tool=healthkit args=[\"types\"] to list every type.",
     _schema({"types": {"type": "array", "items": {"type": "string"},
                        "description": "e.g. [\"heart-rate\",\"active-energy\"]"},
              "days": _s("integer", "lookback days (default 7)")}), t_healthkit),
    ("iphone_location", "Current GPS position, reverse and forward geocoding.",
     _schema({"action": _s("string", "current | geocode | forward"),
              "accuracy": _s("string", "best | near | km"),
              "lat": _s("string", "latitude"), "lng": _s("string", "longitude"),
              "address": _s("string", "address")}), t_location),
    ("iphone_weather", "Current weather, hourly/daily forecast, precipitation and alerts.",
     _schema({"action": _s("string", "current | hourly | minute | daily | alerts | report")}),
     t_weather),
    ("iphone_speak", "Speak text out loud on the iPhone.",
     _schema({"text": _s("string", "text"), "voice": _s("string", "BCP47 tag"),
              "rate": _s("number", "0.0-1.0"), "pitch": _s("number", "0.5-2.0")}), t_speak),
    ("iphone_open_url", "Open a URL, app or Settings page on the iPhone.",
     _schema({"url": _s("string", "url or scheme")}, ["url"]), t_open),
    ("iphone_maps", "Search places, directions and travel time.",
     _schema({"action": _s("string", "search | route | eta"),
              "query": _s("string", "place query"), "lat": _s("string", "lat"),
              "lon": _s("string", "lon"), "radius": _s("string", "meters"),
              "limit": _s("integer", "max results"), "from": _s("string", "origin"),
              "to": _s("string", "destination"),
              "mode": _s("string", "driving | walking | transit")}), t_maps),
    ("iphone_vision", "Image analysis: ocr, barcode, classify, detect, faces, analyze, similarity.",
     _schema({"action": _s("string", "ocr | barcode | classify | detect | faces | analyze | similarity | overlap"),
              "path": _s("string", "image path on the phone"),
              "lang": _s("string", "ocr language"), "level": _s("string", "fast | accurate")}),
     t_vision),
    ("iphone_photos", "Photo library: recent assets, albums, stats, import/export.",
     _schema({"action": _s("string", "list | albums | album | stats | export | import | near"),
              "limit": _s("integer", "max results"), "album": _s("string", "album name"),
              "name": _s("string", "asset name"), "id": _s("string", "asset id"),
              "path": _s("string", "file path"),
              "args": {"type": "array", "items": {"type": "string"},
                       "description": "raw extra CLI args"}}), t_photos),
    ("iphone_notification", "Local notifications: list, settings, schedule, cancel.",
     _schema({"action": _s("string", "pending | delivered | settings | schedule | cancel"),
              "title": _s("string", "title"), "body": _s("string", "body"),
              "after": _s("number", "seconds from now"), "at": _s("string", "ISO time"),
              "id": _s("string", "notification id")}), t_notification),
    ("iphone_media", "Music playback and media library.",
     _schema({"action": _s("string", "now-playing | play | pause | toggle | next | prev | volume | search"),
              "query": _s("string", "search query"), "type": _s("string", "song | album | artist"),
              "volume": _s("number", "0.0-1.0"), "limit": _s("integer", "max results")}), t_media),
    ("iphone_nfc", "NFC: status, scan, write, raw tag read/write.",
     _schema({"action": _s("string", "status | scan | write | tag-info | tag-read | tag-write"),
              "args": {"type": "array", "items": {"type": "string"},
                       "description": "raw extra CLI args"}}), t_nfc),
    ("iphone_shell", "Run a shell command in the phone's environment (device/LAN clients only).",
     _schema({"command": _s("string", "shell command"),
              "timeout": _s("integer", "seconds (default 60)")}, ["command"]), t_shell),

    ("claude_send", "Send a message to the paired Claude agent (read back with claude_poll).",
     _schema({"prompt": _s("string", "message for the Claude agent")}, ["prompt"]), t_claude_send),
    ("claude_poll", "Read replies from the paired Claude agent. wait=N blocks up to N seconds.",
     _schema({"since": _s("number", "cursor from a previous call"),
              "wait": _s("number", "seconds to wait")}), t_claude_poll),
    ("claude_inbox", "Inspect the relay queue in both directions.",
     _schema({"since": _s("number", "cursor")}), t_claude_inbox),

    ("pc_exec", "Run a shell/PowerShell command on the paired PC (Windows/macOS/Linux) and wait for the result.",
     _schema({"command": _s("string", "command line to run on the PC"),
              "timeout": _s("integer", "seconds to wait (default 60)"),
              "shell": _s("string", "auto | cmd | powershell | sh")}, ["command"]), t_pc_exec),
    ("pc_status", "Is the paired PC agent alive? Last heartbeat and pending commands.",
     _schema({}), t_pc_status),
    ("pc_send", "Send a free-form message to the paired PC agent.",
     _schema({"prompt": _s("string", "message")}, ["prompt"]), t_pc_send),
    ("pc_poll", "Read replies from the paired PC agent. wait=N blocks up to N seconds.",
     _schema({"since": _s("number", "cursor"),
              "wait": _s("number", "seconds to wait")}), t_pc_poll),
    ("bridge_info", "Connection details: role, MCP URL, tool count, native tools.",
     _schema({}), t_bridge_info),
]

TOOLS = {spec[0]: spec for spec in TOOL_SPECS}


def public_definitions():
    return [{"name": n, "description": d, "inputSchema": s} for n, d, s, _ in TOOL_SPECS]
