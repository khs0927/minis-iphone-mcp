"""Wrapper around the iOS native framework CLI tools (``apple-*``).

The Minis app ships a family of ``apple-*`` commands that talk to the real
iOS frameworks (HealthKit, HomeKit, Calendar, ...).  This module gives them a
typed, discoverable surface so the MCP tools stay declarative.
"""
import json
import os
import subprocess

BIN_DIR = os.environ.get("MINIS_APPLE_BIN", "/usr/local/bin")

NATIVE_TOOLS = {
    "alarm": ("iOS alarms and timers (AlarmKit)", "apple-alarm list"),
    "bluetooth": ("Bluetooth state and devices", "apple-bluetooth status"),
    "calendar": ("Calendar events and reminders", "apple-calendar list --today"),
    "clipboard": ("Read/write the iOS clipboard", "apple-clipboard get"),
    "device": ("Device model, OS, battery, storage", "apple-device battery"),
    "healthkit": ("Apple Health metrics", 'apple-healthkit batch --types "heart-rate" --days 7'),
    "homekit": ("HomeKit accessories and scenes", "apple-homekit list"),
    "location": ("GPS position and geocoding", "apple-location current"),
    "maps": ("Place search, directions, ETA", 'apple-maps search --query "coffee"'),
    "media": ("Music playback and library", "apple-media now-playing"),
    "nfc": ("NFC tag read/write", "apple-nfc status"),
    "nlp": ("Natural language processing helpers", "apple-nlp --help"),
    "notification": ("Local notifications", "apple-notification pending"),
    "open": ("Open a URL, app or Settings page", 'apple-open "https://example.com"'),
    "photos": ("Photo library", "apple-photos list --limit 5"),
    "player": ("Media player control", "apple-player status"),
    "reminders": ("Reminders lists and items", "apple-reminders list"),
    "speak": ("Text to speech", 'apple-speak speak --text "hi"'),
    "speech": ("Speech recognition", "apple-speech --help"),
    "vision": ("Image OCR/barcode/classification", "apple-vision ocr <path>"),
    "weather": ("WeatherKit forecasts and alerts", "apple-weather current"),
}


class NativeError(Exception):
    pass


def available_tools():
    out = {}
    for name, (desc, example) in NATIVE_TOOLS.items():
        if os.path.exists(os.path.join(BIN_DIR, "apple-" + name)):
            out[name] = (desc, example)
    return out


def run(tool, *args, timeout=90, max_output=60000):
    """Run an ``apple-*`` tool and return parsed JSON (or a raw wrapper)."""
    if tool not in NATIVE_TOOLS:
        raise NativeError("unknown native tool: %s (available: %s)"
                          % (tool, ", ".join(sorted(available_tools()))))
    cmd = [os.path.join(BIN_DIR, "apple-" + tool)] + [str(a) for a in args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "command": " ".join(cmd)}
    except OSError as e:
        return {"error": str(e), "command": " ".join(cmd)}
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    try:
        data = json.loads(out) if out else None
    except ValueError:
        data = None
    if data is not None:
        if isinstance(data, dict):
            if err and proc.returncode != 0:
                data.setdefault("_stderr", err)
            data.setdefault("_exit_code", proc.returncode)
        return data
    text = out or err
    if len(text) > max_output:
        text = text[:max_output] + "\n...[truncated]"
    return {"stdout": text, "exit_code": proc.returncode, "command": " ".join(cmd)}


def help_text(tool, timeout=20):
    path = os.path.join(BIN_DIR, "apple-" + tool)
    if not os.path.exists(path):
        raise NativeError("no such native tool: %s" % tool)
    try:
        proc = subprocess.run([path, "--help"], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    return {"tool": tool, "help": (proc.stdout or "").strip()}
